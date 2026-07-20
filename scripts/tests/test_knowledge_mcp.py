"""Knowledge MCP server (Phase 3) — LLM-free retrieval tools over the corpus.

Tool logic is exercised directly (`_kb_*_impl`) against a LOCAL libsql
:memory: database with the real 0028 migration applied — the stdio server is
never started and the module import must never touch the production DB.
Pins: read-only module source (no SQL write keywords) and side-effect-free
import (db.client.get_db monkeypatched to raise).
"""
from __future__ import annotations

import importlib
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import libsql_experimental as libsql
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from knowledge import mcp_server  # noqa: E402
from knowledge.mcp_server import (  # noqa: E402
    CONTENT_MAX_CHARS,
    DOC_KEY_MAX_CHARS,
    KNOWN_SCOPES,
    KNOWN_SOURCES,
    RECENT_LIMIT_MAX,
    SEARCH_LIMIT_MAX,
    SUMMARY_MAX_CHARS,
    TITLE_MAX_CHARS,
    _kb_incidents_impl,
    _kb_prior_evals_impl,
    _kb_recent_impl,
    _kb_search_impl,
)
from knowledge.schema import KnowledgeDoc  # noqa: E402
from knowledge.store import upsert_documents  # noqa: E402

_MIGRATION = _SCRIPTS_DIR / "db" / "migrations" / "0028_knowledge.sql"
_BOOTSTRAP_SQL = (
    "CREATE TABLE IF NOT EXISTS schema_migrations "
    "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
)

EMBEDDING_DIM = 384

SEARCH_RESULT_KEYS = {
    "source", "scope", "doc_key", "chunk_ix", "title", "summary",
    "content", "score", "last_activity_at",
}
RECENT_RESULT_KEYS = SEARCH_RESULT_KEYS - {"score"}


def _split_statements(sql: str) -> list[str]:
    stripped = "\n".join(re.sub(r"^\s*--.*$", "", line) for line in sql.splitlines())
    return [s.strip() for s in re.split(r";\s*$", stripped, flags=re.MULTILINE) if s.strip()]


@pytest.fixture
def db():
    conn = libsql.connect(":memory:")
    conn.execute(_BOOTSTRAP_SQL)
    for stmt in _split_statements(_MIGRATION.read_text(encoding="utf-8")):
        conn.execute(stmt)
    conn.commit()
    return conn


def _days_ago(days: int) -> str:
    stamp = datetime.now(timezone.utc) - timedelta(days=days)
    return stamp.isoformat().replace("+00:00", "Z")


def _unit_vector(hot_ix: int) -> list[float]:
    values = [0.0] * EMBEDDING_DIM
    values[hot_ix] = 1.0
    return values


def _doc(**overrides) -> KnowledgeDoc:
    base = dict(
        source="docs",
        scope="ops",
        doc_key="doc",
        chunk_ix=0,
        content="placeholder body",
        last_activity_at=_days_ago(1),
    )
    base.update(overrides)
    return KnowledgeDoc(**base)


class _RecordingDb:
    """Pass-through connection that records every SQL statement executed."""

    def __init__(self, inner):
        self._inner = inner
        self.statements: list[str] = []

    def execute(self, sql, *args):
        self.statements.append(sql)
        return self._inner.execute(sql, *args)


# ── kb_search ────────────────────────────────────────────────────────


