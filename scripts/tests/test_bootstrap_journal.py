#!/usr/bin/env python3
"""
Tests for scripts/db/bootstrap_journal.py — Red/Green TDD.

Verifies:
- ib_exec_id is preferred as the stable trade_id when present.
- Legacy rows (no ib_exec_id) get a deterministic ticker|date|structure|id key.
- filled_at is sourced from filled_at -> date -> close_date.
- Idempotent: re-running calls upsert with the same key per row.
- Non-dict trade entries are skipped without raising.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))


def _write_log(tmp_path: Path, trades: list) -> Path:
    log = tmp_path / "trade_log.json"
    log.write_text(json.dumps({"trades": trades}), encoding="utf-8")
    return log


def _import_module(monkeypatch, log_path: Path):
    monkeypatch.setenv("TURSO_DB_URL", "libsql://test")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test")
    if "db.bootstrap_journal" in sys.modules:
        del sys.modules["db.bootstrap_journal"]
    import db.bootstrap_journal as mod
    monkeypatch.setattr(mod, "TRADE_LOG_PATH", log_path)
    return mod


def test_prefers_ib_exec_id(tmp_path, monkeypatch):
    log = _write_log(
        tmp_path,
        [
            {"id": 1, "ticker": "GOOG", "date": "2026-04-01", "structure": "Long Call", "ib_exec_id": "EX-123"},
        ],
    )
    mod = _import_module(monkeypatch, log)
    with patch.object(mod, "upsert_journal_entry") as up:
        rc = mod.main()
    assert rc == 0
    up.assert_called_once()
    args, kwargs = up.call_args
    assert args[0] == "EX-123"
    assert kwargs["filled_at"] == "2026-04-01"


def test_falls_back_to_legacy_key(tmp_path, monkeypatch):
    log = _write_log(
        tmp_path,
        [
            {"id": 7, "ticker": "AMD", "date": "2026-04-02", "structure": "Bull Call Spread"},
        ],
    )
    mod = _import_module(monkeypatch, log)
    with patch.object(mod, "upsert_journal_entry") as up:
        mod.main()
    assert up.call_args.args[0] == "AMD|2026-04-02|Bull Call Spread|7"


def test_filled_at_falls_back_through_chain(tmp_path, monkeypatch):
    log = _write_log(
        tmp_path,
        [
            {"id": 1, "ticker": "X", "structure": "S", "ib_exec_id": "A", "filled_at": "2026-04-04T10:00:00Z"},
            {"id": 2, "ticker": "Y", "structure": "S", "ib_exec_id": "B", "date": "2026-04-05"},
            {"id": 3, "ticker": "Z", "structure": "S", "ib_exec_id": "C", "close_date": "2026-04-06"},
        ],
    )
    mod = _import_module(monkeypatch, log)
    with patch.object(mod, "upsert_journal_entry") as up:
        mod.main()
    received = [c.kwargs["filled_at"] for c in up.call_args_list]
    assert received == ["2026-04-04T10:00:00Z", "2026-04-05", "2026-04-06"]


def test_skips_non_dict_entries(tmp_path, monkeypatch):
    log = _write_log(
        tmp_path,
        [
            {"id": 1, "ticker": "GOOG", "date": "2026-04-01", "structure": "Long Call"},
            "not-a-dict",
            None,
        ],
    )
    mod = _import_module(monkeypatch, log)
    with patch.object(mod, "upsert_journal_entry") as up:
        mod.main()
    assert up.call_count == 1


def test_idempotent_keys_match_across_runs(tmp_path, monkeypatch):
    log = _write_log(
        tmp_path,
        [
            {"id": 1, "ticker": "GOOG", "date": "2026-04-01", "structure": "Long Call", "ib_exec_id": "EX-1"},
            {"id": 2, "ticker": "AMD", "date": "2026-04-02", "structure": "Bull Call Spread"},
        ],
    )
    mod = _import_module(monkeypatch, log)
    with patch.object(mod, "upsert_journal_entry") as up:
        mod.main()
        first_keys = [c.args[0] for c in up.call_args_list]
        up.reset_mock()
        mod.main()
        second_keys = [c.args[0] for c in up.call_args_list]
    assert first_keys == second_keys


def test_missing_log_returns_zero(tmp_path, monkeypatch):
    log = tmp_path / "trade_log.json"  # absent
    mod = _import_module(monkeypatch, log)
    with patch.object(mod, "upsert_journal_entry") as up:
        rc = mod.main()
    assert rc == 0
    up.assert_not_called()
