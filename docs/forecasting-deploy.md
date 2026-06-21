# Forecasting Deploy — Chronos-2 Nightly Runner

The forecasting stack (Chronos-2 + torch) is **isolated to a dedicated host**.
Those heavy dependencies must never land on the standard `radon-*` fleet units.
A single nightly systemd timer runs the orchestrator
(`scripts/nightly_forecast.py`), which chains:

1. `backfill_flow_history.backfill_from_cache` — replays the dark-pool cache
   into `ticker_flow_history`.
2. `flow_surprise.rank_watchlist_surprise` — ranks the watchlist by surprise
   residual and writes the dashboard cache.
3. `calibration_report.build_calibration_report(persist=True)` — produces and
   persists the verdict (does Chronos beat the deterministic baseline?).

Each step is independent: a failure in one is captured (status recorded,
traceback to stderr) and the remaining steps still run.

---

## 1. Provisioning

Provision the isolated venv on the forecasting host once (re-run with
`--upgrade` to refresh deps). The script is idempotent and prints the venv
python path on success.

```bash
# default venv path: /home/radon/forecasting-venv
bash scripts/forecasting/provision_venv.sh

# refresh dependencies later
bash scripts/forecasting/provision_venv.sh --upgrade

# custom path
RADON_FC_VENV=/opt/radon/fc-venv bash scripts/forecasting/provision_venv.sh
```

Requires `python3.12` or `python3.13` on PATH. Installs
`requirements-forecasting.txt` (`torch`, `chronos-forecasting`).

---

## 2. systemd units (COPY-PASTE templates)

> These unit files belong in the **radon-cloud** repo (deploy-authoritative).
> They are documented here as templates only and are **not** auto-deployed from
> this repository. Edit them in the radon-cloud working copy on the VPS and push
> from there.

Replace `<venv>` with the path printed by `provision_venv.sh`
(default `/home/radon/forecasting-venv`) and `<radon-cloud-checkout>` with the
deploy checkout (e.g. `/home/radon/radon-cloud`).

### `/etc/systemd/system/radon-forecast-nightly.service`

```ini
[Unit]
Description=Radon nightly Chronos-2 forecast pipeline (backfill -> surprise -> calibration)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=radon
WorkingDirectory=<radon-cloud-checkout>
ExecStart=<venv>/bin/python scripts/nightly_forecast.py --metric flow_strength --top 25 --lookback 250 --backfill-days 20
# stdout is the summary JSON; progress + tracebacks go to the journal via stderr
StandardOutput=journal
StandardError=journal
TimeoutStartSec=1800

[Install]
WantedBy=multi-user.target
```

### `/etc/systemd/system/radon-forecast-nightly.timer`

```ini
[Unit]
Description=Run the Radon nightly forecast pipeline pre-market

[Timer]
# 07:00 UTC = pre-market (~02:00/03:00 ET depending on DST), off-hours
OnCalendar=*-*-* 07:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

Enable on the forecasting host:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now radon-forecast-nightly.timer
systemctl list-timers radon-forecast-nightly.timer
```

---

## 3. Unit-file ownership

The `.service` / `.timer` files above are **deploy-authoritative in the
radon-cloud repo**, not this one. Do not add them to this repository's deploy
path or expect `scripts/deploy.sh` to install them. Maintain them in the
radon-cloud working copy on the VPS and push from there.

---

## 4. Operator verification sequence

Before trusting any forecast, run the steps manually and inspect the verdict.
Do this once after provisioning and after any model/dependency change:

```bash
VENV=/home/radon/forecasting-venv

# 1. Provision (idempotent) and confirm the python path
bash scripts/forecasting/provision_venv.sh

# 2. Backfill flow history from the dark-pool cache
$VENV/bin/python scripts/backfill_flow_history.py --days 20

# 3. Build + PERSIST the calibration verdict
$VENV/bin/python scripts/forecast_calibration.py --metric flow_strength \
    --lookback 250 --persist

# 4. Inspect the verdict BEFORE trusting forecasts
#    Look for chronos_available=true AND chronos_beats_baseline=true with a
#    non-trivial n_ok. If chronos does NOT beat the baseline, treat forecasts
#    as advisory only — the deterministic baseline is the safer signal.
```

Only after the verdict shows Chronos beating the baseline on a meaningful
sample should the nightly timer's output be wired into any actionable surface.