class TestKbSearch:
    def test_happy_path_fts_only_shape(self, db):
        upsert_documents(
            db,
            [
                _doc(doc_key="ewy", scope="trading", source="journal",
                     title="EWY risk reversal", content="EWY risk reversal thesis"),
                _doc(doc_key="vix", scope="trading", content="VIX term structure notes"),
            ],
        )

        result = _kb_search_impl(db, None, "EWY risk reversal")

        assert result["retrieval"] == "fts-only"
        assert result["results"]
        top = result["results"][0]
        assert set(top) == SEARCH_RESULT_KEYS
        assert top["doc_key"] == "ewy"
        assert top["source"] == "journal"

    def test_hybrid_mode_with_query_embedder(self, db):
        upsert_documents(
            db,
            [
                _doc(doc_key="restore",
                     content="restore hangs while the snapshot finalizes",
                     embedding=_unit_vector(0)),
                _doc(doc_key="futures",
                     content="rolled the June futures position at the open",
                     embedding=_unit_vector(200)),
            ],
        )

        result = _kb_search_impl(
            db, lambda text: _unit_vector(0), "checkpoint stalls"
        )

        assert result["retrieval"] == "hybrid"
        assert result["results"][0]["doc_key"] == "restore"

    def test_broken_embedder_degrades_to_fts_only(self, db):
        upsert_documents(db, [_doc(content="cobalt runbook")])

        def broken(text):
            raise RuntimeError("model failed to load")

        result = _kb_search_impl(db, broken, "cobalt")

        assert result["retrieval"] == "fts-only"
        assert [r["doc_key"] for r in result["results"]] == ["doc"]

    def test_scope_and_source_filters(self, db):
        upsert_documents(
            db,
            [
                _doc(doc_key="ops-doc", scope="ops", content="cobalt runbook"),
                _doc(doc_key="trade-doc", scope="trading", content="cobalt trade thesis"),
                _doc(source="newsfeed", doc_key="post", scope="research", content="cobalt news"),
            ],
        )

        by_scope = _kb_search_impl(db, None, "cobalt", scopes=["ops"])
        by_source = _kb_search_impl(db, None, "cobalt", sources=["newsfeed"])

        assert [r["doc_key"] for r in by_scope["results"]] == ["ops-doc"]
        assert [r["doc_key"] for r in by_source["results"]] == ["post"]

    def test_unknown_source_is_error_listing_valid_values(self, db):
        result = _kb_search_impl(db, None, "cobalt", sources=["reddit"])

        assert set(result) == {"error"}
        assert "reddit" in result["error"]
        for source in KNOWN_SOURCES:
            assert source in result["error"]

    def test_unknown_scope_is_error_listing_valid_values(self, db):
        result = _kb_search_impl(db, None, "cobalt", scopes=["finance"])

        assert set(result) == {"error"}
        assert "finance" in result["error"]
        for scope in KNOWN_SCOPES:
            assert scope in result["error"]

    def test_blank_query_is_error(self, db):
        assert "error" in _kb_search_impl(db, None, "   ")

    def test_limit_clamped_to_max(self, db):
        docs = [
            _doc(source=source, doc_key=f"{source}-{ix}", content="cobalt note")
            for source in KNOWN_SOURCES
            for ix in range(3)
        ]
        upsert_documents(db, docs)

        result = _kb_search_impl(db, None, "cobalt", limit=99)

        assert len(result["results"]) == SEARCH_LIMIT_MAX

    def test_limit_floor_is_one(self, db):
        upsert_documents(db, [_doc(content="cobalt note")])

        result = _kb_search_impl(db, None, "cobalt", limit=0)

        assert len(result["results"]) == 1

    def test_string_bounds(self, db):
        upsert_documents(
            db,
            [
                _doc(
                    doc_key="k" * 300,
                    title="t" * 500,
                    summary="s" * 2000,
                    content="cobalt " + "x" * 5000,
                ),
            ],
        )

        row = _kb_search_impl(db, None, "cobalt")["results"][0]

        assert len(row["doc_key"]) == DOC_KEY_MAX_CHARS
        assert len(row["title"]) == TITLE_MAX_CHARS
        assert len(row["summary"]) == SUMMARY_MAX_CHARS
        assert len(row["content"]) == CONTENT_MAX_CHARS

    def test_no_neighbor_expansion_queries(self, db):
        upsert_documents(
            db,
            [
                _doc(doc_key="eval", chunk_ix=0, content="milestone one validation"),
                _doc(doc_key="eval", chunk_ix=1, content="cobalt edge decision"),
                _doc(doc_key="eval", chunk_ix=2, content="cobalt sizing"),
            ],
        )
        recording = _RecordingDb(db)

        result = _kb_search_impl(recording, None, "cobalt")

        assert result["results"]
        neighbor_selects = [s for s in recording.statements if "chunk_ix BETWEEN" in s]
        assert neighbor_selects == []


# ── kb_recent ────────────────────────────────────────────────────────


class TestKbRecent:
    def test_latest_first_no_score(self, db):
        upsert_documents(
            db,
            [
                _doc(doc_key="old", content="old note", last_activity_at=_days_ago(9)),
                _doc(doc_key="new", content="new note", last_activity_at=_days_ago(0)),
                _doc(doc_key="mid", content="mid note", last_activity_at=_days_ago(4)),
            ],
        )

        result = _kb_recent_impl(db)

        assert [r["doc_key"] for r in result["results"]] == ["new", "mid", "old"]
        assert set(result["results"][0]) == RECENT_RESULT_KEYS

    def test_source_filter(self, db):
        upsert_documents(
            db,
            [
                _doc(source="journal", scope="trading", doc_key="trade", content="a"),
                _doc(source="newsfeed", scope="research", doc_key="post", content="b"),
            ],
        )

        result = _kb_recent_impl(db, source="journal")

        assert [r["doc_key"] for r in result["results"]] == ["trade"]

    def test_unknown_source_is_error(self, db):
        result = _kb_recent_impl(db, source="twitter")

        assert set(result) == {"error"}
        for source in KNOWN_SOURCES:
            assert source in result["error"]

    def test_limit_clamped_to_max(self, db):
        upsert_documents(
            db,
            [_doc(doc_key=f"d{ix}", content=f"note {ix}") for ix in range(RECENT_LIMIT_MAX + 5)],
        )

        result = _kb_recent_impl(db, limit=999)

        assert len(result["results"]) == RECENT_LIMIT_MAX

    def test_default_limit(self, db):
        upsert_documents(
            db,
            [_doc(doc_key=f"d{ix}", content=f"note {ix}") for ix in range(15)],
        )

        assert len(_kb_recent_impl(db)["results"]) == 10


