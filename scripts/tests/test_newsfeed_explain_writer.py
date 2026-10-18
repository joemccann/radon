"""Writer helpers for the newsfeed explanation cache."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from db import writer

MIGRATION = Path(__file__).resolve().parents[1] / "db/migrations/0096_newsfeed_explanations.sql"


class _Mem:
    def __init__(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        self.db.executescript(MIGRATION.read_text())

    def execute(self, sql, args=()):
        return self.db.execute(sql, args)

    def commit(self):
        self.db.commit()


def test_writer_exports_explain_cache_helpers():
    assert callable(writer.upsert_newsfeed_explanation)
    assert callable(writer.get_newsfeed_explanation)


def test_upsert_then_get_round_trip(monkeypatch):
    mem = _Mem()
    monkeypatch.setattr(writer, "get_db", lambda: mem)
    writer.upsert_newsfeed_explanation(
        "post-1",
        "hash-1",
        "The auction cleared at a cap.",
        model="claude-opus-5",
        provider="anthropic",
        has_image=True,
        created_at="2026-10-18T12:00:00Z",
    )
    row = writer.get_newsfeed_explanation("post-1", "hash-1")
    assert row["explanation"] == "The auction cleared at a cap."
    assert row["model"] == "claude-opus-5"
    assert row["provider"] == "anthropic"
    assert int(row["has_image"]) == 1
    assert writer.get_newsfeed_explanation("post-1", "other") is None


def test_upsert_replaces_same_key(monkeypatch):
    mem = _Mem()
    monkeypatch.setattr(writer, "get_db", lambda: mem)
    writer.upsert_newsfeed_explanation(
        "post-1", "hash-1", "first", model="a", provider="anthropic", has_image=False
    )
    writer.upsert_newsfeed_explanation(
        "post-1", "hash-1", "second", model="b", provider="xai", has_image=True
    )
    row = writer.get_newsfeed_explanation("post-1", "hash-1")
    assert row["explanation"] == "second"
    assert row["provider"] == "xai"
    count = mem.execute("SELECT COUNT(*) AS n FROM newsfeed_explanations").fetchone()["n"]
    assert count == 1


def test_upsert_rejects_empty_explanation():
    with pytest.raises(ValueError):
        writer.upsert_newsfeed_explanation(
            "post-1", "hash-1", "  ", model="a", provider="anthropic", has_image=False
        )
