"""Knowledge ingest pipeline (Phase 1) — distill, embed, ingest orchestration.

Runs against a LOCAL libsql :memory: database with the real 0028 migration
(same harness as test_knowledge_store.py). Connectors are FAKE modules
(SimpleNamespace with SOURCE/SCOPE/fetch) — scripts.knowledge.sources is
never imported. No network, no model downloads, no real Cerebras calls:
requests is monkeypatched in distill tests and the distill/embed seams are
monkeypatched on the ingest module in ingest tests.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import libsql_experimental as libsql
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from knowledge import distill as distill_mod  # noqa: E402
from knowledge import embed as embed_mod  # noqa: E402
from knowledge import ingest as ingest_mod  # noqa: E402
from knowledge.schema import KnowledgeDoc  # noqa: E402

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


# ── distill ──────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _chat_response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


_GOOD_DISTILLATION = json.dumps(
    {
        "summary": "What fixed the relay farm-down? A bounded reconnect ladder.",
        "tickers": ["spy"],
    }
)


class TestDistill:
    @pytest.fixture(autouse=True)
    def _key(self, monkeypatch):
        monkeypatch.setattr(distill_mod, "_load_key", lambda: "test-key")

    def test_success_returns_summary_and_uppercased_tickers(self, monkeypatch):
        calls = []

        def fake_post(url, **kwargs):
            calls.append((url, kwargs))
            return _FakeResponse(200, _chat_response(_GOOD_DISTILLATION))

        monkeypatch.setattr(distill_mod.requests, "post", fake_post)

        result = distill_mod.distill("Relay", "the relay farm-down fix")

        assert result == {
            "summary": "What fixed the relay farm-down? A bounded reconnect ladder.",
            "tickers": ["SPY"],
        }
        assert len(calls) == 1
        assert calls[0][0] == distill_mod.CEREBRAS_CHAT_COMPLETIONS_URL

    def test_code_fenced_json_is_parsed(self, monkeypatch):
        fenced = f"```json\n{_GOOD_DISTILLATION}\n```"
        monkeypatch.setattr(
            distill_mod.requests, "post",
            lambda url, **kw: _FakeResponse(200, _chat_response(fenced)),
        )

        result = distill_mod.distill("Relay", "content")

        assert result is not None
        assert result["tickers"] == ["SPY"]

    def test_persistent_failure_returns_none_after_one_retry(self, monkeypatch):
        calls = []

        def always_fails(url, **kwargs):
            calls.append(url)
            raise distill_mod.requests.RequestException("connection refused")

        monkeypatch.setattr(distill_mod.requests, "post", always_fails)

        assert distill_mod.distill("Relay", "content") is None
        assert len(calls) == 2  # first attempt + one retry

    def test_retry_once_then_success(self, monkeypatch):
        responses = [
            _FakeResponse(500, {}),
            _FakeResponse(200, _chat_response(_GOOD_DISTILLATION)),
        ]
        calls = []

        def flaky_post(url, **kwargs):
            calls.append(url)
            return responses[len(calls) - 1]

        monkeypatch.setattr(distill_mod.requests, "post", flaky_post)

        result = distill_mod.distill("Relay", "content")

        assert result is not None
        assert len(calls) == 2

    def test_unparseable_content_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            distill_mod.requests, "post",
            lambda url, **kw: _FakeResponse(200, _chat_response("not json at all")),
        )

        assert distill_mod.distill("Relay", "content") is None

    def test_no_key_returns_none_without_network(self, monkeypatch):
        monkeypatch.setattr(distill_mod, "_load_key", lambda: None)

        def must_not_be_called(url, **kwargs):
            raise AssertionError("network call without an API key")

        monkeypatch.setattr(distill_mod.requests, "post", must_not_be_called)

        assert distill_mod.distill("Relay", "content") is None


# ── embed ────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_embedder_cache(monkeypatch):
    monkeypatch.delenv(embed_mod.DISABLE_ENV, raising=False)
    monkeypatch.setattr(embed_mod, "_embedder", embed_mod._UNSET)


class TestGetEmbedder:
    def test_disabled_env_returns_none(self, monkeypatch):
        monkeypatch.setenv(embed_mod.DISABLE_ENV, "1")

        assert embed_mod.get_embedder() is None

    def test_import_failure_returns_none_and_logs(
        self, monkeypatch, capsys, fresh_embedder_cache
    ):
        def broken_import():
            raise ImportError("No module named fastembed")

        monkeypatch.setattr(embed_mod, "_import_text_embedding", broken_import)

        assert embed_mod.get_embedder() is None
        assert "fastembed" in capsys.readouterr().err

    def test_failure_is_cached_not_retried(self, monkeypatch, fresh_embedder_cache):
        attempts = []

        def broken_import():
            attempts.append(1)
            raise ImportError("No module named fastembed")

        monkeypatch.setattr(embed_mod, "_import_text_embedding", broken_import)

        assert embed_mod.get_embedder() is None
        assert embed_mod.get_embedder() is None
        assert len(attempts) == 1


class TestEmbeddingText:
    def test_prefers_summary_over_content(self):
        assert embed_mod.embedding_text("Title", "the summary", "the content") == (
            "Title\nthe summary"
        )

    def test_falls_back_to_content_head_when_no_summary(self):
        text = embed_mod.embedding_text("Title", None, "x" * 5000)

        assert text.startswith("Title\n")
        assert len(text) == len("Title\n") + embed_mod.CONTENT_HEAD_CHARS

    def test_no_title_uses_bare_text(self):
        assert embed_mod.embedding_text(None, "the summary", "content") == "the summary"


# ── ingest ───────────────────────────────────────────────────────────


def _chunk(doc_key: str = "doc-a", chunk_ix: int = 0, content: str = "alpha content",
           **overrides) -> KnowledgeDoc:
    base = dict(
        source="fake",
        scope="ops",
        doc_key=doc_key,
        chunk_ix=chunk_ix,
        title=f"Title {doc_key}",
        summary=None,
        content=content,
        metadata=None,
        last_activity_at=_days_ago(1),
    )
    base.update(overrides)
    return KnowledgeDoc(**base)


def _module(docs) -> SimpleNamespace:
    return SimpleNamespace(SOURCE="fake", SCOPE="ops", fetch=lambda db: iter(docs))


@pytest.fixture
def fake_distill(monkeypatch):
    calls = []

    def distill_stub(title, content, **kwargs):
        calls.append((title, content))
        return {"summary": f"distilled: {content}", "tickers": []}

    monkeypatch.setattr(ingest_mod, "distill", distill_stub)
    return calls


@pytest.fixture
def forbid_distill(monkeypatch):
    def must_not_distill(title, content, **kwargs):
        raise AssertionError(f"unexpected re-distillation of {title!r}")

    monkeypatch.setattr(ingest_mod, "distill", must_not_distill)


@pytest.fixture
def fake_embedder(monkeypatch):
    batches = []

    def embed_batch(texts):
        batches.append(list(texts))
        return [[0.25] * EMBEDDING_DIM for _ in texts]

    monkeypatch.setattr(ingest_mod, "get_embedder", lambda: embed_batch)
    return batches


def _rows(db):
    return db.execute(
        "SELECT doc_key, chunk_ix, content, summary, embedding FROM knowledge "
        "ORDER BY doc_key, chunk_ix"
    ).fetchall()


class TestIngestSource:
    def test_first_ingest_distills_embeds_and_upserts(
        self, db, fake_distill, fake_embedder, capsys
    ):
        module = _module([_chunk("doc-a"), _chunk("doc-b", content="beta content")])

        result = ingest_mod.ingest_source(db, module)

        assert result["fetched"] == 2
        assert result["skipped_docs"] == 0
        assert result["distilled"] == 2
        assert result["distill_failed"] == 0
        assert result["embedded"] == 2
        assert result["inserted"] == 2
        assert result["deleted"] == 0
        rows = _rows(db)
        assert [row[3] for row in rows] == [
            "distilled: alpha content",
            "distilled: beta content",
        ]
        for row in rows:
            assert isinstance(row[4], bytes)
            assert len(row[4]) == EMBEDDING_DIM * 4
        assert capsys.readouterr().out == ""  # progress goes to stderr only

    def test_prefilter_skips_unchanged_docs_with_summary(
        self, db, fake_embedder, monkeypatch
    ):
        monkeypatch.setattr(
            ingest_mod, "distill",
            lambda title, content, **kw: {"summary": "seeded summary", "tickers": []},
        )
        ingest_mod.ingest_source(db, _module([_chunk("doc-a"), _chunk("doc-b")]))

        def must_not_distill(title, content, **kwargs):
            raise AssertionError("re-distilled an unchanged doc")

        monkeypatch.setattr(ingest_mod, "distill", must_not_distill)

        result = ingest_mod.ingest_source(
            db, _module([_chunk("doc-a"), _chunk("doc-b")])
        )

        assert result["skipped_docs"] == 2
        assert result["distilled"] == 0
        assert result["embedded"] == 0
        assert result["inserted"] == 0
        assert result["updated"] == 0

    def test_changed_content_is_redistilled(self, db, fake_distill, fake_embedder):
        ingest_mod.ingest_source(db, _module([_chunk("doc-a"), _chunk("doc-b")]))
        fake_distill.clear()

        result = ingest_mod.ingest_source(
            db,
            _module([_chunk("doc-a", content="alpha content v2"), _chunk("doc-b")]),
        )

        assert result["skipped_docs"] == 1  # doc-b untouched
        assert [call[1] for call in fake_distill] == ["alpha content v2"]
        rows = _rows(db)
        assert rows[0][3] == "distilled: alpha content v2"

    def test_doc_without_summary_is_retried_next_run(
        self, db, fake_embedder, monkeypatch
    ):
        monkeypatch.setattr(ingest_mod, "distill", lambda title, content, **kw: None)
        first = ingest_mod.ingest_source(db, _module([_chunk("doc-a")]))
        assert first["distill_failed"] == 1
        assert _rows(db)[0][3] is None

        monkeypatch.setattr(
            ingest_mod, "distill",
            lambda title, content, **kw: {"summary": "recovered", "tickers": []},
        )
        second = ingest_mod.ingest_source(db, _module([_chunk("doc-a")]))

        assert second["skipped_docs"] == 0  # no summary stored -> not skipped
        assert second["distilled"] == 1
        assert _rows(db)[0][3] == "recovered"

    def test_no_distill_ingests_raw(self, db, forbid_distill, fake_embedder):
        result = ingest_mod.ingest_source(
            db, _module([_chunk("doc-a")]), distill_enabled=False
        )

        assert result["distilled"] == 0
        assert result["inserted"] == 1
        assert _rows(db)[0][3] is None

    def test_no_embed_leaves_embedding_null(self, db, fake_distill, monkeypatch):
        def must_not_embed():
            raise AssertionError("get_embedder called with embed disabled")

        monkeypatch.setattr(ingest_mod, "get_embedder", must_not_embed)

        result = ingest_mod.ingest_source(
            db, _module([_chunk("doc-a")]), embed_enabled=False
        )

        assert result["embedded"] == 0
        assert _rows(db)[0][4] is None

    def test_embedder_unavailable_degrades_to_no_vectors(
        self, db, fake_distill, monkeypatch
    ):
        monkeypatch.setattr(ingest_mod, "get_embedder", lambda: None)

        result = ingest_mod.ingest_source(db, _module([_chunk("doc-a")]))

        assert result["embedded"] == 0
        assert result["inserted"] == 1
        assert _rows(db)[0][4] is None

    def test_distilled_tickers_merge_into_metadata(self, db, fake_embedder, monkeypatch):
        monkeypatch.setattr(
            ingest_mod, "distill",
            lambda title, content, **kw: {"summary": "s", "tickers": ["EWY"]},
        )

        ingest_mod.ingest_source(db, _module([_chunk("doc-a", metadata={"kind": "x"})]))

        metadata = json.loads(
            db.execute("SELECT metadata FROM knowledge").fetchone()[0]
        )
        assert metadata == {"kind": "x", "tickers": ["EWY"]}

    def test_title_only_change_is_resynced(self, db, fake_distill, fake_embedder):
        ingest_mod.ingest_source(db, _module([_chunk("doc-a")]))

        result = ingest_mod.ingest_source(db, _module([_chunk("doc-a", title="Renamed")]))

        assert result["skipped_docs"] == 0
        assert result["updated"] == 1
        assert db.execute("SELECT title FROM knowledge").fetchone()[0] == "Renamed"

    def test_metadata_only_change_is_resynced(self, db, fake_distill, fake_embedder):
        ingest_mod.ingest_source(db, _module([_chunk("doc-a", metadata={"tags": ["A"]})]))

        result = ingest_mod.ingest_source(
            db, _module([_chunk("doc-a", metadata={"tags": ["A", "B"]})])
        )

        assert result["skipped_docs"] == 0
        metadata = json.loads(db.execute("SELECT metadata FROM knowledge").fetchone()[0])
        assert metadata["tags"] == ["A", "B"]

    def test_distill_merged_tickers_do_not_force_resync(
        self, db, fake_embedder, monkeypatch
    ):
        monkeypatch.setattr(
            ingest_mod, "distill",
            lambda title, content, **kw: {"summary": "s", "tickers": ["EWY"]},
        )
        ingest_mod.ingest_source(db, _module([_chunk("doc-a", metadata={"kind": "x"})]))

        def must_not_distill(title, content, **kwargs):
            raise AssertionError("tickers merged by distill read as connector drift")

        monkeypatch.setattr(ingest_mod, "distill", must_not_distill)

        result = ingest_mod.ingest_source(
            db, _module([_chunk("doc-a", metadata={"kind": "x"})])
        )

        assert result["skipped_docs"] == 1
        metadata = json.loads(db.execute("SELECT metadata FROM knowledge").fetchone()[0])
        assert metadata == {"kind": "x", "tickers": ["EWY"]}

    def test_embedding_backfilled_once_embedder_recovers(self, db, monkeypatch):
        monkeypatch.setattr(
            ingest_mod, "distill",
            lambda title, content, **kw: {"summary": "stable summary", "tickers": []},
        )
        monkeypatch.setattr(ingest_mod, "get_embedder", lambda: None)
        first = ingest_mod.ingest_source(db, _module([_chunk("doc-a")]))
        assert first["embedded"] == 0
        assert _rows(db)[0][4] is None  # degraded: summary stored, vector missing

        def embed_batch(texts):
            return [[0.25] * EMBEDDING_DIM for _ in texts]

        monkeypatch.setattr(ingest_mod, "get_embedder", lambda: embed_batch)
        second = ingest_mod.ingest_source(db, _module([_chunk("doc-a")]))

        assert second["embedded"] == 1
        row = _rows(db)[0]
        assert row[3] == "stable summary"
        assert isinstance(row[4], bytes)
        assert len(row[4]) == EMBEDDING_DIM * 4

    def test_fts_only_rows_not_thrashed_while_embedder_stays_unavailable(
        self, db, monkeypatch
    ):
        monkeypatch.setattr(
            ingest_mod, "distill",
            lambda title, content, **kw: {"summary": "s", "tickers": []},
        )
        monkeypatch.setattr(ingest_mod, "get_embedder", lambda: None)
        ingest_mod.ingest_source(db, _module([_chunk("doc-a")]))

        def must_not_distill(title, content, **kwargs):
            raise AssertionError("re-distilled while embedder still unavailable")

        monkeypatch.setattr(ingest_mod, "distill", must_not_distill)

        result = ingest_mod.ingest_source(db, _module([_chunk("doc-a")]))

        assert result["skipped_docs"] == 1

    def test_vanished_doc_is_pruned(self, db, fake_distill, fake_embedder):
        ingest_mod.ingest_source(db, _module([_chunk("doc-a"), _chunk("doc-gone")]))

        result = ingest_mod.ingest_source(db, _module([_chunk("doc-a")]))

        assert result["deleted"] == 1
        assert [row[0] for row in _rows(db)] == ["doc-a"]

    def test_partial_chunk_change_reprocesses_whole_doc_without_pruning(
        self, db, fake_distill, fake_embedder
    ):
        ingest_mod.ingest_source(
            db,
            _module([_chunk("doc-a", 0, "chunk zero"), _chunk("doc-a", 1, "chunk one")]),
        )
        fake_distill.clear()

        result = ingest_mod.ingest_source(
            db,
            _module(
                [_chunk("doc-a", 0, "chunk zero v2"), _chunk("doc-a", 1, "chunk one")]
            ),
        )

        # Sibling changed -> whole doc reprocessed (both chunks distilled),
        # and the unchanged chunk survives (no false trailing-chunk prune).
        assert result["skipped_docs"] == 0
        assert len(fake_distill) == 2
        assert [(row[0], row[1]) for row in _rows(db)] == [("doc-a", 0), ("doc-a", 1)]

    def test_empty_fetch_never_prunes_existing_corpus(
        self, db, fake_distill, fake_embedder
    ):
        """The store is shared across hosts; a host whose local inputs are
        absent fetches nothing and must not wipe another host's corpus."""
        ingest_mod.ingest_source(db, _module([_chunk("doc-a"), _chunk("doc-b")]))

        result = ingest_mod.ingest_source(db, _module([]))

        assert result["deleted"] == 0
        assert [row[0] for row in _rows(db)] == ["doc-a", "doc-b"]

    def test_prunable_doc_keys_hook_scopes_pruning_authority(
        self, db, fake_distill, fake_embedder
    ):
        ingest_mod.ingest_source(
            db, _module([_chunk("db-doc"), _chunk("db-gone"), _chunk("local:doc")])
        )

        module = _module([_chunk("db-doc")])
        module.prunable_doc_keys = lambda keys: [
            key for key in keys if not key.startswith("local:")
        ]
        result = ingest_mod.ingest_source(db, module)

        assert result["deleted"] == 1  # db-gone pruned; local:doc not ours to prune
        assert [row[0] for row in _rows(db)] == ["db-doc", "local:doc"]

    def test_limit_bounds_docs_and_skips_vanished_pruning(
        self, db, fake_distill, fake_embedder
    ):
        ingest_mod.ingest_source(db, _module([_chunk("doc-a"), _chunk("doc-b")]))

        result = ingest_mod.ingest_source(
            db, _module([_chunk("doc-a", content="alpha v2"), _chunk("doc-b")]), limit=1
        )

        assert result["fetched"] == 1
        assert result["deleted"] == 0  # partial fetch must never prune
        assert [row[0] for row in _rows(db)] == ["doc-a", "doc-b"]


