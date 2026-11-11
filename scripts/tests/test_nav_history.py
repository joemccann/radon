"""Tests for daily NAV history tracking."""
import json
import os
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

import ib_sync


def test_concurrent_updates_preserve_existing_history(tmp_path, monkeypatch):
    path = tmp_path / "nav_history.jsonl"
    path.write_text('{"date":"2026-01-01","nav":100.0}\n')
    monkeypatch.setattr(ib_sync, "NAV_HISTORY_PATH", path)

    threads = [
        threading.Thread(target=ib_sync._append_nav_snapshot, args=(value,))
        for value in (200.0, 300.0)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert any(row["date"] == "2026-01-01" and row["nav"] == 100.0 for row in rows)
    assert len({row["date"] for row in rows}) == len(rows)


def test_interrupted_update_preserves_history(tmp_path, monkeypatch):
    path = tmp_path / "nav_history.jsonl"
    original = '{"date":"2026-01-01","nav":100.0}\n'
    path.write_text(original)
    monkeypatch.setattr(ib_sync, "NAV_HISTORY_PATH", path)
    monkeypatch.setattr(ib_sync.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("boom")))

    with pytest.raises(OSError, match="boom"):
        ib_sync._append_nav_snapshot(200.0)

    assert path.read_text() == original
