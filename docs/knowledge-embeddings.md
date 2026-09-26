# Knowledge embeddings — dual-dimension vector contract

**Status:** LIVE (migration 0087 applied 2026-09-25; nemotron default backend since 2026-09-25).

The `knowledge` table carries two vector columns:

| Column | Model | Dimensions | Index | Purpose |
|---|---|---|---|---|
| `embedding` | BAAI/bge-small-en-v1.5 | 384 | `idx_knowledge_embedding` | Automatic fallback; ingest dual-writes by default. |
| `embedding_v2` | nvidia/nemotron-3-embed-1b | 2048 | `idx_knowledge_embedding_v2` | Live default for queries and ingest (2026-09-25). |

## Query resolution

`scripts/knowledge/embed.py:resolve_query_vector` selects the query vector:

1. If `RADON_KB_EMBED_BACKEND=nvidia` (default) **and** `v2_coverage_ready(db)` is true → NVIDIA 2048-d query vector against `embedding_v2` index.
2. Otherwise → local 384-d `bge-small-en-v1.5` vector against `embedding` index, or FTS-only if the local embedder is unavailable.

`v2_coverage_ready` returns false while **any** `knowledge.embedding_v2` is NULL. The check is cached for 60 seconds. A missing column or failed read also returns false.

## Ingest behavior

- **Dual-write (default):** Every ingested `KnowledgeDoc` writes both vectors when `RADON_KB_EMBED_DUAL_WRITE` is not `0`/`false`/`off`/`no` (default: on). The 384-d column stays current for the fallback.
- **Single-write:** `RADON_KB_EMBED_DUAL_WRITE=0` disables the 2048-d write; ingest writes only `embedding`.
- **Backend override:** `RADON_KB_EMBED_BACKEND=local` forces BAAI/bge-small-en-v1.5 (384d) for both query and ingest; the NVIDIA path is never used.
- **Disable:** `RADON_KB_EMBED_DISABLED=1` turns off all embeddings; ingest writes FTS-only rows (`embedding` and `embedding_v2` both NULL).

## Backfill order (load-bearing)

Migration 0087 creates `embedding_v2` column and its vector index **while every value is still NULL**, then `scripts/knowledge/backfill_v2.py` backfills. The index must exist before the backfill runs because Turso does not backfill `libsql_vector_idx` for rows written before the index existed (local libsql does, which is why a unit test cannot reproduce the miss).

Backfill is idempotent: `backfill_embedding_v2` only writes where `embedding_v2 IS NULL`.

## Environment variables

| Variable | Default | Effect |
|---|---|---|
| `RADON_KB_EMBED_BACKEND` | `nvidia` | `nvidia` = nemotron 2048-d query/ingest; `local` = bge 384-d only. |
| `RADON_KB_EMBED_DUAL_WRITE` | on (unset) | `0`/`false`/`off`/`no` disables v2 ingest write. |
| `RADON_KB_EMBED_DISABLED` | `0` | `1` disables all embeddings (FTS-only). |
| `NVIDIA_API_KEY` | required for nvidia backend | Must be set when backend is `nvidia`. |
| `FASTEMBED_CACHE_PATH` | `~/.cache/fastembed` | Local model cache; set explicitly on the VPS so the API container finds the model the ingest unit downloaded. |

## Drift test

`scripts/tests/test_knowledge_embedding_contract.py` asserts:

- `embedding_v2` column exists post-migration 0087.
- `idx_knowledge_embedding_v2` index exists.
- `v2_coverage_ready` logic: queries use 384-d while any NULL exists; 2048-d only after full backfill.
- Dual-write env var parsing matches `dual_write_enabled()`.

Run: `python3.13 -m pytest scripts/tests/test_knowledge_embed_v2.py -q`

## Operator actions

- **Rotate NVIDIA key:** Update `NVIDIA_API_KEY` in the encrypted credential store (profile Credentials tab) or `/etc/radon/env`. The next query call picks it up automatically.
- **Force local fallback:** Set `RADON_KB_EMBED_BACKEND=local` in the environment and restart `radon-api` and `radon-monitor`.
- **Disable dual-write (save NVIDIA quota):** `RADON_KB_EMBED_DUAL_WRITE=0`; restart ingest workers. Existing `embedding_v2` values remain; new rows get only 384-d.
- **Verify backfill complete:** Run `python3.13 -c "from scripts.knowledge.embed import v2_coverage_ready; from db import get_db; print(v2_coverage_ready(get_db()))"` on a host with Turso credentials. `True` = 2048-d index is fully populated and active.
- **Manual backfill:** `python3.13 scripts/knowledge/backfill_v2.py` (idempotent; safe to re-run).

## Schema reference

```sql
-- Migration 0087
ALTER TABLE knowledge ADD COLUMN embedding_v2 F32_BLOB(2048);
CREATE INDEX IF NOT EXISTS idx_knowledge_embedding_v2
  ON knowledge(libsql_vector_idx(embedding_v2));
```

Row contract: `scripts/knowledge/schema.py:KnowledgeDoc` (fields `embedding: list[float] | None` for 384d, `embedding_v2: list[float] | None` for 2048d).
Writer: `scripts/knowledge/store.py` (dual-write logic, HTTP and local paths).
Query resolution: `scripts/knowledge/embed.py:resolve_query_vector`.
Backfill script: `scripts/knowledge/backfill_v2.py`.
Contract test: `scripts/tests/test_knowledge_embedding_contract.py`.