class TestSelectSources:
    def test_all_returns_every_source(self):
        catalog = {"a": object(), "b": object()}

        assert ingest_mod._select_sources(catalog, "all") == catalog

    def test_named_source_returns_singleton(self):
        module = object()

        assert ingest_mod._select_sources({"a": module}, "a") == {"a": module}

    def test_unknown_source_exits(self):
        with pytest.raises(SystemExit):
            ingest_mod._select_sources({"a": object()}, "nope")


class TestMainConnectionIsolation:
    def test_each_source_gets_a_fresh_connection(self, monkeypatch, db):
        # A single shared connection lets one source's dead Hrana stream
        # poison every source after it (incidents 404 "stream not found"
        # after newsfeed's long 502 run, 2026-07-19). main() must acquire
        # a fresh connection per source.
        import contextlib
        from types import SimpleNamespace

        import db.client as db_client
        import db.service_cycle as service_cycle_mod
        import knowledge.sources as sources_mod

        def _make_db():
            conn = libsql.connect(":memory:")
            conn.execute(_BOOTSTRAP_SQL)
            for stmt in _split_statements(_MIGRATION.read_text(encoding="utf-8")):
                conn.execute(stmt)
            conn.commit()
            return conn

        connections = []

        def counting_get_db():
            conn = _make_db()
            connections.append(conn)
            return conn

        def fake_fetch(_db):
            yield KnowledgeDoc(source="a", scope="ops", doc_key="k", content="body")

        fake_sources = {
            "a": SimpleNamespace(SOURCE="a", SCOPE="ops", fetch=fake_fetch),
            "b": SimpleNamespace(SOURCE="b", SCOPE="ops", fetch=fake_fetch),
        }

        monkeypatch.setattr(db_client, "get_db", counting_get_db)
        monkeypatch.setattr(
            service_cycle_mod, "service_cycle",
            lambda *a, **k: contextlib.nullcontext(),
        )
        monkeypatch.setattr(sources_mod, "ALL_SOURCES", fake_sources)

        assert ingest_mod.main(["--source", "all", "--no-distill", "--no-embed"]) == 0
        # At least one fresh connection per source (batched writes add more:
        # read + one per write batch + prune).
        assert len(connections) >= len(fake_sources)


