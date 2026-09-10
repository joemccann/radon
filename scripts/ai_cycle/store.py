"""Append-only vintages with isolated SQLite or bounded production Turso I/O."""

from __future__ import annotations

import json
import os
import sqlite3
import time

from .model import available_at, canonical, digest, response_group, utc, validate_observation

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS ai_cycle_observations (id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT NOT NULL UNIQUE, available_at TEXT NOT NULL, period_end TEXT NOT NULL, identity TEXT NOT NULL, payload TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS ai_cycle_observations_available ON ai_cycle_observations(available_at)",
    "CREATE INDEX IF NOT EXISTS ai_cycle_observations_identity ON ai_cycle_observations(identity,available_at,period_end)",
    "CREATE TABLE IF NOT EXISTS ai_cycle_source_status (id INTEGER PRIMARY KEY AUTOINCREMENT, checked_at TEXT NOT NULL, payload TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS ai_cycle_raw (hash TEXT PRIMARY KEY, payload TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS ai_cycle_api_snapshot (id INTEGER PRIMARY KEY CHECK (id = 1), generated_at TEXT NOT NULL, payload TEXT NOT NULL)",
)

_TRANSIENT_HRANA_MARKERS = (
    "TimeoutError:",
    "timed out",
    "URLError:",
    "ConnectionResetError:",
    "RemoteDisconnected:",
    "HTTP Error 429",
    "HTTP Error 5",
)
_CLOUD_WRITE_RETRY_DELAYS = (0.25, 1.0)
_SNAPSHOT_PAGE_SIZE = 500
_SNAPSHOT_PERSIST_DEADLINE_SECONDS = 900
_SNAPSHOT_MAX_ROWS = 500_000
_SNAPSHOT_READ_DEADLINE_SECONDS = _SNAPSHOT_PERSIST_DEADLINE_SECONDS


