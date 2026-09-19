"""Red/green tests for scripts/slm/export_dataset.py (SLM fine-tune corpus)."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from slm import export_dataset as mod  # noqa: E402


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
        CREATE TABLE knowledge (
          id INTEGER PRIMARY KEY, source TEXT NOT NULL, scope TEXT NOT NULL, doc_key TEXT NOT NULL,
          chunk_ix INTEGER NOT NULL DEFAULT 0, title TEXT, summary TEXT, content TEXT NOT NULL,
          metadata TEXT, content_hash TEXT NOT NULL, created_at TEXT NOT NULL, last_activity_at TEXT NOT NULL
        );
        """
    )
    return db


def _post(db, pid, title, content, tags_text=None, tags=None, images=None):
    db.execute(
        "INSERT INTO posts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            pid, title, content, "2026-09-01T00:00:00Z",
            json.dumps(images) if images is not None else None, None,
            json.dumps(tags) if tags is not None else None,
            json.dumps(tags_text) if tags_text is not None else None,
            None, "2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z",
        ),
    )


def _knowledge(db, key, title, content, summary, metadata=None):
    db.execute(
        "INSERT INTO knowledge (source, scope, doc_key, chunk_ix, title, summary, content, metadata,"
        " content_hash, created_at, last_activity_at) VALUES ('docs','research',?,0,?,?,?,?,'h','t','t')",
        (key, title, summary, content, json.dumps(metadata) if metadata else None),
    )


# ── tag normaliser mirrors scripts/newsfeed/tagger.js ─────────────────────

def test_normalise_tags_matches_js_rules():
    assert mod.normalise_tags(['#"put call ratio".', "btc", "BTC", "Dealer_Gamma", "", 7]) == [
        "PUT-CALL-RATIO", "BTC", "DEALER-GAMMA",
    ]


# ── tagger examples ────────────────────────────────────────────────────────

def test_tagger_examples_use_text_tags_and_exact_prompts(conn):
    conn.execute("INSERT INTO tag_taxonomy VALUES ('VOL','t'),('puts','t')")
    _post(conn, "p1", "Skew bid", "Puts are bid into the print", tags_text=["puts", "skew", "vol"])
    examples = mod.build_tagger_examples(conn)
    assert len(examples) == 1
    ex = examples[0]
    assert ex["task"] == "tagger"
    assert ex["id"] == "p1"
    system, user, assistant = ex["messages"]
    assert system["role"] == "system"
    assert system["content"].startswith("You are a financial-news tagger")
    assert system["content"].endswith("Existing tags (reuse when possible): PUTS, VOL")
    assert user == {"role": "user", "content": "Title: Skew bid\nBody: Puts are bid into the print"}
    assert json.loads(assistant["content"]) == {"tags": ["PUTS", "SKEW", "VOL"]}


def test_tagger_falls_back_to_merged_tags_only_for_imageless_posts(conn):
    _post(conn, "no-img", "A", "b", tags=["A", "B", "C"], images=[])
    _post(conn, "img", "A", "b", tags=["A", "B", "C"], images=["https://media.radon.run/x.png"])
    ids = [e["id"] for e in mod.build_tagger_examples(conn)]
    assert ids == ["no-img"]


def test_tagger_drops_rows_without_exactly_three_valid_tags(conn):
    _post(conn, "two", "A", "b", tags_text=["A", "B"])
    _post(conn, "dupe", "A", "b", tags_text=["A", "a", "B"])
    _post(conn, "junk", "A", "b", tags_text=["", "??", "B"])
    _post(conn, "ok", "A", "b", tags_text=["A", "B", "C"])
    assert [e["id"] for e in mod.build_tagger_examples(conn)] == ["ok"]


def test_tagger_user_prompt_truncates_body_at_1500_chars(conn):
    _post(conn, "long", "T", "x" * 2000, tags_text=["A", "B", "C"])
    user = mod.build_tagger_examples(conn)[0]["messages"][1]["content"]
    assert user == "Title: T\nBody: " + "x" * 1500


# ── distill examples ───────────────────────────────────────────────────────

def test_distill_examples_use_distill_prompt_scrub_and_tickers(conn):
    _knowledge(
        conn, "k1", "NVDA eval", "account U1234567 says buy NVDA",
        "What did the NVDA eval say? It said buy.", {"tickers": ["NVDA"]},
    )
    examples = mod.build_distill_examples(conn)
    assert len(examples) == 1
    ex = examples[0]
    assert ex["task"] == "distill"
    assert ex["id"] == "docs:k1:0"
    system, user, assistant = ex["messages"]
    assert system["content"].startswith("You distill documents from a trading system")
    assert user["content"] == "Title: NVDA eval\n\naccount [redacted-account] says buy NVDA"
    assert json.loads(assistant["content"]) == {
        "summary": "What did the NVDA eval say? It said buy.", "tickers": ["NVDA"],
    }


def test_distill_skips_rows_without_summary_and_defaults_tickers(conn):
    _knowledge(conn, "empty", "T", "c", "   ")
    _knowledge(conn, "none", "T", "c", None)
    _knowledge(conn, "ok", None, "c", "s")
    examples = mod.build_distill_examples(conn)
    assert [e["id"] for e in examples] == ["docs:ok:0"]
    assert examples[0]["messages"][1]["content"] == "c"
    assert json.loads(examples[0]["messages"][2]["content"])["tickers"] == []


# ── split + write ──────────────────────────────────────────────────────────

def test_split_is_deterministic_and_holds_out_about_ten_percent():
    examples = [{"id": f"id-{i}", "task": "tagger", "messages": []} for i in range(1000)]
    train, evals = mod.split_examples(examples, holdout=0.1)
    train2, evals2 = mod.split_examples(examples, holdout=0.1)
    assert [e["id"] for e in evals] == [e["id"] for e in evals2]
    assert len(train) + len(evals) == 1000
    assert 70 <= len(evals) <= 130
    assert not {e["id"] for e in train} & {e["id"] for e in evals}


def test_export_writes_jsonl_per_task_and_manifest(conn, tmp_path):
    _post(conn, "p1", "T", "b", tags_text=["A", "B", "C"])
    _knowledge(conn, "k1", "T", "c", "s")
    manifest = mod.export(conn, tmp_path, holdout=0.0)
    assert manifest["tasks"]["tagger"] == {"train": 1, "eval": 0}
    assert manifest["tasks"]["distill"] == {"train": 1, "eval": 0}
    rows = [json.loads(l) for l in (tmp_path / "tagger.train.jsonl").read_text().splitlines()]
    assert rows[0]["id"] == "p1" and len(rows[0]["messages"]) == 3
    assert (tmp_path / "distill.train.jsonl").exists()
    assert (tmp_path / "tagger.eval.jsonl").read_text() == ""
    assert json.loads((tmp_path / "manifest.json").read_text()) == manifest