class TestLoadExistingPagination:
    def test_existing_rows_are_read_in_bounded_batches(self, db, monkeypatch):
        # Same Turso HTTP-pipeline limit as the posts read: 1,200 stored
        # newsfeed rows with full content in one SELECT still 502'd after
        # the connector was paginated (2026-07-19). The pre-filter read
        # must paginate too.
        docs = [
            KnowledgeDoc(source="s", scope="ops", doc_key=f"d{i}", content=f"body {i}")
            for i in range(5)
        ]
        from knowledge.store import upsert_documents
        upsert_documents(db, docs)
        monkeypatch.setattr(ingest_mod, "_EXISTING_BATCH_ROWS", 2)

        class CountingDb:
            def __init__(self, inner):
                self.inner = inner
                self.calls = []

            def execute(self, sql, *args):
                if "FROM knowledge" in sql and "content" in sql:
                    self.calls.append(sql)
                return self.inner.execute(sql, *args)

        counting = CountingDb(db)
        existing = ingest_mod._load_existing(counting, "s")

        assert set(existing) == {f"d{i}" for i in range(5)}
        assert len(counting.calls) >= 3, "expected bounded batches, got one big SELECT"
        assert all("LIMIT" in sql for sql in counting.calls)


class TestBatchedWrites:
    def test_changed_docs_are_processed_in_batches_on_fresh_connections(self, db, monkeypatch):
        # 2,287 changed docs meant minutes of distillation followed by ~9k
        # statements on one long-lived Hrana stream: Turso 502'd every run
        # (2026-07-19) while reads were already paginated. Changed docs must
        # be distilled/embedded/written in bounded batches, each batch
        # upserted on a fresh connection so progress is durable.
        from types import SimpleNamespace

        def fetch(_db):
            for i in range(5):
                yield KnowledgeDoc(source="s", scope="ops", doc_key=f"d{i}", content=f"b{i}")

        module = SimpleNamespace(SOURCE="s", SCOPE="ops", fetch=fetch)
        monkeypatch.setattr(ingest_mod, "_INGEST_BATCH_DOCS", 2)

        factory_calls = []

        def db_factory():
            factory_calls.append(1)
            return db

        result = ingest_mod.ingest_source(
            db, module, distill_enabled=False, embed_enabled=False,
            db_factory=db_factory,
        )

        assert result["inserted"] == 5
        assert len(factory_calls) >= 3, "expected a fresh connection per write batch"
