# scripts/

Python and Node daemons for Radon. **Do not move these files into
`scripts/fetch/` or similar without a shim at the old path and a matching
systemd / launchd / docs-owner update in the same commit.** Fifty units
`ExecStart=` these paths.

CLI aliases live in `.pi/commands.json`. Human index:
[`docs/scripts-reference.md`](../docs/scripts-reference.md).

## Launchers

| Path | Role |
|---|---|
| `cloud.sh` | Everyday: laptop Next.js + newsfeed, VPS owns IB/API/relay |
| `local.sh` | Fully local, including `docker/ib-gateway` |
| `dev` | `web/` `bun run dev` supervisor (Next + IB WS + FastAPI + newsfeed) |
| `ib` | `scripts/ib mode local\|cloud` |
| `docker_ib_gateway.sh` | Laptop Gateway compose in `docker/ib-gateway/` |

## Ingest (`fetch_*.py`)

Dark pool / flow / OI / options / UW surfaces, MenthorQ, Equibles, catalysts,
calendar, X watchlist, yield curve, vol cone, skew, straddle, COR.

## Scanners

`scanner.py`, `discover.py`, `cri_scan.py`, `gex_scan.py`, `vcg_scan.py`,
`bpi_scan.py`, `breadth_scan.py`, `rv_ratio_scan.py`, `leap_scanner_uw.py`,
`leap_iv_scanner.py`, `theta_harvester_scanner.py`,
`strength_confirmation_scanner.py`, `garch_convergence.py`,
`flow_analysis.py`.

## IB / execution

`ib_sync.py`, `ib_place_order.py`, `ib_orders.py`, `ib_order_manage.py`,
`ib_cancel_all.py`, `ib_execute.py`, `ib_reconcile.py`, `ib_chain.py`,
`ib_option_chain.py`, `ib_realtime_server.js`, `ib_watchdog.py`,
`evaluate.py`, `kelly.py`.

## Timer wrappers (`run_*.sh`)

Thin systemd/launchd entrypoints. They call the Python scanners above.
Do not invent a second copy of the scan logic here.

## Packages (leave in place)

| Dir | Role |
|---|---|
| `api/` | FastAPI `:8321` |
| `monitor_daemon/` | Fills, exits, journal, flex-token check |
| `watchdog/` | Service-health paging |
| `newsfeed/` | The Market Ear scraper |
| `db/` | Turso writers + migrations |
| `clients/` | IB, UW, MenthorQ, Equibles, FRED, Cboe |
| `health_service/` | Isolated `:8330` health daemon |
| `incident_watchdog/` | Incident artifacts |
| `knowledge/` | radon-kb MCP |
| `trade_blotter/` | Flex blotter |
| `utils/` | Shared Python helpers |
| `lib/` | Shared JS (relay) + TWR math |
| `tests/` | pytest for this tree |
| `forecasting/`, `backtest/`, `paper/`, `workflow/` | Research / paper |

New scripts stay at this top level unless a shim plan is explicit.
Go-forward grouping (`scripts/fetch/`, `scripts/scan/`, `scripts/ib/`)
is Phase 3 of [`docs/monorepo-legibility-plan.md`](../docs/monorepo-legibility-plan.md)
and is not in force.
