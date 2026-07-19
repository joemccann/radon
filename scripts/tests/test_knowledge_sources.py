"""Phase 1 connectors — fixture-driven tests.

LOCAL libsql :memory: DB with miniature journal/posts/service_health tables
(real column names), tmp_path fixture files for evals/docs/trade_log. No
network, no get_db(). Window-relative dates only.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import libsql_experimental as libsql
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from knowledge.sources import ALL_SOURCES  # noqa: E402
from knowledge.sources import docs as docs_source  # noqa: E402
from knowledge.sources import evals as evals_source  # noqa: E402
from knowledge.sources import incidents as incidents_source  # noqa: E402
from knowledge.sources import journal as journal_source  # noqa: E402
from knowledge.sources import newsfeed as newsfeed_source  # noqa: E402


def _days_ago(days: int) -> str:
    stamp = datetime.now(timezone.utc) - timedelta(days=days)
    return stamp.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _days_ago_date(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()


@pytest.fixture
def db():
    conn = libsql.connect(":memory:")
    conn.execute(
        "CREATE TABLE journal (trade_id TEXT PRIMARY KEY, payload TEXT NOT NULL, "
        "filled_at TEXT, written_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE posts (id TEXT PRIMARY KEY, title TEXT, content TEXT, "
        "timestamp TEXT, images TEXT, raw_images TEXT, tags TEXT, tags_text TEXT, "
        "tags_vision TEXT, created_at TEXT, updated_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE service_health (service TEXT PRIMARY KEY, state TEXT, "
        "last_attempt_started_at TEXT, last_attempt_finished_at TEXT, "
        "last_error TEXT, updated_at TEXT)"
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------- journal


_AUTO_IMPORT_PAYLOAD = {
    "id": "00019285.6a5a6b03.01.01",
    "date": _days_ago_date(3),
    "ticker": "NVDA",
    "structure": "Closed Put $150 2026-07-24",
    "decision": "IB_AUTO_IMPORT",
    "action": "BUY_TO_CLOSE",
    "fill_price": 1.25,
    "total_cost": 125.0,
    "commission": 1.05,
    "ib_exec_id": "00019285.6a5a6b03.01.01",
    "notes": "Imported from IB session fills on " + _days_ago_date(3),
    "contracts": 1,
    "strike": 150,
    "right": "P",
    "expiry": "20260724",
}

_RICH_PAYLOAD = {
    "id": 1,
    "date": _days_ago_date(30),
    "ticker": "ALAB",
    "company_name": "Astera Labs Inc",
    "sector": "Semiconductors",
    "action": "TRADE",
    "decision": "EXECUTED",
    "structure": "Long Call - LEAP",
    "contract": "ALAB Jan 15, 2027 $120 Call",
    "contracts": 5,
    "fill_price": 36.9,
    "total_cost": 18452.24,
    "pct_of_bankroll": 1.7,
    "gates_passed": ["CONVEXITY", "EDGE (IV)", "RISK_MGMT"],
    "risk_factors": ["No dark pool flow confirmation", "Wide bid-ask spread"],
    "thesis": "LEAP options significantly underpriced vs realized volatility.",
    "target_exit": "IV gap closes to <15% OR 100% gain on premium",
    "stop_loss": "50% loss on premium",
    "notes": "Largest IV gap from LEAP scan (+43.6%).",
}


def _seed_journal(db):
    db.execute(
        "INSERT INTO journal (trade_id, payload, filled_at, written_at) VALUES (?, ?, ?, ?)",
        (
            _AUTO_IMPORT_PAYLOAD["id"],
            json.dumps(_AUTO_IMPORT_PAYLOAD),
            _days_ago_date(3),
            _days_ago(3),
        ),
    )
    db.execute(
        "INSERT INTO journal (trade_id, payload, filled_at, written_at) VALUES (?, ?, ?, ?)",
        ("manual-alab-1", json.dumps(_RICH_PAYLOAD), _days_ago_date(30), _days_ago(30)),
    )
    db.commit()


@pytest.fixture
def empty_trade_log(tmp_path, monkeypatch):
    path = tmp_path / "trade_log.json"
    path.write_text(json.dumps({"trades": [], "_checksum": "x"}))
    monkeypatch.setattr(journal_source, "TRADE_LOG_PATH", path)
    return path


def test_journal_auto_import_row_renders_fill_line(db, empty_trade_log):
    _seed_journal(db)
    docs = {d.doc_key: d for d in journal_source.fetch(db)}
    doc = docs["00019285.6a5a6b03.01.01"]
    assert "NVDA" in doc.content
    assert "BUY_TO_CLOSE" in doc.content
    assert "Closed Put $150 2026-07-24" in doc.content
    assert doc.scope == "trading"
    assert doc.metadata["ticker"] == "NVDA"
    assert doc.metadata["action"] == "BUY_TO_CLOSE"


def test_journal_rich_row_includes_thesis(db, empty_trade_log):
    _seed_journal(db)
    docs = {d.doc_key: d for d in journal_source.fetch(db)}
    doc = docs["manual-alab-1"]
    assert "LEAP options significantly underpriced" in doc.content
    assert "CONVEXITY" in doc.content
    assert "IV gap closes to <15%" in doc.content
    assert "50% loss on premium" in doc.content
    assert doc.metadata["ticker"] == "ALAB"


def test_journal_last_activity_from_written_at(db, empty_trade_log):
    _seed_journal(db)
    docs = {d.doc_key: d for d in journal_source.fetch(db)}
    assert docs["00019285.6a5a6b03.01.01"].last_activity_at == _days_ago(3)


def test_journal_trade_log_thesis_entries_included_stubs_skipped(db, tmp_path, monkeypatch):
    log = {
        "trades": [
            dict(_RICH_PAYLOAD),
            {
                "id": 2,
                "date": _days_ago_date(10),
                "ticker": "PLTR",
                "decision": "IB_AUTO_IMPORT",
                "action": "SELL_TO_CLOSE",
            },
        ],
        "_checksum": "x",
    }
    path = tmp_path / "trade_log.json"
    path.write_text(json.dumps(log))
    monkeypatch.setattr(journal_source, "TRADE_LOG_PATH", path)
    docs = list(journal_source.fetch(db))
    keys = [d.doc_key for d in docs]
    assert "trade_log:1" in keys
    assert all("trade_log:2" != k for k in keys)
    doc = next(d for d in docs if d.doc_key == "trade_log:1")
    assert "LEAP options significantly underpriced" in doc.content
    assert doc.last_activity_at == _days_ago_date(30) + "T00:00:00Z"


def test_journal_raw_and_deterministic(db, empty_trade_log):
    _seed_journal(db)
    first = list(journal_source.fetch(db))
    second = list(journal_source.fetch(db))
    assert first == second
    assert all(d.summary is None and d.embedding is None for d in first)
    assert all(d.source == "journal" for d in first)


def test_journal_skips_unparseable_payload(db, empty_trade_log):
    db.execute(
        "INSERT INTO journal (trade_id, payload, filled_at, written_at) VALUES (?, ?, ?, ?)",
        ("broken", "{not json", None, _days_ago(1)),
    )
    db.commit()
    assert list(journal_source.fetch(db)) == []


def test_journal_prune_authority_never_covers_trade_log_keys(
    tmp_path, monkeypatch, empty_trade_log
):
    # trade_log.json is host-local and DIVERGES between hosts (production
    # appends on the VPS while the laptop holds a stale copy), so even a host
    # WITH the file must not treat another host's entries as vanished.
    # 2026-07-19: the VPS's present-but-partial reports/ pruned 177 laptop
    # eval docs; same hazard here.
    keys = ["manual-alab-1", "trade_log:1"]
    assert journal_source.prunable_doc_keys(keys) == ["manual-alab-1"]
    monkeypatch.setattr(journal_source, "TRADE_LOG_PATH", tmp_path / "absent.json")
    assert journal_source.prunable_doc_keys(keys) == ["manual-alab-1"]


# ---------------------------------------------------------------- evals


def _report_html(title: str, paragraphs: list[str]) -> str:
    body = "\n".join(f"<div class='panel'><p>{p}</p></div>" for p in paragraphs)
    return (
        f"<html><head><title>{title}</title><style>.x{{color:red}}</style>"
        f"<script>var hidden = 'SCRIPT_NOISE';</script></head>"
        f"<body><h1 class='title'>{title}</h1>{body}</body></html>"
    )


@pytest.fixture
def reports_dir(tmp_path, monkeypatch):
    path = tmp_path / "reports"
    path.mkdir()
    monkeypatch.setattr(evals_source, "REPORTS_DIR", path)
    return path


def _set_mtime(path: Path, days_ago: int) -> str:
    stamp = datetime.now(timezone.utc) - timedelta(days=days_ago)
    epoch = stamp.timestamp()
    os.utime(path, (epoch, epoch))
    return (
        datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def test_evals_skips_tweet_cards_and_non_html(db, reports_dir):
    (reports_dir / "tweet-cta-2026-03-19-card-1.html").write_text(
        _report_html("Card", ["x" * 500])
    )
    (reports_dir / "leap-scan.json").write_text("{}")
    (reports_dir / "chart.png").write_bytes(b"\x89PNG")
    (reports_dir / f"hims-evaluation-{_days_ago_date(5)}.html").write_text(
        _report_html("HIMS Evaluation", ["A substantive thesis paragraph. " * 20])
    )
    docs = list(evals_source.fetch(db))
    assert {d.doc_key for d in docs} == {f"hims-evaluation-{_days_ago_date(5)}.html"}


def test_evals_skips_low_text_artifacts(db, reports_dir):
    (reports_dir / "stub.html").write_text(_report_html("Stub", ["tiny"]))
    assert list(evals_source.fetch(db)) == []


def test_evals_extracts_text_and_strips_markup(db, reports_dir):
    name = f"goog-evaluation-{_days_ago_date(4)}.html"
    (reports_dir / name).write_text(
        _report_html("GOOG Evaluation", ["Edge is a genuine dark pool signal. " * 20])
    )
    (doc,) = list(evals_source.fetch(db))
    assert "Edge is a genuine dark pool signal." in doc.content
    assert "SCRIPT_NOISE" not in doc.content
    assert "<p>" not in doc.content
    assert doc.title == "GOOG Evaluation"
    assert doc.scope == "trading"


def test_evals_chunks_long_reports_at_paragraph_boundaries(db, reports_dir):
    paragraphs = [f"Paragraph {i}. " + ("word " * 250) for i in range(8)]
    name = f"mstr-evaluation-{_days_ago_date(2)}.html"
    (reports_dir / name).write_text(_report_html("MSTR Evaluation", paragraphs))
    docs = list(evals_source.fetch(db))
    assert len(docs) > 1
    assert [d.chunk_ix for d in docs] == list(range(len(docs)))
    assert all(d.doc_key == name for d in docs)
    assert all(len(d.content) <= evals_source.CHUNK_CHARS for d in docs)
    joined = "\n\n".join(d.content for d in docs)
    assert "Paragraph 0." in joined and "Paragraph 7." in joined
    boundaries = [d.content for d in docs]
    assert all(chunk.strip() for chunk in boundaries)
    assert docs[0].content.rstrip().endswith("word.") or "word" in docs[0].content


def test_evals_mtime_and_doc_key_stable(db, reports_dir):
    name = f"tsla-evaluation-{_days_ago_date(6)}.html"
    path = reports_dir / name
    path.write_text(_report_html("TSLA Evaluation", ["Convex structure rationale. " * 30]))
    expected = _set_mtime(path, 6)
    first = list(evals_source.fetch(db))
    second = list(evals_source.fetch(db))
    assert first == second
    assert first[0].doc_key == name
    assert first[0].last_activity_at == expected
    assert first[0].summary is None and first[0].embedding is None


def test_evals_prune_authority_always_empty(tmp_path, monkeypatch, db, reports_dir):
    # reports/ is gitignored and host-local; each host holds a DIFFERENT
    # subset (laptop generates most evals, the VPS a few). A directory being
    # present is not authority over the shared corpus: on 2026-07-19 the
    # VPS's single-report reports/ pruned 177 laptop-generated eval docs.
    # Evals therefore never prune; stale rows are harmless.
    assert evals_source.prunable_doc_keys(["r.html"]) == []
    monkeypatch.setattr(evals_source, "REPORTS_DIR", tmp_path / "absent")
    assert evals_source.prunable_doc_keys(["r.html"]) == []


# ---------------------------------------------------------------- docs


_FENCED_MD = """# Ops Guide