# ── kb_prior_evals ───────────────────────────────────────────────────


class TestKbPriorEvals:
    def _seed_ticker_corpus(self, db):
        upsert_documents(
            db,
            [
                _doc(source="journal", scope="trading", doc_key="ewy-trade",
                     content="EWY risk reversal opened"),
                _doc(source="evals", scope="trading", doc_key="ewy-eval",
                     content="EWY milestone eval passed"),
                _doc(source="docs", scope="ops", doc_key="ewy-runbook",
                     content="EWY mention in a runbook"),
            ],
        )

    def test_restricts_to_journal_and_evals(self, db):
        self._seed_ticker_corpus(db)

        result = _kb_prior_evals_impl(db, None, "EWY")

        assert result["ticker"] == "EWY"
        sources = {r["source"] for r in result["results"]}
        assert sources == {"journal", "evals"}

    def test_lowercase_ticker_uppercased(self, db):
        self._seed_ticker_corpus(db)

        result = _kb_prior_evals_impl(db, None, "ewy")

        assert result["ticker"] == "EWY"
        assert result["results"]

    @pytest.mark.parametrize("bad", ["", "TOOLONG7", "bad!", "brk.b", "  "])
    def test_invalid_ticker_is_error(self, db, bad):
        result = _kb_prior_evals_impl(db, None, bad)

        assert set(result) == {"error"}


# ── kb_incidents ─────────────────────────────────────────────────────


class TestKbIncidents:
    def _seed_incidents(self, db):
        upsert_documents(
            db,
            [
                _doc(source="incidents", scope="ops", doc_key="radon-api-outage",
                     content="destroy-storm outage", last_activity_at=_days_ago(2)),
                _doc(source="incidents", scope="ops", doc_key="radon-relay-stale",
                     content="stale tick loop", last_activity_at=_days_ago(9)),
                _doc(source="newsfeed", scope="research", doc_key="post",
                     content="unrelated news", last_activity_at=_days_ago(0)),
            ],
        )

    def test_only_incident_rows_latest_first(self, db):
        self._seed_incidents(db)

        result = _kb_incidents_impl(db)

        assert [r["doc_key"] for r in result["results"]] == [
            "radon-api-outage", "radon-relay-stale",
        ]
        assert set(result["results"][0]) == RECENT_RESULT_KEYS

    def test_service_filters_by_doc_key(self, db):
        self._seed_incidents(db)

        result = _kb_incidents_impl(db, service="relay")

        assert [r["doc_key"] for r in result["results"]] == ["radon-relay-stale"]

    def test_no_match_returns_empty(self, db):
        self._seed_incidents(db)

        assert _kb_incidents_impl(db, service="nats")["results"] == []

    def test_limit_clamped_to_max(self, db):
        upsert_documents(
            db,
            [
                _doc(source="incidents", scope="ops", doc_key=f"svc-{ix}", content=f"outage {ix}")
                for ix in range(RECENT_LIMIT_MAX + 5)
            ],
        )

        result = _kb_incidents_impl(db, limit=999)

        assert len(result["results"]) == RECENT_LIMIT_MAX


# ── pins: read-only module, side-effect-free import ──────────────────


class TestReadOnlyPin:
    def test_module_source_contains_no_write_statements(self):
        source = Path(mcp_server.__file__).read_text(encoding="utf-8")

        # SQL keywords are uppercase throughout this codebase; case-sensitive
        # so `sys.path.insert` and prose don't trip the pin.
        writes = re.findall(
            r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|REPLACE|UPSERT)\b", source
        )
        assert writes == []


class TestImportSideEffects:
    def test_import_does_not_connect_to_db(self, monkeypatch):
        import db.client as db_client

        def explode(*args, **kwargs):
            raise AssertionError("module import must not touch the DB")

        monkeypatch.setattr(db_client, "get_db", explode)
        sys.modules.pop("knowledge.mcp_server", None)
        try:
            module = importlib.import_module("knowledge.mcp_server")
        finally:
            sys.modules.pop("knowledge.mcp_server", None)
            importlib.import_module("knowledge.mcp_server")

        assert module is not None

    def test_tools_registered(self):
        names = {tool.name for tool in mcp_server.mcp._tool_manager.list_tools()}

        assert names == {"kb_search", "kb_recent", "kb_prior_evals", "kb_incidents"}
