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
| PERF-008 | FastAPI Turso Hrana transport | `scripts/api/db_http.py:75` | TCP connections per 5 sequential `/v2/pipeline` POSTs on one thread | 5 | 1 | local `ThreadingHTTPServer` `setup()` count | `scripts/tests/test_api_db_http.py::TestHranaKeepAlive` |
