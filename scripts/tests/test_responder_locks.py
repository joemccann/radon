"""Responder pid locks are created atomically (O_CREAT | O_EXCL).

The old check-then-write let a second responder that started between the
existence check and the write overwrite the first one's lock: both ran.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

import grok_page_responder
import incident_responder

NOW = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("module", [incident_responder, grok_page_responder])
def test_concurrent_creator_wins_and_we_stand_down(module, tmp_path, monkeypatch):
    lock = tmp_path / "cache" / ".responder.lock"
    real_mkdir = Path.mkdir

    def mkdir_then_race(self, *args, **kwargs):
        real_mkdir(self, *args, **kwargs)
        if self == lock.parent and not lock.exists():
            # A second responder creates its lock right now.
            lock.write_text(str(os.getpid()))

    monkeypatch.setattr(Path, "mkdir", mkdir_then_race)
    assert module.acquire_lock(lock, NOW) is False
    assert lock.read_text() == str(os.getpid())


@pytest.mark.parametrize("module", [incident_responder, grok_page_responder])
def test_free_lock_is_acquired(module, tmp_path):
    lock = tmp_path / "cache" / ".responder.lock"
    assert module.acquire_lock(lock, NOW) is True
    assert lock.read_text() == str(os.getpid())
    assert module.acquire_lock(lock, NOW) is False


@pytest.mark.parametrize("module", [incident_responder, grok_page_responder])
def test_stale_lock_is_broken(module, tmp_path):
    lock = tmp_path / ".responder.lock"
    lock.write_text("999999999")
    old = NOW.timestamp() - module.LOCK_STALE_SECS - 10
    os.utime(lock, (old, old))
    assert module.acquire_lock(lock, NOW) is True
    assert lock.read_text() == str(os.getpid())
    assert list(tmp_path.iterdir()) == [lock]
