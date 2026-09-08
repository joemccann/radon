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
)


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
        from scripts.db.hrana_http import hrana_execute

        return hrana_execute(sql, args)

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
        self._execute(
            "INSERT INTO ai_cycle_source_status(checked_at,payload) VALUES (?,?)",
            (status["checked_at"], canonical(status)),
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
        from datetime import datetime, timedelta

        at = utc(as_of)
        cutoff = utc(datetime.fromisoformat(at.replace("Z", "+00:00")) - timedelta(days=1500))
        daily_cutoff = utc(datetime.fromisoformat(at.replace("Z", "+00:00")) - timedelta(days=400))
        rows, cursor, deadline = [], 0, time.monotonic() + 12
        while True:
            if time.monotonic() > deadline or len(rows) >= 100000:
                raise RuntimeError("AI snapshot history exceeds bounded read budget")
            try:
                page = self._query(
                    "SELECT id,payload FROM ai_cycle_observations AS o WHERE id > ? AND available_at <= ? AND period_end >= ? AND (period_end >= ? OR json_extract(payload,'$.indicator_id') IN ('F1','F2','H1','H2','P2')) AND NOT EXISTS (SELECT 1 FROM ai_cycle_observations AS newer WHERE newer.identity=o.identity AND newer.available_at <= ? AND (newer.available_at>o.available_at OR (newer.available_at=o.available_at AND (COALESCE(json_extract(newer.payload,'$.published_at'),'') || COALESCE(json_extract(newer.payload,'$.metadata.accession'),'') || printf('%020d',newer.id)) > (COALESCE(json_extract(o.payload,'$.published_at'),'') || COALESCE(json_extract(o.payload,'$.metadata.accession'),'') || printf('%020d',o.id))))) ORDER BY id LIMIT 200",
                    (cursor, at, cutoff, daily_cutoff, at),
                )
            except Exception as exc:
                if "no such table: ai_cycle_observations" in str(exc):
                    return []
                raise
            rows.extend(json.loads(row[1]) for row in page)
            if len(page) < 200:
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
