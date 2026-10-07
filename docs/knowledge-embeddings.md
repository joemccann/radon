# Knowledge embeddings — dual-dimension vector contract

**Status:** LIVE (migration 0087 applied 2026-09-25; nemotron default backend since 2026-09-25).

The `knowledge` table carries two vector columns:

| Column | Model | Dimensions | Access | Purpose |
|---|---|---|---|---|
| `embedding` | BAAI/bge-small-en-v1.5 | 384 | exact `vector_distance_cos` scan | Automatic fallback; ingest dual-writes by default. No ANN index after 0090. |
| `embedding_v2` | nvidia/nemotron-3-embed-1b | 2048 | exact `vector_distance_cos` scan | Live default for queries and ingest (2026-09-25). No ANN index. |

## Query resolution

`scripts/knowledge/embed.py:resolve_query_vector` selects the query vector:

1. If `RADON_KB_EMBED_BACKEND=nvidia` (default) **and** `v2_coverage_ready(db)` is true → NVIDIA 2048-d query vector. Retrieval is `SELECT id FROM knowledge WHERE embedding_v2 IS NOT NULL ORDER BY vector_distance_cos(embedding_v2, vector32(?)) LIMIT ?`. Cosine distance 0 is identical, so ascending order is nearest-first, the rank RRF expects. Similarity is `1 - distance` and ranks the same rows.
2. Otherwise → local 384-d `bge-small-en-v1.5` vector, the same exact scan over `embedding`, or FTS-only if the local embedder is unavailable.

`v2_coverage_ready` returns false while **any** `knowledge.embedding_v2` is NULL. The check is cached for 60 seconds. A missing column or failed read also returns false.

## Ingest behavior

- **Dual-write (default):** Every ingested `KnowledgeDoc` writes both vectors when `RADON_KB_EMBED_DUAL_WRITE` is not `0`/`false`/`off`/`no` (default: on). The 384-d column stays current for the fallback.
- **Single-write:** `RADON_KB_EMBED_DUAL_WRITE=0` disables the 2048-d write; ingest writes only `embedding`.
- **NVIDIA rate limit:** every embedding request takes a slot from the host-wide NVIDIA pacer shared with the model ladder (`scripts/nvidia_rate_limit.py`, `RADON_NVIDIA_RPM`, default 20 a minute; see [dropbox-research.md](dropbox-research.md)). With no slot inside 15 s the call raises `rate_limited_local` and the query falls back to the 384-d local vector. A 429 feeds the shared cooldown; a 401/403 is logged as `NVIDIA AUTHORIZATION FAILED` and not retried.
- **Backend override:** `RADON_KB_EMBED_BACKEND=local` forces BAAI/bge-small-en-v1.5 (384d) for both query and ingest; the NVIDIA path is never used.
- **Disable:** `RADON_KB_EMBED_DISABLED=1` turns off all embeddings; ingest writes FTS-only rows (`embedding` and `embedding_v2` both NULL).
- **Transient write retries:** source and prepared-write retries wait `_retry_delay(attempt)` in `scripts/knowledge/ingest.py`: 12s doubling to a 60s cap, plus up to 25% jitter. Turso reaps an abandoned idle transaction after 10s (up to 300s if it is still running), so a shorter wait queues the retry behind the orphan's writer lock. Replay is safe because upserts are idempotent on `content_hash`.

## Bounded HTTP persistence

`scripts/knowledge/http_db.py` provides a schema-independent Hrana connection.
It supports both the `scripts/` import path and package-mode imports. Knowledge
ingestion and Liquid Compute observation replacement share its conditional
transaction transport (REL-257): BEGIN, dependent writes, COMMIT or ROLLBACK,
and stream close travel in one bounded request. A lost receipt raises; callers
must prove replay idempotency rather than infer that no commit happened. Liquid
Compute deletes and reinserts each observation identity within that transaction,
so a failed insert preserves the prior batch. The connection itself creates no
knowledge-specific schema.

