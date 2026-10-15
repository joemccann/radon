# Performance Log

Append-only ledger of measured performance fixes. Never edit or delete a row; add a new row to supersede one.

| ID | Surface | File:line | Metric | Before | After | Harness | Test |
|---|---|---|---|---|---|---|---|
| PERF-001 | `GET /cash-flows` | `scripts/api/server.py:6178` | Turso `/v2/pipeline` HTTP round-trips per request | 2 | 1 | `urllib.request.urlopen` call count via `PipelineRecorder` (`scripts/tests/_pipeline_budget_fake.py`) | `scripts/tests/test_cash_flows_pipeline_budget.py` |
| PERF-002 | `_read_orders_snapshot_from_db` (`/orders/refresh`, orders sync) | `scripts/api/server.py:1344` | Turso `/v2/pipeline` HTTP round-trips per call | 2 | 1 | `urllib.request.urlopen` call count via `PipelineRecorder` (`scripts/tests/_pipeline_budget_fake.py`) | `scripts/tests/test_orders_snapshot_pipeline_budget.py` |
| PERF-003 | journal reconciliation import (`POST /api/journal`, `POST /api/journal/sync`) | `web/lib/journalDb.ts:156` | Turso HTTP round-trips to insert 40 new fills | 40 `execute` | 1 `batch` | vitest fake `getDb()` call counts | `web/tests/journal-import-batch-budget.test.ts` |
| PERF-004 | `/knowledge/search` neighbor expansion | `scripts/knowledge/retrieve.py:318` | neighbor SELECTs per 8 hybrid-search winners | 8 | 1 | `_RecordingDb` statement count in `scripts/tests/test_knowledge_retrieve.py` | `scripts/tests/test_knowledge_retrieve.py::TestDedupAndNeighbors::test_neighbor_expansion_is_one_statement_for_eight_winners` |
| PERF-006 | `GET /api/newsfeed/posts` | `web/app/api/newsfeed/posts/route.ts:152` | Turso `execute` calls per cache miss with research sources + feedback | 3 | 1 | vitest spy on in-memory `db.execute` | `web/tests/newsfeed-posts-pipeline-budget.test.ts` |
| PERF-007 | `_write_cache` JSON fallback | `scripts/api/server.py:1303` | bytes for 400-level GEX-shaped payload | 21529 pretty | 11117 compact | `json.dumps` size vs written file | `scripts/api/tests/test_write_cache_compact_budget.py` |
| PERF-010 | `fetch_vol_cone.run` heartbeat grouping | `scripts/fetch_vol_cone.py:742` | history row key-visits on 10 tickers x 5 monthlies x 10 sessions (500 rows) | 59500 nested scans | 4000 one-pass index | `_CountRow.__getitem__` in heartbeat `run()` | `scripts/tests/test_vol_cone_history_index_budget.py` |
| PERF-011 | `fetch_vol_cone._mirror_snapshot` terminal upsert | `scripts/fetch_vol_cone.py:471` | `vol_cone_history` rows upserted for 1 new session on 249 existing (5x5x10) | 251 (checkpoint + full flatten) | 2 (checkpoint + delta retry) | `writer.upsert_vol_cone_rows` row count | `scripts/tests/test_vol_cone_upsert_budget.py` |