class ObservationStore:
    def __init__(self, path=None):
        path = path if path is not None else os.environ.get("RADON_AI_CYCLE_DB_PATH")
        self.connection = sqlite3.connect(str(path)) if path is not None else None
        self._initialized = False

    def _execute(self, sql, args=()):
        if self.connection is not None:
            result = self.connection.execute(sql, args)
            self.connection.commit()
            return result
        from scripts.db.hrana_http import HranaHttpError, hrana_execute

        for attempt in range(len(_CLOUD_WRITE_RETRY_DELAYS) + 1):
            try:
                return hrana_execute(sql, args)
            except HranaHttpError as exc:
                transient = any(marker in str(exc) for marker in _TRANSIENT_HRANA_MARKERS)
                if not transient or attempt == len(_CLOUD_WRITE_RETRY_DELAYS):
                    raise
                time.sleep(_CLOUD_WRITE_RETRY_DELAYS[attempt])

    def _query(self, sql, args=()):
        if self.connection is not None:
            return self.connection.execute(sql, args).fetchall()
        from scripts.db.hrana_http import hrana_query

        return hrana_query(sql, args)

    def initialize(self):
        if not self._initialized:
            for sql in SCHEMA:
                self._execute(sql)
            self._initialized = True

    def append_observations(self, rows):
        validated = [validate_observation(row) for row in rows]
        response_members = {}
        for row in validated:
            group = response_group(row)
            if group is not None:
                key = (group, row["fetched_at"], row["raw_hash"])
                response_members.setdefault(key, set()).add((row["series_id"], row["period_start"], row["period_end"]))
        for row in validated:
            group = response_group(row)
            if group is not None:
                expected = len(response_members[(group, row["fetched_at"], row["raw_hash"])])
                row["metadata"] = {**row["metadata"], "response_group": group, "response_row_count": expected}
        self.initialize()
        for offset in range(0, len(validated), 100):
            batch = validated[offset : offset + 100]
            args = []
            for row in batch:
                payload = canonical(row)
                identity = canonical(
                    [
                        row[key]
                        for key in (
                            "indicator_id",
                            "series_id",
                            "source_id",
                            "period_start",
                            "period_end",
                            "methodology_version",
                            "cohort_version",
                        )
                    ]
                )
                args.extend((digest(payload.encode()), available_at(row), row["period_end"], identity, payload))
            self._execute(
                "INSERT OR IGNORE INTO ai_cycle_observations (fingerprint,available_at,period_end,identity,payload) VALUES "
                + ",".join("(?,?,?,?,?)" for _ in batch),
                args,
            )
        return len(validated)

    def archive_raw(self, payload: bytes):
        import base64
        import zlib

        if len(payload) > 20 * 1024 * 1024:
            raise ValueError("Raw response exceeds archive bound")
        self.initialize()
        key = digest(payload)
        self._execute(
            "INSERT OR IGNORE INTO ai_cycle_raw(hash,payload) VALUES (?,?)",
            (key, base64.b64encode(zlib.compress(payload)).decode()),
        )
        return key

    def import_raw_archive(self, archive):
        from pathlib import Path

        root = Path(archive)
        imported = 0
        if not root.is_dir():
            return 0
        for path in root.iterdir():
            if path.suffix != ".json" or path.name.startswith("."):
                continue
            raw = path.read_bytes()
            if digest(raw) != path.stem:
                continue
            self.archive_raw(raw)
            imported += 1
        return imported

    def write_api_snapshot(self, snapshot):
        payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False)
        self.initialize()
        self._execute(
            "INSERT INTO ai_cycle_api_snapshot(id,generated_at,payload) VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET generated_at=excluded.generated_at, payload=excluded.payload",
            (snapshot.get("generated_at") or utc(), payload),
        )

    def read_api_snapshot(self):
        try:
            rows = self._query("SELECT payload FROM ai_cycle_api_snapshot WHERE id=1")
        except Exception as exc:
            if "no such table: ai_cycle_api_snapshot" in str(exc):
                return None
            raise
        if not rows:
            return None
        payload = json.loads(rows[0][0])
        if payload.get("version") != 1:
            raise RuntimeError("AI API snapshot is incompatible")
        return payload

    def record_source_status(self, status):
        from .registry import SOURCES

        status = dict(status)
        if status.get("source_id") not in SOURCES or status.get("status") not in (
            "available",
            "unavailable",
            "error",
            "restricted",
            "experimental",
        ):
            raise ValueError("Invalid source status")
        status["checked_at"] = utc(status["checked_at"])
        # Only collector-defined reasons belong here, never response bodies or exception URLs.
        status["reason"] = str(status.get("reason", ""))[:500]
        self.initialize()
        payload = canonical(status)
        self._execute(
            "INSERT INTO ai_cycle_source_status(checked_at,payload) SELECT ?,? WHERE NOT EXISTS (SELECT 1 FROM ai_cycle_source_status WHERE checked_at=? AND payload=?)",
            (status["checked_at"], payload, status["checked_at"], payload),
        )

    def _read(self, table, time_column, as_of):
        rows, cursor = [], 0
        deadline = time.monotonic() + 12
        while True:
            if time.monotonic() > deadline or len(rows) >= 100_000:
                raise RuntimeError("AI observation read exceeds bounded snapshot budget")
            try:
                page = self._query(
                    f"SELECT id,payload FROM {table} WHERE id > ? AND {time_column} <= ? ORDER BY id LIMIT 200",
                    (cursor, utc(as_of)),
                )
            except Exception as exc:
                if "no such table: " + table in str(exc):
                    return []
                raise
            rows.extend(json.loads(row[1]) for row in page)
            if len(page) < 200:
                return rows
            cursor = int(page[-1][0])

    def read_observations(self, as_of=None):
        return self._read("ai_cycle_observations", "available_at", as_of)

    def read_snapshot_observations(self, as_of=None):
        at = utc(as_of)
        # Publisher floors are explicit: SEC XBRL begins in 2009, while the
        # oldest continuous operational series (EIA/NOAA) begins in July 2018.
        # Keep these stable instead of silently moving the chart window forward.
        cutoff = utc("2009-01-01T00:00:00Z")
        daily_cutoff = utc("2018-07-01T00:00:00Z")
        rows, cursor = [], 0
        deadline = time.monotonic() + _SNAPSHOT_READ_DEADLINE_SECONDS
        while True:
            if time.monotonic() > deadline or len(rows) >= _SNAPSHOT_MAX_ROWS:
                raise RuntimeError("AI snapshot history exceeds bounded read budget")
            try:
                page = self._query(
                    f"SELECT id,payload FROM ai_cycle_observations WHERE id > ? AND available_at <= ? AND period_end >= ? AND (period_end >= ? OR json_extract(payload,'$.indicator_id') IN ('F1','F2','H1','H2','P2')) ORDER BY id LIMIT {_SNAPSHOT_PAGE_SIZE}",
                    (cursor, at, cutoff, daily_cutoff),
                )
            except Exception as exc:
                if "no such table: ai_cycle_observations" in str(exc):
                    return []
                raise
            rows.extend(json.loads(row[1]) for row in page)
            if len(page) < _SNAPSHOT_PAGE_SIZE:
                return rows
            cursor = int(page[-1][0])

    def read_source_statuses(self, as_of=None):
        try:
            rows = self._query(
                "SELECT payload FROM (SELECT payload, ROW_NUMBER() OVER (PARTITION BY json_extract(payload, '$.source_id') ORDER BY checked_at DESC, id DESC) AS rank FROM ai_cycle_source_status WHERE checked_at <= ?) WHERE rank=1",
                (utc(as_of),),
            )
        except Exception as exc:
            if "no such table: ai_cycle_source_status" in str(exc):
                return []
            raise
        return [json.loads(row[0]) for row in rows]

    def close(self):
        if self.connection is not None:
            self.connection.close()