Connection `execute` and `execute_transaction` outcomes also contribute to the
fixed-label `database` counters (REL-320 / R-027). One conditional transaction
is one observed operation, rather than one count per statement. Failed or lost
receipts count as errors even when the caller later recovers; no retry is added
by telemetry. Bounded `operation_metrics` log samples report process-lifetime
counts, monotonic rates and error ratios without SQL or source content. The
[core counter contract](cloud-services.md#host-metrics-dur-12) describes restart
resets, scope and why these observations are not a durable execution ledger.

## Backfill

**Use when:** v2 coverage is incomplete or knowledge ingestion causes writer
contention. Before manual migrations, verify the deployed retrieval code
uses exact scans and preserve a [verified recovery
copy](cloud-services.md#restore-runbook). Diagnose with the read-only index
checks below and the backfill's `--dry-run`; manual migration can apply all
pending manual files, so review those files before using its opt-in.
The write blast radius is the shared database. Stop on an unexpected target,
schema error, or renewed writer contention. Keep ingestion disabled until
the index checks pass. Verify coverage and the golden eval after backfill.
Do not roll back to retrieval code requiring a dropped index; retain exact
scan/local fallback and escalate unresolved failures via the [incident
runbook](incident-runbook.md). Data recovery follows the restore owner above.

Migration 0087 added `embedding_v2` and `idx_knowledge_embedding_v2` while every value was still NULL. That DiskANN index matched exact-scan recall (0.96 hit@5, 0.99 recall vs exact on 11,718 rows / 24 eval_golden questions) and cost about 5.0 GB, 4.5-33s per row insert, and a CREATE that cannot finish inside Turso's one-hour statement limit. Exact scan was about 1s p50 and 1.8s p95, with no index storage and about 17ms per row write. Migration 0089 drops the index. Do not edit 0087.

0089 is a manual migration. `DROP INDEX` took about 14 minutes on a branch copy. `radon-api` runs `migrate.py --boot` under `timeout 30` and a 20s boot deadline, and the runner commits an ordinary file once at the end. The automatic path skips 0089 (the boot schema marker ignores it, so a Turso brownout still boots). Apply it once, each statement committed on its own:

```bash
RADON_MIGRATE_MANUAL=1 python3.13 scripts/db/migrate.py
```

Confirm:

```sql
SELECT name FROM sqlite_master WHERE name = 'idx_knowledge_embedding_v2';
```

Migration 0090 drops the 384-d `idx_knowledge_embedding` the same way (manual, same command). On a fork of `radon` (2026-09-28) one row's `embedding` UPDATE took 10-26s with the index and every other statement about 0.03s. Ingest held Turso's single writer that long per chunk and every other writer timed out. `radon-knowledge.timer` stays disabled while `SELECT name FROM sqlite_master WHERE name = 'idx_knowledge_embedding'` returns a row. Production applied 0090 on 2026-09-28, the index is gone, and the timer runs hourly.

Then rerun the backfill. Without the index, `--batch-size 64` and no sleep is about 15 minutes for 8,715 NULL rows:

```bash
python3.13 scripts/knowledge/backfill_v2.py --batch-size 64 --min-interval 0
```

Then `python3.13 scripts/knowledge/eval_golden.py --mode all`. Backfill does not require the index. It only writes where `embedding_v2 IS NULL`.

## Golden eval

`scripts/knowledge/eval_golden.py` scores the draft v2 set (`scripts/knowledge/golden_set.json`) under three retrieval modes:

| Mode | Path | Recency | Per-source cap |
|---|---|---|---|
| `hybrid` | production `hybrid_search` (FTS + vector RRF) | on | on |
| `vector` | vector leg only | off | off |
| `keyword` | FTS leg only | off | off |

Default `--mode all` runs every mode. Metrics per mode: hit@1, hit@5, recall@10, MRR, nDCG@10 (grades 3/2/1). Also broken down by `category` and by relevant-doc `source`. Each question keeps its top-10 rows. The report records `backend_used` and `fallback` (true when nvidia requested but a 384-d local vector actually ran). `--strict-backend` exits 2 on that fallback.

```bash
.venv/bin/python scripts/knowledge/eval_golden.py --mode all
.venv/bin/python scripts/knowledge/eval_golden.py --mode hybrid --backend nvidia --strict-backend
.venv/bin/python scripts/knowledge/eval_golden.py --mode all \
  --baseline scripts/knowledge/golden_eval_baseline.json \
  --max-drop 0.03 \
  --write-results /var/lib/radon/knowledge-eval
```

`--baseline PATH` exits 1 when hit@5 or MRR for any scored mode drops more than `--max-drop` (default 0.03). `--write-baseline PATH` writes the compact metrics snapshot. `scripts/knowledge/golden_eval_baseline.json` is the initial live Turso `--mode all` snapshot (`placeholder: false`, NVIDIA 2048-d, no fallback). `golden_set_candidates.json` is gone. Keep `draft: true` until a human reviews the set. The nightly timer stays off until that review.

Three production-derived journal questions and their exact trade identifiers have been removed from the tracked golden set; existing semantic journal patterns remain. `scripts/tests/test_knowledge_golden.py::TestGoldenSetFile::test_shipped_golden_set_has_no_journal_trade_ids` rejects journal `doc_key_pattern` values containing six or more consecutive digits. Do not commit production trade identifiers or private brokerage details in evaluation fixtures. The baseline predates the removal and must be regenerated against the reviewed set before enabling nightly evaluation.

Nightly VPS unit (not enabled): `cloud/services/radon-knowledge-eval.{service,timer}`. `setup-vps.sh` inventories both files and `enable_services` skips them on every run. It does not read the baseline file. Auto-sync stays off. A `not-installed:` drift ack holds the pending window; the ack is the draft review, not the baseline file. The oneshot writes no `service_health` row (`EXEMPT_UNITS` `gap:`); a failed run pages via the unit watchdog. Enable only after the draft review, using the steps in [`cloud-services.md`](cloud-services.md#knowledge-golden-eval-radon-knowledge-evaltimer). CI stays offline: in-memory libsql fixtures cover metric math, mode switching, baseline comparison, and schema validation.

Past about 50k rows, revisit a compact index (`compress_neighbors=float8`, `max_neighbors=32`, `insert_l=40`) built in pieces.

## Environment variables

| Variable | Default | Effect |
|---|---|---|
| `RADON_KB_EMBED_BACKEND` | `nvidia` | `nvidia` = nemotron 2048-d query/ingest; `local` = bge 384-d only. |
| `RADON_KB_EMBED_DUAL_WRITE` | on (unset) | `0`/`false`/`off`/`no` disables v2 ingest write. |
| `RADON_KB_EMBED_DISABLED` | `0` | `1` disables all embeddings (FTS-only). |
| `NVIDIA_API_KEY` | required for nvidia backend | Must be set when backend is `nvidia`. |
| `FASTEMBED_CACHE_PATH` | `~/.cache/fastembed` | Local model cache; set explicitly on the VPS so the API container finds the model the ingest unit downloaded. |

## Drift test

`scripts/tests/test_knowledge_embed_v2.py` asserts:

- `embedding_v2` column exists post-migration 0087.
- `idx_knowledge_embedding_v2` is created by 0087 and dropped by 0089; `idx_knowledge_embedding` is dropped by 0090. Both are `-- radon-migrate: manual` and are not applied by boot.
- `v2_coverage_ready` logic: queries use 384-d while any NULL exists; 2048-d exact scan only after full backfill.
- Dual-write env var parsing matches `dual_write_enabled()`.

Run: `python3.13 -m pytest scripts/tests/test_knowledge_embed_v2.py scripts/tests/test_migrate.py -q`

## Operator actions

- **Rotate NVIDIA key:** Update `NVIDIA_API_KEY` in the encrypted credential store (profile Credentials tab) or `/etc/radon/env`. The next query call picks it up automatically.
- **Force local fallback:** Set `RADON_KB_EMBED_BACKEND=local` in the environment and restart `radon-api` and `radon-monitor`.
- **Disable dual-write (save NVIDIA quota):** `RADON_KB_EMBED_DUAL_WRITE=0`; restart ingest workers. Existing `embedding_v2` values remain; new rows get only 384-d.
- **Verify backfill complete:** Run `python3.13 -c "from scripts.knowledge.embed import v2_coverage_ready; from scripts.db.client import get_db; print(v2_coverage_ready(get_db()))"` on a host with Turso credentials. Run from the repository root with dependencies installed and the intended database environment already loaded. `True` means no NULL `embedding_v2` was found; backend configuration or a provider failure can still select the local fallback.
- **Manual backfill:** `python3.13 scripts/knowledge/backfill_v2.py --batch-size 64 --min-interval 0` (idempotent; safe to re-run). Run it after 0089 has dropped the index.
- **Drop the 2048-d index:** `RADON_MIGRATE_MANUAL=1 python3.13 scripts/db/migrate.py` (about 14 minutes when the index exists). Idempotent (`DROP INDEX IF EXISTS`).

## Schema reference

```sql
-- Migration 0087 (unchanged; superseded by 0089)
ALTER TABLE knowledge ADD COLUMN embedding_v2 F32_BLOB(2048);
CREATE INDEX IF NOT EXISTS idx_knowledge_embedding_v2
  ON knowledge(libsql_vector_idx(embedding_v2));

-- Migration 0089, manual only
DROP INDEX IF EXISTS idx_knowledge_embedding_v2;
```

Row contract: `scripts/knowledge/schema.py:KnowledgeDoc` (fields `embedding: list[float] | None` for 384d, `embedding_v2: list[float] | None` for 2048d).
Writer: `scripts/knowledge/store.py` (dual-write logic, HTTP and local paths).
Query resolution: `scripts/knowledge/embed.py:resolve_query_vector`.
Backfill script: `scripts/knowledge/backfill_v2.py`.
Contract test: `scripts/tests/test_knowledge_embed_v2.py`.
