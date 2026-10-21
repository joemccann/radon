"""Explicit, bounded offline reconstruction from retained local ingest IDs.

No upstream replay is implied. The collector must be stopped: its existing
exclusive owner lock excludes an offline reader. SQLite and its WAL are copied
under a shared lock, so opening a read transaction cannot create/change WAL
sidecars in the production directory. Output is returned only, never published.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import math
from pathlib import Path
import shutil
import sqlite3
import tempfile

from .config import DEFAULT_DIRECTORY
from .model import Materializer

MAX_EVENTS = 100_000
MAX_COPY_BYTES = 1024 * 1024 * 1024 + 32 * 1024 * 1024
MAX_PAYLOAD_BYTES = 1024 * 1024
MAX_REPLAY_BYTES = 32 * 1024 * 1024


@contextmanager
def _offline_copy(directory: Path):
    database = directory / "events.sqlite3"
    if not database.is_file() or database.is_symlink():
        raise ValueError("Retained research journal unavailable")
    lock = None
    lock_path = directory / "collector.lock"
    try:
        if not lock_path.exists():
            raise ValueError("Offline replay requires the retained collector owner lock")
        if lock_path.exists():
            if lock_path.is_symlink():
                raise ValueError("Offline replay owner lock is invalid")
            lock = lock_path.open("rb")
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Stop the research collector before offline replay") from None
        files = [database]
        for suffix in ("-wal", "-shm"):
            sidecar = directory / ("events.sqlite3" + suffix)
            if sidecar.exists():
                if sidecar.is_symlink() or not sidecar.is_file():
                    raise ValueError("Offline replay sidecar is invalid")
                files.append(sidecar)
        if sum(file.stat().st_size for file in files) > MAX_COPY_BYTES:
            raise ValueError("Retained journal exceeds offline replay copy bound")
        with tempfile.TemporaryDirectory(prefix="radon-uw-replay-") as temporary:
            target = Path(temporary)
            for file in files:
                shutil.copyfile(file, target / file.name)
            yield target / database.name
    finally:
        if lock is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()


def replay_retained(directory: Path | str | None = None, *, from_ingest_id: int = 1,
                    to_ingest_id: int | None = None, max_events: int = 10_000) -> dict:
    """Return a research-only projection of an explicitly retained ID range.

    A retained range is not a historical baseline: earlier pruned observations,
    upstream delivery gaps and legacy unjournaled control transitions cannot be
    reconstructed. Additive buckets are partial until a retained REST confirmation.
    """
    for value, label in ((from_ingest_id, "start"), (max_events, "event bound")):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("Replay " + label + " must be a positive integer")
    if max_events > MAX_EVENTS:
        raise ValueError("Replay event bound exceeds retained journal limit")
    if to_ingest_id is not None and (isinstance(to_ingest_id, bool) or not isinstance(to_ingest_id, int) or to_ingest_id < from_ingest_id):
        raise ValueError("Replay end must not precede its start")
    directory = Path(directory) if directory is not None else DEFAULT_DIRECTORY
    try:
        with _offline_copy(directory) as database:
            conn = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)
            try:
                conn.execute("PRAGMA query_only=ON")
                conn.execute("BEGIN")
                low, high = conn.execute("SELECT min(ingest_id),max(ingest_id) FROM events").fetchone()
                limit = to_ingest_id if to_ingest_id is not None else high
                rows = []
                byte_limited = False
                selected_bytes = 0
                if high is not None:
                    cursor = conn.execute(
                        # Reject giant/corrupt values inside SQLite before the driver
                        # allocates Python strings. UTF-8 encoding below is then
                        # bounded by at most four bytes per accepted character.
                        "SELECT ingest_id,received_at,channel,CASE WHEN typeof(payload)='text' AND length(CAST(payload AS BLOB))<=? THEN payload ELSE NULL END FROM events WHERE ingest_id>=? AND ingest_id<=? ORDER BY ingest_id LIMIT ?",
                        (MAX_PAYLOAD_BYTES, from_ingest_id, limit, max_events + 1),
                    )
                    for record in cursor:
                        size = len(record[3].encode()) if isinstance(record[3], str) and len(record[3]) <= MAX_PAYLOAD_BYTES else 0
                        if selected_bytes + size > MAX_REPLAY_BYTES:
                            byte_limited = True
                            break
                        rows.append(record)
                        selected_bytes += size
            finally:
                conn.close()
    except (sqlite3.Error, OSError):
        raise ValueError("Retained research journal cannot be read") from None
    truncated = byte_limited or len(rows) > max_events
    rows = rows[:max_events]
    channels = set()
    decoded = []
    invalid = 0
    for ingest_id, received_at, channel, encoded in rows:
        if not isinstance(encoded, str) or len(encoded) > MAX_PAYLOAD_BYTES or len(encoded.encode()) > MAX_PAYLOAD_BYTES or not isinstance(received_at, (int, float)) or not math.isfinite(received_at) or not 0 <= received_at <= 4_102_444_800:
            invalid += 1
            continue
        try:
            payload = json.loads(encoded, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
            if not isinstance(payload, dict) or not isinstance(channel, str):
                raise ValueError("Malformed replay record")
            if channel == "__reconcile__":
                requested = payload.get("channel")
                if not isinstance(requested, str) or not isinstance(payload.get("rows"), list):
                    raise ValueError("Malformed replay confirmation")
                channels.add(requested)
            elif channel != "__control__":
                channels.add(channel)
            decoded.append((ingest_id, float(received_at), channel, payload))
        except (ValueError, TypeError, UnicodeError):
            invalid += 1
    # Offline parsing is enabled explicitly, independently of websocket ACKs.
    # No actual connected/live claim is made about this historical projection.
    model = Materializer(tuple(sorted(channels)))
    first_time = decoded[0][1] if decoded else 0.0
    model.gap(first_time, "offline-replay-without-prior-baseline")
    for meta in model.state["channels"].values():
        meta["acknowledged"] = True
        meta["acknowledgement_basis"] = "offline-parser-only; not a live channel acknowledgement"
    for ingest_id, received_at, channel, payload in decoded:
        invalid_before = model.state["metrics"]["invalid"]
        try:
            if channel == "__reconcile__":
                model.reconcile_greek(payload["channel"], payload["rows"], received_at, ingest_id)
            elif channel == "__control__":
                # Older journals did not persist control frames. Do not invent an
                # upstream gap ledger or replay a current connected state.
                if payload.get("event") == "disconnect":
                    model.gap(received_at, "retained-disconnect")
            else:
                model.apply(channel, payload, received_at, ingest_id)
        except (ValueError, TypeError, KeyError, ArithmeticError):
            invalid += 1
            model.gap(received_at, "invalid-retained-record")
        invalid += model.state["metrics"]["invalid"] - invalid_before
        model.state["connection"]["connected"] = False
    last_time = decoded[-1][1] if decoded else first_time
    snapshot = model.snapshot(last_time)
    snapshot["status"] = "offline-replay"
    snapshot["connection"]["connected"] = False
    for meta in snapshot["channels"].values():
        meta["acknowledged"] = False
        meta["status"] = "offline-replayed"
    snapshot["replay"] = {
        "kind": "local-retained-ingest-range", "network_used": False, "published": False,
        "requested_from_ingest_id": from_ingest_id, "requested_to_ingest_id": to_ingest_id,
        "first_available_ingest_id": low, "last_available_ingest_id": high,
        "first_replayed_ingest_id": rows[0][0] if rows else None,
        "last_replayed_ingest_id": rows[-1][0] if rows else None,
        "max_events": max_events, "max_payload_bytes": MAX_REPLAY_BYTES, "payload_byte_limited": byte_limited, "events_selected": len(rows), "events_decoded": len(decoded),
        "invalid_records": invalid, "truncated": truncated,
        "pruned_before_requested_start": low is not None and from_ingest_id < low,
        "requested_end_not_retained": to_ingest_id is not None and (high is None or to_ingest_id > high),
        "prior_baseline_available": False, "gap_history_available": False,
        "upstream_replay_available": False,
        "retained_range_complete": not truncated and invalid == 0 and low is not None and low <= from_ingest_id <= high and (to_ingest_id is None or to_ingest_id <= high),
    }
    snapshot["limitations"] = ["Local retained events only; no provider replay or missing-frame recovery.",
        "No prior state baseline is reconstructed; partial minute totals remain incomplete unless a retained REST confirmation exists.",
        "Older control transitions were not journaled, so complete upstream gap history is unavailable."]
    return snapshot
