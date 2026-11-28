# 0002. One Clairic date grammar

## Status

Accepted. 2026-11-28.

## Context

`scripts/research/clairic.py` and `scripts/model_gateway/_clairic_backend.py`
carried the same page trim, token budget, request body, and accept/reject
rules. The research module must call the model gateway. The backend is the
Cloudflare transport. Copying `apply` into both let those rules drift, and
importing the backend's `apply` from research would have called Cloudflare
directly because `apply` looked up `fetch_report_date` in the backend.

## Decision

`scripts/model_gateway/clairic_rules.py` owns the grammar.
`apply_date` takes the transport and the ET clock as arguments. Each module
keeps a one-line `apply` that binds its own `fetch_report_date`. Research
still does not read Cloudflare credentials.

`scripts/tests/test_clairic_rules_single_source.py` rejects a second
definition of `build_state`, `build_request`, `sanity_failure`,
`reject_clairic`, or `apply_date`.

## Consequences

A change to what counts as a stale or low-confidence date happens in one
file. The two HTTP paths stay separate.
