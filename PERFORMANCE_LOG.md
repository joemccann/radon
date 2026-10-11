# Performance Log

Append-only ledger of measured performance fixes. Never edit or delete a row; add a new row to supersede one.

| ID | Surface | File:line | Metric | Before | After | Harness | Test |
|---|---|---|---|---|---|---|---|
| PERF-001 | `GET /cash-flows` | `scripts/api/server.py:6178` | Turso `/v2/pipeline` HTTP round-trips per request | 2 | 1 | `urllib.request.urlopen` call count via `PipelineRecorder` (`scripts/tests/_pipeline_budget_fake.py`) | `scripts/tests/test_cash_flows_pipeline_budget.py` |
| PERF-002 | `_read_orders_snapshot_from_db` (`/orders/refresh`, orders sync) | `scripts/api/server.py:1344` | Turso `/v2/pipeline` HTTP round-trips per call | 2 | 1 | `urllib.request.urlopen` call count via `PipelineRecorder` (`scripts/tests/_pipeline_budget_fake.py`) | `scripts/tests/test_orders_snapshot_pipeline_budget.py` |
