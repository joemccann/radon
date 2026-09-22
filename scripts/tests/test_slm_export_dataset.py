"""Red/green tests for scripts/slm/export_dataset.py (SLM fine-tune corpus)."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from newsfeed.slm.contract import tagger_system_prompt  # noqa: E402
from slm import export_dataset as mod  # noqa: E402

NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)
BODY = "Puts are bid into the print and dealers are short gamma into opex."


@pytest.fixture
def conn() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.executescript(
        """
        CREATE TABLE posts (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT, timestamp TEXT NOT NULL,
          images TEXT, raw_images TEXT, tags TEXT, tags_text TEXT, tags_vision TEXT,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE tag_taxonomy (tag TEXT PRIMARY KEY COLLATE NOCASE, created_at TEXT NOT NULL);
        CREATE TABLE research_post_sources (post_id TEXT PRIMARY KEY, provenance_json TEXT NOT NULL);
        CREATE TABLE knowledge (
          id INTEGER PRIMARY KEY, source TEXT NOT NULL, scope TEXT NOT NULL, doc_key TEXT NOT NULL,
          chunk_ix INTEGER NOT NULL DEFAULT 0, title TEXT, summary TEXT, content TEXT NOT NULL,
          metadata TEXT, content_hash TEXT NOT NULL, created_at TEXT NOT NULL, last_activity_at TEXT NOT NULL
        );
        """
    )
    return db


def _post(db, pid, title, content, *, tags_text=None, tags=None, images=None, days_ago=200):
    ts = (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    db.execute(
        "INSERT INTO posts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            pid, title, content, ts,
            json.dumps(images) if images is not None else None, None,
            json.dumps(tags) if tags is not None else None,
            json.dumps(tags_text) if tags_text is not None else None,
            None, ts, ts,
        ),
    )


def _knowledge(db, key, title, content, summary, metadata=None, chunk_ix=0):
    db.execute(
        "INSERT INTO knowledge (source, scope, doc_key, chunk_ix, title, summary, content, metadata,"
        " content_hash, created_at, last_activity_at) VALUES ('docs','research',?,?,?,?,?,?,'h','t','t')",
        (key, chunk_ix, title, summary, content, json.dumps(metadata) if metadata else None),
    )


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


# ── tagger ─────────────────────────────────────────────────────────────────

def test_tagger_uses_live_prompts_and_text_tags(conn, tmp_path):
    conn.execute("INSERT INTO tag_taxonomy VALUES ('VOL','t'),('puts','t')")
    _post(conn, "p1", "Skew bid", BODY, tags_text=["puts", "skew", "vol"])
    mod.export_tagger(conn, tmp_path, extract_at=NOW)
    rows = _rows(tmp_path / "train.jsonl")
    assert len(rows) == 1
    system, user, assistant = rows[0]["messages"]
    assert system["content"] == tagger_system_prompt(["PUTS", "VOL"])
    assert system["content"].endswith("Existing tags (reuse when possible): PUTS, VOL")
    assert user == {"role": "user", "content": f"Title: Skew bid\nBody: {BODY}"}
    assert json.loads(assistant["content"]) == {"tags": ["PUTS", "SKEW", "VOL"]}
    assert rows[0]["id"] == "p1" and rows[0]["label_source"] == "ladder"


def test_tagger_row_rules(conn, tmp_path):
    _post(conn, "ok", "A", BODY, tags_text=["A", "B", "C"])
    _post(conn, "two", "A", BODY + " two", tags_text=["A", "B"])
    _post(conn, "dupe-tag", "A", BODY + " dupe", tags_text=["A", "a", "B"])
    _post(conn, "junk", "A", BODY + " junk", tags_text=["", "??", "B"])
    _post(conn, "stub", "A", "short", tags_text=["A", "B", "C"])
    _post(conn, "research", "A", BODY + " research", tags_text=["A", "B", "C"])
    conn.execute("INSERT INTO research_post_sources VALUES ('research','{}')")
    _post(conn, "no-img", "A", BODY + " merged", tags=["A", "B", "C"], images=[])
    _post(conn, "img", "A", BODY + " vision", tags=["A", "B", "C"], images=["https://media.radon.run/x.png"])
    mod.export_tagger(conn, tmp_path, extract_at=NOW)
    assert {r["id"] for r in _rows(tmp_path / "train.jsonl")} == {"ok", "no-img"}


def test_tagger_dedupes_on_content_keeping_earliest_and_strips_pii(conn, tmp_path):
    body = BODY + " mail joe@example.com see https://x.com/a"
    _post(conn, "late", "T", body, tags_text=["A", "B", "C"], days_ago=100)
    _post(conn, "early", "T", body, tags_text=["A", "B", "C"], days_ago=150)
    manifest = mod.export_tagger(conn, tmp_path, extract_at=NOW)
    rows = _rows(tmp_path / "train.jsonl")
    assert [r["id"] for r in rows] == ["early"]
    assert "joe@example.com" not in rows[0]["messages"][1]["content"]
    assert "https://" not in rows[0]["messages"][1]["content"]
    assert manifest["pii_email_count"] == 2 and manifest["pii_url_count"] == 2


def test_tagger_user_prompt_truncates_body_at_1500_chars(conn, tmp_path):
    _post(conn, "long", "T", "x" * 2000, tags_text=["A", "B", "C"])
    mod.export_tagger(conn, tmp_path, extract_at=NOW)
    assert _rows(tmp_path / "train.jsonl")[0]["messages"][1]["content"] == "Title: T\nBody: " + "x" * 1500


def test_tagger_time_split_is_strictly_ordered(conn, tmp_path):
    for i, days in enumerate((5, 30, 61, 75, 91, 120)):
        _post(conn, f"p{i}", "T", BODY + str(i), tags_text=["A", "B", "C"], days_ago=days)
    manifest = mod.export_tagger(conn, tmp_path, extract_at=NOW)
    assert manifest["split_counts"] == {"train": 2, "valid": 2, "test": 2}
    ts = {name: [r["timestamp"] for r in _rows(tmp_path / f"{name}.jsonl")] for name in ("train", "valid", "test")}
    assert max(ts["train"]) < min(ts["valid"]) <= max(ts["valid"]) < min(ts["test"])
    assert manifest["cutoff_dates"]["test_start"] == (NOW - timedelta(days=60)).isoformat()
    assert manifest["cutoff_dates"]["valid_start"] == (NOW - timedelta(days=90)).isoformat()


def test_tagger_manifest_carries_trainer_contract(conn, tmp_path):
    conn.execute("INSERT INTO tag_taxonomy VALUES ('A','t'),('B','t'),('C','t'),('D','t')")
    for i in range(3):
        _post(conn, f"p{i}", "T", BODY + str(i), tags_text=["A", "B", "C"])
    _post(conn, "p9", "T", BODY + "9", tags_text=["A", "B", "D"])
    manifest = mod.export_tagger(conn, tmp_path, extract_at=NOW)
    assert manifest["sources"] == ["turso.posts"]
    assert manifest["rows"] == 4 and manifest["label_source_counts"] == {"ladder": 4}
    assert manifest["label_distribution"] == {"A": 4, "B": 4, "C": 3, "D": 1}
    assert manifest["rare_tags"] == ["C"]
    assert manifest["taxonomy_size"] == 4 and len(manifest["taxonomy_sha256"]) == 64
    assert manifest["body_char_limit"] == 1500
    assert len(manifest["prompt_contract_sha256"]) == 64
    assert json.loads((tmp_path / "manifest.json").read_text()) == manifest


def test_tagger_paginates_on_id_cursor(conn, tmp_path):
    for i in range(mod.PAGE + 5):
        _post(conn, f"p{i:04d}", "T", BODY + str(i), tags_text=["A", "B", "C"])
    assert mod.export_tagger(conn, tmp_path, extract_at=NOW)["rows"] == mod.PAGE + 5


# ── distill ────────────────────────────────────────────────────────────────

def test_distill_uses_distill_prompt_scrub_and_tickers(conn, tmp_path):
    _knowledge(
        conn, "k1", "NVDA eval", "account U1234567 says buy NVDA",
        "What did the NVDA eval say? It said buy.", {"tickers": ["NVDA"]},
    )
    mod.export_distill(conn, tmp_path)
    rows = [r for name in ("train", "valid", "test") for r in _rows(tmp_path / f"{name}.jsonl")]
    assert len(rows) == 1
    ex = rows[0]
    assert ex["id"] == "docs:k1:0" and ex["doc_key"] == "docs:k1"
    system, user, assistant = ex["messages"]
    assert system["content"].startswith("You distill documents from a trading system")
    assert user["content"] == "Title: NVDA eval\n\naccount [redacted-account] says buy NVDA"
    assert json.loads(assistant["content"]) == {
        "summary": "What did the NVDA eval say? It said buy.", "tickers": ["NVDA"],
    }


def test_distill_skips_rows_without_summary_and_defaults_tickers(conn, tmp_path):
    _knowledge(conn, "empty", "T", "c", "   ")
    _knowledge(conn, "none", "T", "c", None)
    _knowledge(conn, "ok", None, "c", "s")
    manifest = mod.export_distill(conn, tmp_path)
    assert manifest["rows"] == 1 and manifest["sources"] == ["turso.knowledge"]
    rows = [r for name in ("train", "valid", "test") for r in _rows(tmp_path / f"{name}.jsonl")]
    assert rows[0]["messages"][1]["content"] == "c"
    assert json.loads(rows[0]["messages"][2]["content"])["tickers"] == []


def test_distill_split_is_document_level_and_deterministic(conn, tmp_path):
    for d in range(300):
        for chunk in range(3):
            _knowledge(conn, f"doc{d}", "T", f"c{d}-{chunk}", "s", chunk_ix=chunk)
    manifest = mod.export_distill(conn, tmp_path)
    per_split = {name: {r["doc_key"] for r in _rows(tmp_path / f"{name}.jsonl")} for name in ("train", "valid", "test")}
    assert not (per_split["train"] & per_split["valid"]) and not (per_split["train"] & per_split["test"])
    assert not (per_split["valid"] & per_split["test"])
    counts = manifest["split_counts"]
    assert counts["train"] + counts["valid"] + counts["test"] == 900
    assert 40 <= counts["valid"] <= 140 and 40 <= counts["test"] <= 140
    assert mod.export_distill(conn, tmp_path)["split_counts"] == counts


# ── layout ─────────────────────────────────────────────────────────────────

def test_export_writes_v1_layout_per_task(conn, tmp_path):
    _post(conn, "p1", "T", BODY, tags_text=["A", "B", "C"])
    _knowledge(conn, "k1", "T", "c", "s")
    manifests = mod.export(conn, tmp_path, extract_at=NOW)
    assert manifests["tagger"]["split_counts"]["train"] == 1
    assert manifests["distill"]["rows"] == 1
    for task in ("tagger", "distill"):
        for name in ("train", "valid", "test", "manifest"):
            ext = "json" if name == "manifest" else "jsonl"
            assert (tmp_path / task / "v1" / f"{name}.{ext}").exists()