Intro paragraph about operations.

## Restart procedure

```bash
# this comment is not a heading
systemctl restart radon-api
```

More prose after the fence.

## Second section

Tail content here.
"""


@pytest.fixture
def docs_tree(tmp_path, monkeypatch):
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    lessons = tmp_path / "tasks" / "lessons.md"
    lessons.parent.mkdir()
    monkeypatch.setattr(docs_source, "DOCS_DIR", docs_dir)
    monkeypatch.setattr(docs_source, "LESSONS_PATH", lessons)
    return docs_dir, lessons


def test_docs_chunks_on_headings_not_inside_code_fences(db, docs_tree):
    docs_dir, _ = docs_tree
    (docs_dir / "operations.md").write_text(_FENCED_MD)
    docs = list(docs_source.fetch(db))
    titles = [d.title for d in docs]
    assert "Ops Guide" in titles
    assert "Restart procedure" in titles
    assert "Second section" in titles
    assert not any("this comment is not a heading" in t for t in titles)
    restart = next(d for d in docs if d.title == "Restart procedure")
    assert "# this comment is not a heading" in restart.content
    assert "More prose after the fence." in restart.content


def test_docs_scope_and_doc_keys(db, docs_tree):
    docs_dir, lessons = docs_tree
    (docs_dir / "strategies.md").write_text("# Strategies\n\nBody.\n")
    lessons.write_text(f"## {_days_ago_date(1)} - Relay farm-down\n\nBounded ladder fix.\n")
    docs = list(docs_source.fetch(db))
    by_key = {}
    for d in docs:
        by_key.setdefault(d.doc_key, []).append(d)
    assert set(by_key) == {"docs/strategies.md", "tasks/lessons.md"}
    assert all(d.scope == "research" for d in by_key["docs/strategies.md"])
    assert all(d.scope == "ops" for d in by_key["tasks/lessons.md"])
    assert all(d.source == "docs" for d in docs)


def test_docs_long_section_splits_sequentially(db, docs_tree):
    docs_dir, _ = docs_tree
    paragraphs = "\n\n".join("prose " * 300 for _ in range(5))
    (docs_dir / "strategies.md").write_text(f"# Strategies\n\n{paragraphs}\n")
    docs = list(docs_source.fetch(db))
    assert len(docs) > 1
    assert [d.chunk_ix for d in docs] == list(range(len(docs)))
    assert all(len(d.content) <= docs_source.CHUNK_CHARS for d in docs)


def test_docs_mtime_propagates_and_deterministic(db, docs_tree):
    docs_dir, _ = docs_tree
    path = docs_dir / "evaluation.md"
    path.write_text("# Evaluation\n\nSeven milestones.\n")
    expected = _set_mtime(path, 9)
    first = list(docs_source.fetch(db))
    second = list(docs_source.fetch(db))
    assert first == second
    assert first[0].last_activity_at == expected
    assert first[0].summary is None and first[0].embedding is None


# ---------------------------------------------------------------- newsfeed


def _seed_posts(db):
    db.execute(
        "INSERT INTO posts (id, title, content, timestamp, images, raw_images, tags) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "cRThqRM_Kk",
            "MoMo touching the trendline",
            "Momentum names are back at the trendline that has held all year.",
            _days_ago(2),
            json.dumps(["https://media.radon.run/img/momo.png"]),
            json.dumps(["https://themarketear.com/img/momo.png"]),
            json.dumps(["TRENDLINE", "MOMENTUM", "SPX"]),
        ),
    )
    db.execute(
        "INSERT INTO posts (id, title, content, timestamp, images, raw_images, tags) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("aAAAAAA_Aa", "Vol reset", "VIX crushed into the print.", _days_ago(1), None, None, json.dumps(["VIX"])),
    )
    db.commit()


def test_newsfeed_doc_per_post_with_tags_and_timestamp(db):
    _seed_posts(db)
    docs = {d.doc_key: d for d in newsfeed_source.fetch(db)}
    assert set(docs) == {"cRThqRM_Kk", "aAAAAAA_Aa"}
    momo = docs["cRThqRM_Kk"]
    assert momo.scope == "research"
    assert momo.metadata["tags"] == ["TRENDLINE", "MOMENTUM", "SPX"]
    assert momo.metadata["tickers"] == ["SPX"]
    assert momo.metadata["url"] == "https://media.radon.run/img/momo.png"
    assert momo.last_activity_at == _days_ago(2)
    assert docs["aAAAAAA_Aa"].metadata["url"] is None


def test_newsfeed_reads_posts_in_bounded_batches(db, monkeypatch):
    # One unbounded SELECT of every post (full bodies + image JSON) exceeds
    # what Turso's HTTP pipeline will forward: newsfeed ingest 502'd for 11
    # hours on 2026-07-19 while every smaller source converged. Reads must
    # paginate on an id cursor with a bounded LIMIT per query.
    for i in range(5):
        db.execute(
            "INSERT INTO posts (id, title, content, timestamp, images, raw_images, tags) "
            "VALUES (?, ?, ?, ?, NULL, NULL, NULL)",
            (f"post-{i}", f"Title {i}", f"Body {i}", _days_ago(i), ),
        )
    db.commit()
    monkeypatch.setattr(newsfeed_source, "_BATCH_ROWS", 2)

    class CountingDb:
        def __init__(self, inner):
            self.inner = inner
            self.calls = []

        def execute(self, sql, *args):
            if "FROM posts" in sql:
                self.calls.append(sql)
            return self.inner.execute(sql, *args)

    counting = CountingDb(db)
    docs = list(newsfeed_source.fetch(counting))

    assert [d.doc_key for d in docs] == sorted(f"post-{i}" for i in range(5))
    assert len(counting.calls) >= 3, "expected multiple bounded reads, got one big SELECT"
    assert all("LIMIT" in sql for sql in counting.calls)


def test_newsfeed_content_merges_title_and_body(db):
    _seed_posts(db)
    momo = next(d for d in newsfeed_source.fetch(db) if d.doc_key == "cRThqRM_Kk")
    assert momo.content.startswith("MoMo touching the trendline")
    assert "held all year" in momo.content
    assert momo.title == "MoMo touching the trendline"
    assert momo.summary is None and momo.embedding is None


def test_newsfeed_deterministic(db):
    _seed_posts(db)
    assert list(newsfeed_source.fetch(db)) == list(newsfeed_source.fetch(db))


# ---------------------------------------------------------------- incidents


def _seed_service_health(db):
    db.execute(
        "INSERT INTO service_health VALUES (?, ?, ?, ?, ?, ?)",
        (
            "journal-gap-sli",
            "ok",
            _days_ago(1),
            _days_ago(1),
            json.dumps({"missing_exec_id_count": 0, "window_days": 7}),
            _days_ago(1),
        ),
    )
    db.execute(
        "INSERT INTO service_health VALUES (?, ?, ?, ?, ?, ?)",
        (
            "ib-realtime-relay",
            "error",
            _days_ago(0),
            _days_ago(0),
            "no ticks for 60s despite healthy gateway",
            _days_ago(0),
        ),
    )
    db.commit()


def test_incidents_one_doc_per_service_with_state_digest(db):
    _seed_service_health(db)
    docs = {d.doc_key: d for d in incidents_source.fetch(db)}
    assert set(docs) == {"journal-gap-sli", "ib-realtime-relay"}
    ok_doc = docs["journal-gap-sli"]
    assert ok_doc.scope == "ops"
    assert "ok" in ok_doc.content
    assert "missing_exec_id_count=0" in ok_doc.content
    assert ok_doc.metadata == {"service": "journal-gap-sli", "state": "ok"}
    err_doc = docs["ib-realtime-relay"]
    assert "no ticks for 60s" in err_doc.content
    assert "error" in err_doc.content
    assert err_doc.last_activity_at == _days_ago(0)


def test_incidents_deterministic_order(db):
    _seed_service_health(db)
    first = list(incidents_source.fetch(db))
    second = list(incidents_source.fetch(db))
    assert first == second
    assert [d.doc_key for d in first] == sorted(d.doc_key for d in first)
    assert all(d.summary is None and d.embedding is None for d in first)


# ---------------------------------------------------------------- registry


def test_all_sources_registry():
    assert set(ALL_SOURCES) == {"journal", "evals", "docs", "newsfeed", "incidents"}
    scopes = {"trading", "research", "ops"}
    for name, module in ALL_SOURCES.items():
        assert module.SOURCE == name
        assert module.SCOPE in scopes
        assert callable(module.fetch)
