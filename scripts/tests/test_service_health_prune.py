"""Tests for db.writer.prune_service_health_events (DUR-11 retention sweep).

The append-only ``service_health_events`` table (migration 0011) grows on
every state transition; the daily flex_token_check handler prunes rows older
than 90 days. These tests pin the cutoff math and the SQL surface without
touching a database (get_db is monkeypatched — see the 2026-05-14
test-pollution incident for why that is non-negotiable).
"""
from __future__ import annotations

import importlib.util
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Private package name: test_handler_heartbeat (and friends) install a
# synthetic ``db.writer`` into sys.modules when they run first in the full
# suite, so a plain ``import db.writer`` here returns a fake without the
# prune symbols. Load the REAL files under an isolated name instead.
_PKG = "_dur11_real_db"


def _load_real_writer():
    db_dir = _SCRIPTS_DIR / "db"
    if f"{_PKG}.writer" in sys.modules:
        return sys.modules[f"{_PKG}.writer"]
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(db_dir)]
    sys.modules[_PKG] = pkg
    for sub in ("client", "service_health_sql", "writer"):
        spec = importlib.util.spec_from_file_location(f"{_PKG}.{sub}", db_dir / f"{sub}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"{_PKG}.{sub}"] = module
        spec.loader.exec_module(module)
    return sys.modules[f"{_PKG}.writer"]


@pytest.fixture
def writer(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RADON_DB_NO_REPLICA", "1")
    return _load_real_writer()


@pytest.fixture
def fake_db(writer, monkeypatch: pytest.MonkeyPatch):
    cursor = MagicMock(name="cursor")
    cursor.rowcount = 7
    db = MagicMock(name="db")
    db.execute.return_value = cursor
    monkeypatch.setattr(writer, "get_db", lambda: db)
    return db


class TestPruneServiceHealthEvents:
    def test_retention_default_is_90_days(self, writer):
        assert writer.SERVICE_HEALTH_EVENTS_RETENTION_DAYS == 90

    def test_deletes_only_rows_older_than_cutoff_and_commits(self, writer, fake_db):
        before = datetime.now(timezone.utc)
        deleted = writer.prune_service_health_events()
        after = datetime.now(timezone.utc)

        assert deleted == 7
        fake_db.commit.assert_called_once()
        (sql, args), _ = fake_db.execute.call_args
        assert "DELETE FROM service_health_events" in sql
        assert "created_at < ?" in sql
        assert len(args) == 1

        cutoff = datetime.fromisoformat(args[0].replace("Z", "+00:00"))
        assert before - timedelta(days=90) <= cutoff <= after - timedelta(days=90)

    def test_custom_retention_moves_the_cutoff(self, writer, fake_db):
        writer.prune_service_health_events(retention_days=7)
        (_, args), _ = fake_db.execute.call_args
        cutoff = datetime.fromisoformat(args[0].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        assert abs((now - timedelta(days=7)) - cutoff) < timedelta(minutes=1)

    def test_unknown_rowcount_reports_zero(self, writer, fake_db):
        fake_db.execute.return_value.rowcount = -1
        assert writer.prune_service_health_events() == 0

    def test_db_errors_propagate_for_handler_retry(self, writer, fake_db):
        """The daily handler must raise on retryable errors (BaseHandler
        contract) — the prune helper must NOT swallow them."""
        fake_db.execute.side_effect = ValueError("Hrana: stream closed")
        with pytest.raises(ValueError, match="stream closed"):
            writer.prune_service_health_events()
