# Forecasting Deploy — Chronos-2 Nightly Runner

The Chronos-2 and torch forecasting stack is isolated to a dedicated forecasting
host. Those dependencies, its virtual environment, and its nightly workload
must never be installed on the standard `radon-*` fleet host.

The nightly pipeline (`scripts/nightly_forecast.py`) runs, independently and in
order:

1. `backfill_flow_history.backfill_from_cache` to replay the dark-pool cache
   into `ticker_flow_history`.
2. `flow_surprise.rank_watchlist_surprise` to rank watchlist surprise residuals
   and write the dashboard cache.
3. `calibration_report.build_calibration_report(persist=True)` to persist the
   Chronos-versus-baseline verdict.

A failure is recorded to stderr and does not prevent later stages from running.

## Dedicated-host runtime

Provision the forecasting virtual environment on the dedicated host only. The
script is idempotent and prints the resulting Python path.

```bash
# default: /home/radon/forecasting-venv
bash scripts/forecasting/provision_venv.sh

# refresh dependencies
bash scripts/forecasting/provision_venv.sh --upgrade

# custom location
RADON_FC_VENV=/opt/radon/fc-venv bash scripts/forecasting/provision_venv.sh
```

The host needs Python 3.13, or Python 3.12 with its venv module. Provisioning
installs the app dependencies plus `requirements-forecasting.txt`; the latter
uses CPU-only torch wheels. Do not install these dependencies on the production
VPS fleet.

## Ownership and installation

The canonical unit sources are:

- [`cloud/services/radon-forecast-nightly.service`](../cloud/services/radon-forecast-nightly.service)
- [`cloud/services/radon-forecast-nightly.timer`](../cloud/services/radon-forecast-nightly.timer)

They are owned with the rest of the production infrastructure in the Radon
monorepo `cloud/` tree. The standalone `radon-cloud` checkout owns no
forecasting code or unit files. Its `/home/radon/radon-cloud/.env` path remains
an external secrets-file compatibility exception only; it is not a source tree.

Install or update these dedicated-host units through that host's approved root
transaction. Do not copy templates from this document, install units directly,
or use `setup-vps.sh` as a live-update shortcut. The main VPS root bootstrap and
manifest preflight process applies only to artifacts it manages; it does not
claim to install the dedicated forecasting units. Use the [cloud operating
contract](../cloud/CLAUDE.md) and [monorepo cloud lifecycle
runbook](monorepo-cloud-migration.md) for the main VPS control-plane procedure.

The dedicated-host transaction must preserve these invariants:

- `WorkingDirectory=/home/radon/radon`, from the same monorepo SHA as `cloud/`.
- `EnvironmentFile=/home/radon/radon-cloud/.env`, mode `0600`; never copy its
  values into the checkout.
- `RADON_DB_NO_REPLICA=1` to use the direct cloud database path.
- `ExecStart` uses `/home/radon/forecasting-venv/bin/python` and the canonical
  `scripts/nightly_forecast.py` invocation.
- The timer remains nightly at 07:00 UTC, persistent, and jittered.

## Operator validation

After an approved host update or a model/dependency change, run the pipeline
manually on the dedicated host and inspect the persisted calibration verdict.
Chronos is actionable only when `chronos_available=true`,
`chronos_beats_baseline=true`, and the successful sample is meaningful.
Otherwise treat its output as advisory and use the deterministic baseline.

For pipeline behavior and test coverage, use the source and tests in the
monorepo. For the cloud lifecycle and manifest contract, use the linked
canonical runbooks above.
