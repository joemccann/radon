"""Golden-set eval harness (Phase 1) — hit matching, miss counting, scope
pass-through, embedder wiring, JSON output shape.

Pure tests: synthetic golden entries against a LOCAL libsql :memory: database
seeded via store.upsert_documents, real 0028 migration applied (same fixture
pattern as test_knowledge_store/retrieve). No network — the CLI's production
get_db() path is deliberately untested here.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import libsql_experimental as libsql
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from knowledge import embed as embed_mod  # noqa: E402
from knowledge import eval_golden as eval_golden_mod  # noqa: E402
from knowledge.eval_golden import (  # noqa: E402
    DEFAULT_GOLDEN_PATH,
    DEFAULT_MAX_DROP,
    baseline_payload,
    compare_baseline,
    hit_at_k,
    mean_reciprocal_rank,
    ndcg_at_k,
    recall_at_k,
    relevant_labels,
    run_golden,
    validate_golden_set,
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
    )
    base.update(overrides)
    return KnowledgeDoc(**base)


def _entry(question: str, expected: list[dict], **extra) -> dict:
    return {"question": question, "expected": expected, **extra}


class TestHitMatching:
    def test_pattern_match_against_doc_key_is_a_hit(self, db):
        upsert_documents(
            db,
            [_doc(source="journal", scope="trading", doc_key="ALAB|2026-03-02|Long Call - LEAP|1",
                  content="ALAB LEAP thesis, IV underpriced vs realized")],
        )
        golden = [_entry("ALAB LEAP thesis", [{"source": "journal", "doc_key_pattern": "ALAB"}])]

        summary = run_golden(db, golden)

        assert summary["overall_hit_at_5"] == 1.0
        outcome = summary["per_question"][0]
        assert outcome["hit"] is True
        assert outcome["matched"]["source"] == "journal"
        assert outcome["matched"]["doc_key"] == "ALAB|2026-03-02|Long Call - LEAP|1"
        assert outcome["matched"]["pattern"] == "ALAB"
        assert outcome["matched"]["grade"] == 3
        assert outcome["matched"]["rank"] == 1

    def test_source_mismatch_is_a_miss_even_when_pattern_matches(self, db):
        upsert_documents(
            db,
            [_doc(source="evals", scope="trading", doc_key="alab-evaluation",
                  content="ALAB LEAP thesis, IV underpriced vs realized")],
        )
        golden = [_entry("ALAB LEAP thesis", [{"source": "journal", "doc_key_pattern": "(?i)alab"}])]

        summary = run_golden(db, golden)

        assert summary["per_question"][0]["hit"] is False
        assert summary["per_question"][0]["matched"] is None

    def test_pattern_is_regex_not_plain_substring(self, db):
        upsert_documents(
            db,
            [
                _doc(doc_key="docs/evaluation.md", content="seven milestone evaluation pipeline"),
                _doc(doc_key="reports/hims-evaluation.html", content="milestone evaluation of HIMS"),
            ],
        )
        golden = [
            _entry(
                "milestone evaluation",
                [{"source": "docs", "doc_key_pattern": r"^docs/evaluation\.md$"}],
            )
        ]

        summary = run_golden(db, golden)

        assert summary["per_question"][0]["matched"]["doc_key"] == "docs/evaluation.md"

    def test_any_of_multiple_expected_patterns_counts(self, db):
        upsert_documents(
            db,
            [_doc(source="evals", scope="trading", doc_key="goog-evaluation-report",
                  content="GOOG dark pool accumulation evaluation")],
        )
        golden = [
            _entry(
                "GOOG dark pool",
                [
                    {"source": "journal", "doc_key_pattern": "GOOG"},
                    {"source": "evals", "doc_key_pattern": "goog-evaluation"},
                ],
            )
        ]

        assert run_golden(db, golden)["overall_hit_at_5"] == 1.0


class TestMissCounting:
    def test_overall_rate_mixes_hits_and_misses(self, db):
        upsert_documents(db, [_doc(doc_key="runbook.md", content="relay reconnect runbook")])
        golden = [
            _entry("relay reconnect", [{"source": "docs", "doc_key_pattern": "runbook"}]),
            _entry("zzzunknowntoken", [{"source": "docs", "doc_key_pattern": "runbook"}]),
        ]

        summary = run_golden(db, golden)

        assert summary["overall_hit_at_5"] == 0.5
        assert [outcome["hit"] for outcome in summary["per_question"]] == [True, False]

    def test_empty_golden_returns_zero_rate(self, db):
        summary = run_golden(db, [])

        assert summary["overall_hit_at_5"] == 0.0
        assert summary["per_question"] == []
        assert summary["modes"]["hybrid"]["hit_at_5"] == 0.0

    def test_miss_when_expected_doc_ranks_below_limit(self, db):
        docs = [
            _doc(doc_key=f"docs/note-{ix}.md", content="cobalt cobalt cobalt note")
            for ix in range(3)
        ]
        docs.append(_doc(source="newsfeed", scope="research", doc_key="post-1",
                         content="cobalt mention"))
        upsert_documents(db, docs)
        golden = [_entry("cobalt", [{"source": "newsfeed", "doc_key_pattern": "post-1"}])]

        summary = run_golden(db, golden, limit=1)

        assert summary["per_question"][0]["hit"] is False
        assert len(summary["per_question"][0]["results"]) == 1


class TestScopePassThrough:
    def _seed_two_scopes(self, db):
        upsert_documents(
            db,
            [
                _doc(doc_key="runbook.md", scope="ops", content="cobalt ops runbook"),
                _doc(doc_key="thesis.md", scope="trading", content="cobalt trade thesis"),
            ],
        )

    def test_scoped_entry_only_searches_its_scopes(self, db):
        self._seed_two_scopes(db)
        golden = [
            _entry(
                "cobalt",
                [{"source": "docs", "doc_key_pattern": "thesis"}],
                scopes=["ops"],
            )
        ]

        summary = run_golden(db, golden)

        assert summary["per_question"][0]["hit"] is False  # thesis scoped out
        result_keys = [row["doc_key"] for row in summary["per_question"][0]["results"]]
        assert result_keys == ["runbook.md"]

    def test_unscoped_entry_searches_everything(self, db):
        self._seed_two_scopes(db)
        golden = [_entry("cobalt", [{"source": "docs", "doc_key_pattern": "thesis"}])]

        assert run_golden(db, golden)["per_question"][0]["hit"] is True


class TestEmbedderWiring:
    def _seed_paraphrase_doc(self, db):
        upsert_documents(
            db,
            [_doc(doc_key="restore.md", content="restore hangs while the snapshot finalizes",
                  embedding=_unit_vector(0))],
        )

    def test_query_embedder_enables_paraphrase_hit(self, db):
        self._seed_paraphrase_doc(db)
        golden = [_entry("checkpoint stalls", [{"source": "docs", "doc_key_pattern": "restore"}])]

        summary = run_golden(db, golden, query_embedder=lambda text: _unit_vector(0))

        assert summary["per_question"][0]["hit"] is True

    def test_without_embedder_paraphrase_query_degrades_to_fts_miss(self, db):
        self._seed_paraphrase_doc(db)
        golden = [_entry("checkpoint stalls", [{"source": "docs", "doc_key_pattern": "restore"}])]

        assert run_golden(db, golden)["per_question"][0]["hit"] is False


class TestLoadQueryEmbedder:
    """The CLI wires get_embedder()'s BATCH callable (Sequence[str] ->
    list[list[float]]) into a single-query embedder. Passing the bare query
    string would iterate it per-character (list("query")) and crash _as_vector."""

    def test_query_is_sent_as_a_batch_of_one_and_unwrapped(self, monkeypatch):
        received = []

        def batch_embed(texts):
            received.append(list(texts))
            return [[0.5] * EMBEDDING_DIM for _ in list(texts)]

        monkeypatch.setenv("RADON_KB_EMBED_BACKEND", "local")
        monkeypatch.setattr(embed_mod, "get_embedder", lambda: batch_embed)

        query_embedder = eval_golden_mod._load_query_embedder()
        vector = query_embedder("why did the relay farm down")

        assert received == [["why did the relay farm down"]]
        assert vector == [0.5] * EMBEDDING_DIM


class TestOutputShape:
    def test_summary_is_json_serializable_with_expected_keys(self, db):
        upsert_documents(db, [_doc(doc_key="runbook.md", content="relay reconnect runbook")])
        golden = {
            "draft": True,
            "note": "synthetic",
            "questions": [
                _entry("relay reconnect", [{"source": "docs", "doc_key_pattern": "runbook"}])
            ],
        }

        summary = run_golden(db, golden)
        round_tripped = json.loads(json.dumps(summary))

        assert {
            "overall_hit_at_5",
            "per_question",
            "modes",
            "backend_used",
            "fallback",
        } <= set(round_tripped)
        outcome = round_tripped["per_question"][0]
        assert {"question", "hit", "matched", "results", "modes", "relevant"} <= set(outcome)
        assert outcome["results"] == [
            {"source": "docs", "doc_key": "runbook.md", "score": pytest.approx(outcome["results"][0]["score"])}
        ]
        assert set(outcome["results"][0]) == {"source", "doc_key", "score"}
        assert len(outcome["results"]) <= 10


class TestGoldenSetFile:
    def test_shipped_golden_set_is_well_formed(self):
        from knowledge.eval_golden import validate_golden_set

        golden = json.loads(DEFAULT_GOLDEN_PATH.read_text(encoding="utf-8"))

        assert golden["draft"] is True
        assert golden["version"]
        assert golden["note"]
        questions = golden["questions"]
        assert len(questions) >= 100
        errors = validate_golden_set(golden)
        assert errors == [], errors
        ids = [entry["id"] for entry in questions]
        assert len(ids) == len(set(ids))
        categories = {entry["category"] for entry in questions}
        sources = {want["source"] for entry in questions for want in entry["relevant"]}
        assert categories == {"exact_term", "paraphrase", "numeric", "recent", "cross_source"}
        assert sources == {"journal", "evals", "docs", "newsfeed", "incidents"}
        assert any(
            entry.get("scopes") == ["ops"]
            and any(want["doc_key_pattern"] == r"^tasks/lessons\.md$" for want in entry["relevant"])
            for entry in questions
        )
        assert any(
            any(want["source"] == "docs" and "docs/" in want["doc_key_pattern"] for want in entry["relevant"])
            and entry.get("scopes") != ["ops"]
            for entry in questions
        )
        for entry in questions:
            assert entry["question"].strip()
            assert entry["relevant"]
            for want in entry["relevant"]:
                assert want["source"] in {"journal", "evals", "docs", "newsfeed", "incidents"}
                assert want["grade"] in {1, 2, 3}
                re.compile(want["doc_key_pattern"])
            if "scopes" in entry:
                assert set(entry["scopes"]) <= {"trading", "research", "ops"}

    def test_shipped_golden_set_has_no_journal_trade_ids(self):
        golden = json.loads(DEFAULT_GOLDEN_PATH.read_text(encoding="utf-8"))

        journal_patterns = [
            label["doc_key_pattern"]
            for question in golden["questions"]
            for label in question["relevant"]
            if label["source"] == "journal"
        ]

        assert not any(re.search(r"\d{6,}", pattern) for pattern in journal_patterns)


class TestCliInvocation:
    """The CLI must run as a direct script (systemd/operator convention shared
    with ingest.py), not only as `-m knowledge.eval_golden`."""

    def test_direct_script_help_exits_zero(self):
        import subprocess

        result = subprocess.run(
            [sys.executable, str(_SCRIPTS_DIR / "knowledge" / "eval_golden.py"), "--help"],
            capture_output=True,
            text=True,
            cwd=str(_PROJECT_ROOT),
            timeout=60,
        )

        assert result.returncode == 0, result.stderr
        help_text = result.stdout + result.stderr
        assert "golden" in help_text.lower()
        for flag in (
            "--mode",
            "--baseline",
            "--max-drop",
            "--write-baseline",
            "--write-results",
            "--strict-backend",
            "--backend",
        ):
            assert flag in help_text
        for mode in ("hybrid", "vector", "keyword", "all"):
            assert mode in help_text


class TestMetricMath:
    """Hand-computed metric values. No DB."""

    def test_hit_at_k(self):
        ranks = [1, 3, None, 6]
        assert hit_at_k(ranks, 1) == pytest.approx(0.25)
        assert hit_at_k(ranks, 5) == pytest.approx(0.5)
        assert hit_at_k([], 5) == 0.0

    def test_mrr(self):
        ranks = [1, 2, None]
        assert mean_reciprocal_rank(ranks) == pytest.approx((1.0 + 0.5 + 0.0) / 3)
        assert mean_reciprocal_rank([]) == 0.0

    def test_recall_at_k(self):
        labels = [
            [{"source": "docs", "doc_key_pattern": "a", "grade": 3},
             {"source": "docs", "doc_key_pattern": "b", "grade": 2}],
            [{"source": "docs", "doc_key_pattern": "a", "grade": 3},
             {"source": "newsfeed", "doc_key_pattern": "z", "grade": 1}],
        ]
        retrieved = [
            [{"source": "docs", "doc_key": "a.md"}, {"source": "docs", "doc_key": "b.md"}],
            [{"source": "docs", "doc_key": "a.md"}, {"source": "docs", "doc_key": "other.md"}],
        ]
        assert recall_at_k(retrieved, labels, 10) == pytest.approx((1.0 + 0.5) / 2)

    def test_ndcg_at_k(self):
        import math

        labels = [[{"source": "docs", "doc_key_pattern": "hit", "grade": 3},
                   {"source": "docs", "doc_key_pattern": "side", "grade": 1}]]
        retrieved = [[
            {"source": "docs", "doc_key": "noise.md"},
            {"source": "docs", "doc_key": "hit.md"},
            {"source": "docs", "doc_key": "side.md"},
        ]]
        dcg = 0.0 / math.log2(2) + 3.0 / math.log2(3) + 1.0 / math.log2(4)
        idcg = 3.0 / math.log2(2) + 1.0 / math.log2(3)
        assert ndcg_at_k(retrieved, labels, 10) == pytest.approx(dcg / idcg)


class TestRelevantCompat:
    def test_legacy_expected_is_grade_3(self):
        entry = {"expected": [{"source": "docs", "doc_key_pattern": "runbook"}]}
        labels = relevant_labels(entry)
        assert labels == [{"source": "docs", "doc_key_pattern": "runbook", "grade": 3}]

    def test_relevant_wins_over_expected(self):
        entry = {
            "expected": [{"source": "docs", "doc_key_pattern": "old"}],
            "relevant": [{"source": "evals", "doc_key_pattern": "new", "grade": 2}],
        }
        assert relevant_labels(entry)[0]["source"] == "evals"
        assert relevant_labels(entry)[0]["grade"] == 2


class TestModeSwitching:
    def _seed(self, db):
        upsert_documents(
            db,
            [
                _doc(doc_key="fts-only.md", content="cobalt runbook reconnect",
                     embedding=_unit_vector(1)),
                _doc(doc_key="vector-only.md", content="unrelated body text here",
                     embedding=_unit_vector(0)),
            ],
        )

    def test_keyword_hits_fts_and_misses_paraphrase(self, db):
        self._seed(db)
        golden = [_entry("cobalt reconnect", [{"source": "docs", "doc_key_pattern": "fts-only"}])]
        summary = run_golden(db, golden, modes=("keyword", "vector"), query_embedder=lambda t: _unit_vector(0))
        assert summary["per_question"][0]["modes"]["keyword"]["hit"] is True
        assert summary["per_question"][0]["modes"]["keyword"]["results"][0]["doc_key"] == "fts-only.md"
        assert summary["per_question"][0]["modes"]["vector"]["results"][0]["doc_key"] == "vector-only.md"

    def test_hybrid_is_the_default_mode(self, db):
        upsert_documents(db, [_doc(doc_key="runbook.md", content="relay reconnect runbook")])
        summary = run_golden(db, [_entry("relay reconnect", [{"source": "docs", "doc_key_pattern": "runbook"}])])
        assert "hybrid" in summary["modes"]
        assert "keyword" not in summary["modes"]

    def test_keeps_top_10_results(self, db):
        upsert_documents(
            db,
            [_doc(doc_key=f"docs/note-{ix}.md", content="cobalt cobalt note") for ix in range(12)],
        )
        golden = [_entry("cobalt", [{"source": "docs", "doc_key_pattern": "note-0"}])]
        summary = run_golden(db, golden, limit=10, modes=("keyword",))
        assert len(summary["per_question"][0]["results"]) == 10
        assert len(summary["per_question"][0]["modes"]["keyword"]["results"]) == 10


class TestBaselineComparison:
    def test_drop_within_margin_is_ok(self):
        current = {"modes": {"hybrid": {"hit_at_5": 0.80, "mrr": 0.70}}}
        baseline = {"modes": {"hybrid": {"hit_at_5": 0.82, "mrr": 0.72}}}
        assert compare_baseline(current, baseline, max_drop=0.03) == []

    def test_drop_past_margin_is_a_regression(self):
        current = {"modes": {"hybrid": {"hit_at_5": 0.70, "mrr": 0.70}}}
        baseline = {"modes": {"hybrid": {"hit_at_5": 0.80, "mrr": 0.70}}}
        lines = compare_baseline(current, baseline, max_drop=DEFAULT_MAX_DROP)
        assert len(lines) == 1
        assert "hybrid.hit_at_5" in lines[0]

    def test_placeholder_baseline_is_a_regression(self):
        lines = compare_baseline({"modes": {}}, {"placeholder": True})
        assert lines and "placeholder" in lines[0]

    def test_write_baseline_is_compact(self):
        payload = baseline_payload({
            "version": "2.0",
            "backend_requested": "nvidia",
            "backend_used": "local",
            "fallback": False,
            "modes": {
                "hybrid": {
                    "hit_at_5": 0.8,
                    "mrr": 0.7,
                    "hit_at_1": 0.5,
                    "recall_at_10": 0.9,
                    "ndcg_at_10": 0.6,
                    "by_category": {},
                }
            },
        })
        assert payload["placeholder"] is False
        assert payload["backend_requested"] == "nvidia"
        assert payload["backend_used"] == "local"
        assert set(payload["modes"]["hybrid"]) == {
            "hit_at_5", "mrr", "hit_at_1", "recall_at_10", "ndcg_at_10",
        }

    def test_tracking_embedder_backend_is_read_after_call(self):
        tracker = eval_golden_mod._TrackingEmbedder(lambda text: _unit_vector(0), "nvidia")
        assert tracker.backend_used == "fts"
        tracker("relay reconnect")
        assert tracker.backend_used == "local"
        assert tracker.fallback is True


class TestSchemaValidation:
    def test_validate_rejects_small_set(self):
        errors = validate_golden_set({"draft": True, "version": "2.0", "questions": []})
        assert any("100" in err for err in errors)

    def test_shipped_candidates_file_is_gone_after_promotion(self):
        path = DEFAULT_GOLDEN_PATH.with_name("golden_set_candidates.json")
        assert not path.exists(), (
            "Turso-validated candidates were promoted or dropped; "
            "do not ship an empty review file"
        )


class TestBackendRecording:
    def test_fallback_flag_when_nvidia_request_returns_384(self, db, monkeypatch):
        upsert_documents(db, [_doc(doc_key="runbook.md", content="relay reconnect runbook")])
        summary = run_golden(
            db,
            [_entry("relay reconnect", [{"source": "docs", "doc_key_pattern": "runbook"}])],
            query_embedder=lambda text: _unit_vector(0),
            backend_requested="nvidia",
            backend_used="local",
            fallback=True,
        )
        assert summary["fallback"] is True
        assert summary["backend_used"] == "local"
        assert summary["backend_requested"] == "nvidia"
