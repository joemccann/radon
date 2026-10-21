"""Bounded SQLite WAL raw capture and atomically published research state."""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from utils.atomic_io import atomic_save, verified_load
from .config import Config, DEFAULT_DIRECTORY
from .model import Materializer, iso

MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024


def missing_snapshot(status: str = "missing") -> dict:
    return {"schema_version": 1, "source": "unusual_whales", "status": status, "research_only": True, "updated_at": None, "connection": {"connected": False, "gaps": 0, "reconnects": 0}, "channels": {}, "latest": {}, "metrics": {}}


def read_snapshot(path: Path | str | None = None, now: float | None = None) -> dict:
    """Read-only API seam. Never creates directories or a SQLite database."""
    target = Path(path) if path is not None else DEFAULT_DIRECTORY / "snapshot.json"
    now = time.time() if now is None else now
    try:
        if target.stat().st_size > MAX_SNAPSHOT_BYTES:
            return missing_snapshot("unavailable")
        result = verified_load(str(target))
        if not isinstance(result, dict) or result.get("schema_version") != 1 or result.get("source") != "unusual_whales" or not isinstance(result.get("channels"), dict) or not isinstance(result.get("latest"), dict):
            return missing_snapshot("unavailable")
        updated = datetime.fromisoformat(result["updated_at"].replace("Z", "+00:00")).timestamp()
        if not math.isfinite(updated) or updated > now + 60:
            return missing_snapshot("unavailable")
        result["age_seconds"] = max(0, now - updated)
        if result["status"] not in {"disabled", "missing", "unavailable"} and now - updated > 15:
            result["status"] = "stale"
            result["connection"]["connected"] = False
        for channel, meta in result["channels"].items():
            meta["quiet_is_not_failure"] = channel.partition(":")[0] in {"flow-alerts", "off_lit_trades", "news", "sec", "futures_blocks"}
            # Publisher heartbeat and source-data age are separate measures.
            stamp = meta.get("last_received_at")
            meta["age_seconds"] = max(0, now - datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()) if stamp else None
        return result
    except FileNotFoundError:
        return missing_snapshot()
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return missing_snapshot("unavailable")


class Store:
    def __init__(self, config: Config):
        self.config = config
        config.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.database_path = config.directory / "events.sqlite3"
        self.snapshot_path = config.directory / "snapshot.json"
        self.db = sqlite3.connect(self.database_path, timeout=2)
        self.db.execute("PRAGMA auto_vacuum=INCREMENTAL")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA journal_size_limit=16777216")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                ingest_id INTEGER PRIMARY KEY AUTOINCREMENT,
                received_at REAL NOT NULL,
                channel TEXT NOT NULL,
                payload TEXT NOT NULL,
                identity TEXT UNIQUE
            );
            CREATE TABLE IF NOT EXISTS checkpoint (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
        """)
        row = self.db.execute("SELECT payload FROM checkpoint WHERE id=1").fetchone()
        try:
            initial = json.loads(row[0]) if row else None
        except (TypeError, ValueError):
            initial = None
        self.model = Materializer(config.channels, initial)
        if initial:
            # A process restart has an unknowable upstream gap even if every
            # local transaction is intact. No provider replay exists.
            self.model.gap(time.time(), "collector-restarted")
        self.last_prune = 0.0

    def close(self) -> None:
        self.db.close()

    def _storage_admissible(self) -> bool:
        total = sum(p.stat().st_size for p in self.config.directory.glob("events.sqlite3*") if p.is_file())
        return total < self.config.max_disk_bytes and shutil.disk_usage(self.config.directory).free >= self.config.min_free_bytes

    def flush(self, frames: list[tuple[str, dict, float]], now: float, queue_depth: int = 0) -> dict:
        if frames and not self._storage_admissible():
            self.model.state["metrics"]["dropped"] += len(frames)
            self.model.gap(now, "storage-watermark")
            frames = []
        # Restore both materialized values and incomplete multipart buffers on
        # rollback. A failed transaction must not double-add Greek partials.
        import copy
        before = copy.deepcopy(self.model)
        try:
            with self.db:
                for channel, payload, received_at in frames:
                    if channel == "__control__":
                        event = payload.get("event")
                        if event == "connect":
                            self.model.connect(received_at)
                        elif event == "disconnect":
                            self.model.disconnect(received_at)
                        elif event == "invalid":
                            self.model.state["metrics"]["invalid"] += 1
                        elif event == "reconciliation_unavailable":
                            meta = self.model.state["channels"].get(payload.get("channel"))
                            if meta is not None:
                                meta["reconciliation_status"] = "unavailable; retained observations unconfirmed"
                            self.model.state["metrics"]["reconciliation_failures"] = self.model.state["metrics"].get("reconciliation_failures", 0) + 1
                        continue
                    encoded = json.dumps(payload, separators=(",", ":"), allow_nan=False)
                    source_id = payload.get("id") or payload.get("trade_id") or payload.get("accession_number")
                    family = channel.partition(":")[0]
                    identity = None
                    if source_id is not None and family in {"flow-alerts", "off_lit_trades", "news", "sec"}:
                        identity = hashlib.sha256((channel + str(source_id) + encoded).encode()).hexdigest()
                    cursor = self.db.execute("INSERT OR IGNORE INTO events (received_at,channel,payload,identity) VALUES (?,?,?,?)", (received_at, channel, encoded, identity))
                    if cursor.rowcount == 0:
                        continue
                    if channel == "__reconcile__":
                        self.model.reconcile_greek(payload.get("channel"), payload.get("rows") or [], received_at, cursor.lastrowid)
                    else:
                        self.model.apply(channel, payload, received_at, cursor.lastrowid)
                    self.model.state["metrics"]["persisted"] += 1
                    self.model.state["metrics"]["persisted_bytes"] = self.model.state["metrics"].get("persisted_bytes", 0) + len(encoded.encode())
                self.model.state["metrics"]["queue_depth"] = queue_depth
                snapshot = self.model.snapshot(now)
                while len(json.dumps(snapshot, separators=(",", ":")).encode()) > MAX_SNAPSHOT_BYTES:
                    candidates = [(record.get("ingest_id", 0), channel, key) for channel, values in self.model.state["latest"].items() for key, record in values.items()]
                    if not candidates:
                        raise ValueError("Research metadata exceeds snapshot bound")
                    _, channel, key = min(candidates)
                    del self.model.state["latest"][channel][key]
                    self.model.state["channels"][channel]["retained_history_limited"] = True
                    self.model.gap(now, "materialization-size-bound")
                    snapshot = self.model.snapshot(now)
                self.db.execute("INSERT OR REPLACE INTO checkpoint (id,payload) VALUES (1,?)", (json.dumps(snapshot, separators=(",", ":"), allow_nan=False),))
                if now - self.last_prune >= 60:
                    self.db.execute("DELETE FROM events WHERE received_at < ?", (now - self.config.retention_seconds,))
                    self.db.execute("DELETE FROM events WHERE ingest_id NOT IN (SELECT ingest_id FROM events ORDER BY ingest_id DESC LIMIT ?)", (self.config.max_raw_rows,))
            if now - self.last_prune >= 60:
                self.db.execute("PRAGMA wal_checkpoint(PASSIVE)")
                self.db.execute("PRAGMA incremental_vacuum(1000)")
                self.last_prune = now
        except Exception:
            self.model = before
            self.model.gap(now, "storage-write-failed")
            raise
        atomic_save(str(self.snapshot_path), snapshot)
        return snapshot

    def publish_status(self, status: str, now: float) -> dict:
        self.model.state["status"] = status
        return self.flush([], now)
