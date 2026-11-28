# 0003. One model-catalog store

## Status

Accepted. 2026-11-28.

## Context

`scripts/refresh_model_catalog.py` is the systemd entry. It asks the model
gateway for `refresh-catalog`, then writes Turso and `data/llm_models.json`.
The same file still carried the frontier selectors, the picker label, and a
second copy of the store. Nothing in that process called the selectors.
`FETCHERS` named fetch functions that the file no longer defined.

The gateway module `scripts/model_gateway/_catalog_backend.py` is the copy
`specialized.refresh_catalog` and the catalog tests actually run. Its
provider set includes NVIDIA and Cerebras. The app copy's set did not, so a
failed poll for those providers was not offered back for carry-forward.

## Decision

Frontier selection and the store stay in `_catalog_backend`. The app job
imports `load_previous`, `persist_result`, and `_record_cycle_failure` from
there. Discovery still crosses the gateway socket. The app process does not
poll providers itself.

`scripts/tests/test_catalog_rules_single_source.py` rejects a second
`select_anthropic_frontier`, `select_openai_frontier`, `select_xai_frontier`,
or `label_for`. It also requires the app seed to keep NVIDIA and Cerebras
rows.

## Consequences

A change to what counts as a frontier model, or to how a failed poll is
stored, happens in the gateway module. The app job's previous seed now
matches that module's provider set, so a failed NVIDIA or Cerebras poll
keeps the last row in the payload the gateway returns. The in-process
`_catalog_backend.run` path remains for tests and for a native process that
already holds the grants. It writes through the same store functions.
