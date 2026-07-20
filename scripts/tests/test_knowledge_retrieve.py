"""Knowledge retrieval (Phase 0) — hybrid search legs, RRF fusion, recency
decay, dedup, per-source cap, neighbor expansion.

Fusion/decay helpers are pure Python (no DB). DB-backed tests run against a
LOCAL libsql :memory: database with the real 0028 migration applied — local
libsql_experimental 0.0.55 supports FTS5 + bm25 + vector32 + ANN vector_top_k
(verified in the Phase 0 spike). The vector leg is additionally exercised via
an injected numpy scorer, the seam production keeps for tests.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import libsql_experimental as libsql
import numpy as np
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from knowledge.retrieve import (  # noqa: E402
    CANDIDATE_POOL,
    MAX_PER_SOURCE,
    MIN_RECENCY_FACTOR,
    RRF_K,
    cap_per_source,
    dedup_best_chunk,
    hybrid_search,
    recency_factor,
    rrf_fuse,
)
from knowledge.schema import KnowledgeDoc  # noqa: E402
from knowledge.store import upsert_documents  # noqa: E402

_MIGRATION = _SCRIPTS_DIR / "db" / "migrations" / "0028_knowledge.sql"
_BOOTSTRAP_SQL = (
    "CREATE TABLE IF NOT EXISTS schema_migrations "
    "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
)

EMBEDDING_DIM = 384


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


def _numpy_vector_search(db, query_embedding, pool, scopes=None, sources=None):
    """Injected vector leg: cosine over stored F32 blobs, best-first ids.
    Ignores the scope/source filters — _fetch_candidate_rows still enforces
    them, which is exactly the fallback path this seam exercises."""
    rows = db.execute(
        "SELECT id, embedding FROM knowledge WHERE embedding IS NOT NULL"
    ).fetchall()
    query = np.asarray(query_embedding, dtype=np.float32)
    scored = []
    for row_id, blob in rows:
        stored = np.frombuffer(blob, dtype=np.float32)
        cosine = float(stored @ query) / float(
            np.linalg.norm(stored) * np.linalg.norm(query) or 1.0
        )
        scored.append((-cosine, row_id))
    return [row_id for _, row_id in sorted(scored)[:pool]]


# ── pure fusion math (no DB) ─────────────────────────────────────────


class TestRrfFuse:
    def test_hand_computed_example(self):
        fused = rrf_fuse({"fts": ["a", "b", "c"], "vector": ["b"]})

        assert fused["a"] == pytest.approx(1.0 / (RRF_K + 1))
        assert fused["b"] == pytest.approx(1.0 / (RRF_K + 2) + 1.0 / (RRF_K + 1))
        assert fused["c"] == pytest.approx(1.0 / (RRF_K + 3))
        assert sorted(fused, key=fused.get, reverse=True) == ["b", "a", "c"]

    def test_weights_scale_leg_contributions(self):
        fused = rrf_fuse(
            {"fts": ["a"], "vector": ["b"]},
            weights={"fts": 1.0, "vector": 0.5},
        )

        assert fused["a"] == pytest.approx(1.0 / (RRF_K + 1))
        assert fused["b"] == pytest.approx(0.5 / (RRF_K + 1))
        assert fused["a"] > fused["b"]


class TestRecencyFactor:
    def test_decaying_source_halves_per_half_life(self):
        now = datetime.now(timezone.utc)

        assert recency_factor("newsfeed", _days_ago(0), now) == pytest.approx(1.0, abs=1e-3)
        assert recency_factor("newsfeed", _days_ago(7), now) == pytest.approx(0.5, abs=1e-3)

    def test_decay_is_floored(self):
        now = datetime.now(timezone.utc)

        assert recency_factor("newsfeed", _days_ago(1000), now) == MIN_RECENCY_FACTOR

    def test_non_decaying_source_never_decays(self):
        now = datetime.now(timezone.utc)

        assert recency_factor("docs", _days_ago(1000), now) == 1.0


class TestPureListOps:
    def test_dedup_keeps_best_chunk_per_doc(self):
        scored = [
            (0.9, {"source": "docs", "doc_key": "a", "chunk_ix": 2}),
            (0.5, {"source": "docs", "doc_key": "a", "chunk_ix": 0}),
            (0.4, {"source": "docs", "doc_key": "b", "chunk_ix": 0}),
        ]

        kept = dedup_best_chunk(scored)

        assert [(score, row["doc_key"], row["chunk_ix"]) for score, row in kept] == [
            (0.9, "a", 2),
            (0.4, "b", 0),
        ]

    def test_cap_per_source(self):
        scored = [(1.0 - ix / 10, {"source": "newsfeed", "doc_key": str(ix)}) for ix in range(5)]
        scored.append((0.1, {"source": "docs", "doc_key": "d"}))

        kept = cap_per_source(scored, max_per_source=2)

        sources = [row["source"] for _, row in kept]
        assert sources.count("newsfeed") == 2
        assert sources.count("docs") == 1


# ── DB-backed hybrid search ──────────────────────────────────────────


class TestFtsLeg:
    def test_exact_token_match_ranks_first(self, db):
        upsert_documents(
            db,
            [
                _doc(doc_key="ewy", scope="trading", content="EWY risk reversal thesis, dark pool prints"),
                _doc(doc_key="vix", scope="trading", content="VIX futures term structure notes"),
                _doc(doc_key="cri", scope="research", content="crash risk indicator methodology"),
            ],
        )

        results = hybrid_search(db, "EWY risk reversal")

        assert results
        assert results[0]["doc_key"] == "ewy"

    def test_no_match_returns_empty(self, db):
        upsert_documents(db, [_doc(content="nothing relevant here")])

        assert hybrid_search(db, "zzzunknowntoken") == []


class TestVectorLeg:
    def _seed_paraphrase_corpus(self, db):
        upsert_documents(
            db,
            [
                _doc(
                    doc_key="restore",
                    content="restore hangs while the snapshot finalizes",
                    embedding=_unit_vector(0),
                ),
                _doc(
                    doc_key="futures",
                    content="rolled the June futures position at the open",
                    embedding=_unit_vector(200),
                ),
            ],
        )

    def test_paraphrase_match_via_injected_scorer(self, db):
        self._seed_paraphrase_corpus(db)

        results = hybrid_search(
            db,
            "checkpoint stalls",  # no token overlap with either doc
            query_embedding=_unit_vector(0),
            vector_search=_numpy_vector_search,
        )

        assert results
        assert results[0]["doc_key"] == "restore"

    def test_paraphrase_match_via_server_side_top_k(self, db):
        self._seed_paraphrase_corpus(db)

        results = hybrid_search(db, "checkpoint stalls", query_embedding=_unit_vector(0))

        assert results
        assert results[0]["doc_key"] == "restore"


class TestRecencyOrdering:
    def test_fresh_newsfeed_doc_outranks_stale_equal_match(self, db):
        upsert_documents(
            db,
            [
                _doc(
                    source="newsfeed",
                    doc_key="stale",
                    content="cobalt supply squeeze report",
                    last_activity_at=_days_ago(30),
                ),
                _doc(
                    source="newsfeed",
                    doc_key="fresh",
                    content="cobalt supply squeeze report",
                    last_activity_at=_days_ago(0),
                ),
            ],
        )

        results = hybrid_search(db, "cobalt")

        assert [r["doc_key"] for r in results] == ["fresh", "stale"]


class TestFilters:
    def test_scope_and_source_filters(self, db):
        upsert_documents(
            db,
            [
                _doc(doc_key="ops-doc", scope="ops", content="cobalt runbook"),
                _doc(doc_key="trade-doc", scope="trading", content="cobalt trade thesis"),
                _doc(source="newsfeed", doc_key="post", scope="research", content="cobalt news"),
            ],
        )

        by_scope = hybrid_search(db, "cobalt", scopes=["ops"])
        by_source = hybrid_search(db, "cobalt", sources=["newsfeed"])

        assert [r["doc_key"] for r in by_scope] == ["ops-doc"]
        assert [r["doc_key"] for r in by_source] == ["post"]

    def test_scoped_search_survives_candidate_pool_flooded_by_other_scope(self, db):
        flood = [
            _doc(doc_key=f"flood-{ix}", scope="trading", content="cobalt trade note")
            for ix in range(CANDIDATE_POOL)
        ]
        flood.append(_doc(doc_key="runbook", scope="ops", content="cobalt ops runbook"))
        upsert_documents(db, flood)

        results = hybrid_search(db, "cobalt", scopes=["ops"])

        assert [r["doc_key"] for r in results] == ["runbook"]


class TestDedupAndNeighbors:
    def test_one_result_per_doc_with_adjacent_chunks_attached(self, db):
        upsert_documents(
            db,
            [
                _doc(doc_key="eval", chunk_ix=0, content="milestone one validation"),
                _doc(doc_key="eval", chunk_ix=1, content="cobalt cobalt edge decision"),
                _doc(doc_key="eval", chunk_ix=2, content="cobalt sizing"),
            ],
        )

        results = hybrid_search(db, "cobalt")

        assert len(results) == 1  # deduped to one row per (source, doc_key)
        winner = results[0]
        assert winner["chunk_ix"] == 1  # best-matching chunk kept
        assert [n["chunk_ix"] for n in winner["neighbors"]] == [0, 2]
        assert winner["neighbors"][0]["content"] == "milestone one validation"

    def test_with_neighbors_false_skips_neighbor_queries(self, db):
        upsert_documents(
            db,
            [
                _doc(doc_key="eval", chunk_ix=0, content="milestone one validation"),
                _doc(doc_key="eval", chunk_ix=1, content="cobalt cobalt edge decision"),
                _doc(doc_key="eval", chunk_ix=2, content="cobalt sizing"),
            ],
        )
        recording = _RecordingDb(db)

        results = hybrid_search(recording, "cobalt", with_neighbors=False)

        assert len(results) == 1
        assert "neighbors" not in results[0]
        assert [s for s in recording.statements if "chunk_ix BETWEEN" in s] == []


class TestPerSourceCap:
    def test_flooded_source_is_capped_but_others_survive(self, db):
        docs = [
            _doc(source="newsfeed", doc_key=f"post-{ix}", content=f"cobalt update {ix}")
            for ix in range(MAX_PER_SOURCE + 2)
        ]
        docs.append(_doc(doc_key="methodology", content="cobalt methodology"))
        upsert_documents(db, docs)

        results = hybrid_search(db, "cobalt")

        sources = [r["source"] for r in results]
        assert sources.count("newsfeed") == MAX_PER_SOURCE
        assert "docs" in sources

    def test_limit_caps_total_results(self, db):
        upsert_documents(
            db,
            [_doc(doc_key=f"d{ix}", content="cobalt note") for ix in range(3)],
        )

        assert len(hybrid_search(db, "cobalt", limit=2)) == 2
