# Status

This file is not a decision log.

Trade evaluations live in Turso and the radon-kb corpus. Run `python3.13 scripts/evaluate.py [TICKER]` for a fresh 7-milestone eval. Journal and blotter are the fill record.

Operator health is `/api/service-health` (body, not HTTP status) and `docs/incident-runbook.md`. Portfolio source of truth is Interactive Brokers via `python3.13 scripts/ib_sync.py`.
