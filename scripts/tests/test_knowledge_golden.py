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
from knowledge.eval_golden import DEFAULT_GOLDEN_PATH, run_golden  # noqa: E402
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
        assert outcome["matched"] == {
            "source": "journal",
            "doc_key": "ALAB|2026-03-02|Long Call - LEAP|1",
            "pattern": "ALAB",
        }

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

        assert summary == {"overall_hit_at_5": 0.0, "per_question": []}

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

        assert set(round_tripped) == {"overall_hit_at_5", "per_question"}
        outcome = round_tripped["per_question"][0]
        assert set(outcome) == {"question", "hit", "matched", "results"}
        assert outcome["results"] == [
            {"source": "docs", "doc_key": "runbook.md", "score": pytest.approx(outcome["results"][0]["score"])}
        ]
        assert set(outcome["results"][0]) == {"source", "doc_key", "score"}


class TestGoldenSetFile:
    def test_shipped_golden_set_is_well_formed(self):
        golden = json.loads(DEFAULT_GOLDEN_PATH.read_text(encoding="utf-8"))

        assert golden["draft"] is True
        assert golden["note"]
        questions = golden["questions"]
        assert len(questions) == 24
        for entry in questions:
            assert entry["question"].strip()
            assert entry["expected"]
            for want in entry["expected"]:
                assert want["source"] in {"journal", "evals", "docs", "newsfeed", "incidents"}
                re.compile(want["doc_key_pattern"])  # must be a valid regex
            if "scopes" in entry:
                assert set(entry["scopes"]) <= {"trading", "research", "ops"}


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
        assert "golden" in (result.stdout + result.stderr).lower()
