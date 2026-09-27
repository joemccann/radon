"""Knowledge ingest retries must outwait Turso's abandoned-stream expiry.

Turso support (2026-09-26): an abandoned idle transaction is reaped after
10s, a running one after up to 300s. Retrying a timed-out BEGIN IMMEDIATE
after 1s queued a second writer behind the orphan and raised concurrency.
"""
from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from knowledge import ingest as ingest_mod  # noqa: E402
from knowledge.http_db import TransportError  # noqa: E402

TURSO_IDLE_STREAM_EXPIRY_SECS = 10.0


def _record_sleeps(monkeypatch) -> list[float]:
    sleeps: list[float] = []
    monkeypatch.setattr(ingest_mod.time, "sleep", sleeps.append)
    return sleeps


def _assert_paced(sleeps: list[float]) -> None:
    assert sleeps, "a transient failure must wait before retrying"
    assert all(s > TURSO_IDLE_STREAM_EXPIRY_SECS for s in sleeps), sleeps
    assert all(b > a for a, b in zip(sleeps, sleeps[1:])), sleeps
    # Worst case stays well inside the unit's 1800s TimeoutStartSec.
    assert sum(sleeps) < 300, sleeps


def test_prepared_write_retry_outwaits_abandoned_transaction(monkeypatch):
    sleeps = _record_sleeps(monkeypatch)
    calls = []

    def operation(_connection):
        calls.append(1)
        if len(calls) < ingest_mod._WRITE_ATTEMPTS:
            raise TransportError("transaction receipt timed out")
        return "ok"

    assert ingest_mod._persist_prepared(lambda: object(), operation, source="newsfeed") == "ok"
    assert len(calls) == ingest_mod._WRITE_ATTEMPTS
    assert len(sleeps) == ingest_mod._WRITE_ATTEMPTS - 1
    _assert_paced(sleeps)


def test_source_retry_outwaits_abandoned_transaction(monkeypatch):
    import db.service_cycle as service_cycle_mod
    import knowledge.sources as sources_mod

    sleeps = _record_sleeps(monkeypatch)

    def always_busy(db, module, **kwargs):
        raise RuntimeError("SQLITE_BUSY: database is locked")

    monkeypatch.setattr(ingest_mod, "ingest_source", always_busy)
    monkeypatch.setattr(ingest_mod, "_fresh_db", lambda: object())
    monkeypatch.setattr(service_cycle_mod, "service_cycle",
                        lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(sources_mod, "ALL_SOURCES",
                        {"newsfeed": SimpleNamespace(SOURCE="newsfeed", SCOPE="research")})

    with pytest.raises(RuntimeError, match="newsfeed"):
        ingest_mod.main(["--source", "newsfeed", "--no-distill", "--no-embed"])
    assert len(sleeps) == ingest_mod._SOURCE_ATTEMPTS - 1
    _assert_paced(sleeps)


def test_retry_delay_is_jittered_so_parallel_writers_do_not_realign(monkeypatch):
    monkeypatch.setattr(ingest_mod.random, "random", lambda: 0.0)
    low = ingest_mod._retry_delay(1)
    monkeypatch.setattr(ingest_mod.random, "random", lambda: 1.0)
    high = ingest_mod._retry_delay(1)
    assert TURSO_IDLE_STREAM_EXPIRY_SECS < low < high
