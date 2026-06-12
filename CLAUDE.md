# RADON — CLAUDE.md

**Radon** = market-structure reconstruction. Surfaces convex opportunities from dark pool / OTC flow, vol surfaces, cross-asset positioning. **Flow signal or nothing.**

Brand: `docs/brand-identity.md` · Structures: `docs/options-structures.{json,md}` · UW spec: `docs/unusual_whales_api.md` · Cloud runbook: `docs/cloud-services.md`

---

## Subsystem-specific rules — loaded on cwd

Sub-directory CLAUDE.md files auto-load when cwd is anywhere under that subtree. Only the relevant subset is in context for any given session.

- **`web/CLAUDE.md`** — Next.js frontend: calculations, components cheat sheet, theme system, cache contract, combo / BAG order guardrails, IB error rendering, build constraint, mobile shell.
- **`scripts/CLAUDE.md`** — Python conventions: client ID ranges, IB request bounding, high-throughput patterns, journal lot-matched basis, entry-date resolution.
- **`scripts/api/CLAUDE.md`** — FastAPI: IB Gateway 3 modes, 2FA-aware restart summary, authentication, subprocess pattern, autonomous timers, service health dual-write.
- **`scripts/monitor_daemon/CLAUDE.md`** — Real-time fill / order / journal handlers: market-hours gate, heartbeat convention, journal_sync labelling.
- **`scripts/watchdog/CLAUDE.md`** — Service-health buckets, anti-flood, writer-state semantics, IB-outage grouping.
- **`scripts/newsfeed/CLAUDE.md`** — Headless Playwright, tagging pipeline (vision + text), cookie-gated images, taxonomy.
- **`scripts/health_service/CLAUDE.md`** — Isolated stdlib health daemon (`radon-health.service` :8330): stdlib-only isolation contract, `/healthz` + `/status`, never-502 edge floor, three-valued states. Decoupled from the cascade-stop on purpose.

## Reference docs — read explicitly when needed

- `docs/evaluation.md` — 7-milestone trade eval pipeline + signal interpretation + intraday dark-pool interpolation
- `docs/reports.md` — HTML report templates + P&L card spec
- `docs/ib-gateway-recovery.md` — 2FA push lock + backoff ladder + watchdog self-heal state machine
- `docs/options-structures.{json,md}` — structure taxonomy
- `docs/brand-identity.md` — brand tokens, signal semantics, voice
- `docs/cloud-services.md` — Hetzner runbook (services, schedulers, deploy)
- `docs/unusual_whales_api.md` — UW endpoint surface

---

## Behavioral Guidelines

**Think before coding.** State assumptions, surface tradeoffs, ask when unclear. Don't pick silently between interpretations.

**Simplicity first.** Minimum code that solves the problem. No speculative features, abstractions for single-use code, "flexibility" not requested, or error handling for impossible scenarios. If 200 lines could be 50, rewrite.

**Surgical changes.** Touch only what you must. Don't "improve" adjacent code/comments/formatting or refactor unrelated things. Match existing style. Remove orphans YOUR changes created; leave pre-existing dead code alone unless asked. Every changed line should trace to the user's request.

**Goal-driven execution.** Transform tasks into verifiable goals ("add validation" → "write tests for invalid inputs, then make them pass"). State a brief plan for multi-step work. Strong success criteria let you loop independently.

---

## ⛔ Mandatory Rules

1. **Be concise.** No preamble.
2. **Red/green TDD always.** Vitest (unit), chrome-cdp / Playwright (E2E). Target 95% coverage.
3. **E2E browser verification for all UI work.** Primary `chrome-cdp`, fallback Playwright (`web/playwright.config.ts`).
4. **API keys** in `.env` files. Never `~/.zshrc` unless fallback.
5. **No raw hex in UI.** Use brand tokens. 4px max border-radius on panels.
6. **No em dashes in user-facing copy.**

## ⛔ Four Gates — Sequential, No Exceptions

| Gate | Rule |
|---|---|
| 1. Convexity | Gain ≥ 2× loss. Defined-risk only. |
| 2. Edge | Specific, data-backed dark-pool / OTC signal that hasn't moved price. |
| 3. Risk | Fractional Kelly. Hard cap 2.5% bankroll / position. |
| 4. ~~No naked shorts~~ | **DISABLED 2026-04-30.** Logic preserved as `_*Impl`. Re-enable: `docs/naked-short-reenable.md`. |

Any gate fails → stop. Name the gate.

## Data Source Priority

1. Interactive Brokers (TWS / Gateway) — real-time
2. Unusual Whales (`$UW_TOKEN`) — dark pool, sweeps, alerts
3. Yahoo — fallback
4. Web scrape — last resort

Never skip to Yahoo / web without trying IB → UW first. Clients live in `scripts/clients/`.

## Credentials

| File | Loaded by | Contains |
|---|---|---|
| `.env` (root) | python-dotenv | MenthorQ creds, Clerk JWKS / issuer / allowlist |
| `.env.ib-mode` (root, gitignored) | overlayed after `.env` | `IB_GATEWAY_MODE`, `IB_GATEWAY_HOST` — toggled by `scripts/ib mode local\|cloud` |
| `web/.env` | Next.js | `ANTHROPIC_API_KEY`, `UW_TOKEN`, `EXA_API_KEY`, `CEREBRAS_API_KEY`, Clerk keys |

**IB Flex env (Hetzner `/home/radon/radon-cloud/.env`):** `IB_FLEX_TOKEN`, `IB_FLEX_QUERY_ID=1422766` (blotter), `IB_FLEX_NAV_QUERY_ID=1497709` (CashTransactions — don't repurpose for trade pulls), `IB_GATEWAY_MODE=docker`, `IB_GATEWAY_COMPOSE_DIR=/home/radon/radon-cloud` (required; in-tree default is wrong on VPS). Journal rehydrate uses query `1442520` via `IB_FLEX_QUERY_ID` at runtime.

**`.env` values with `$` need single-quoting.** Bash `set -a; . file; set +a` shell-expands `$VAR` under `set -u` and aborts silently from systemd. Single-quote (`PASS='RX$abc!xyz'`) or use systemd `EnvironmentFile=` / `python-dotenv`. See `feedback_env_file_shell_expansion.md`.

---

## Architecture

`npm run dev` runs four services. Filter logs: `npm run dev -- --only <next|ib|api|scraper>`.

| Service | Port / cadence |
|---|---|
| Next.js | 3000 |
| FastAPI (`scripts/api/server.py`) | 8321 |
| IB WS relay (`ib_realtime_server.js`) | 8765 |
| Newsfeed scraper (`scripts/newsfeed/index.js`) | 120s |

Next.js routes call FastAPI via `radonFetch()` (`web/lib/radonApi.ts`). **No `spawn()` from Next.js.** Detailed FastAPI rules in `scripts/api/CLAUDE.md`.

### Two-Mode Deployment

Both modes read/write the **same Turso DB** (`libsql://radon-joemccann.aws-us-west-2.turso.io`) **direct-to-cloud — no embedded replica anywhere as of 2026-05-20**. JSON files in `data/` are written alongside as fallback. The libsql embedded replica was retired after WAL conflicts between multi-writer-per-host and direct-cloud writers. Direct-to-cloud is the code default (DUR-07): a replica needs an explicit `RADON_DB_USE_REPLICA=1` opt-in, the legacy `RADON_DB_NO_REPLICA=1` kill switch always wins, and the fleet drop-in `radon-.service.d/common.conf` sets it on every `radon-*` unit as belt-and-suspenders. Reads +30–60 ms (absorbed by SWR); WAL contention structurally impossible. See `feedback_libsql_replica_one_writer.md`.

- `scripts/cloud.sh` → `RADON_MODE=hetzner`. Schedulers run as systemd on Hetzner (`radon-{api,monitor,relay,refresh,nextjs}`); laptop runs only Next.js + newsfeed. `app.radon.run` serves when laptop closed.
- `scripts/local.sh` → `RADON_MODE=local`. Laptop launchd plists own all schedulers.

**Auto-deploy on push to main.** `.github/workflows/ci.yml` runs the Vitest + pytest gate then deploys on green: it SSHes to Hetzner and runs `bash scripts/deploy.sh` from `~/radon-cloud/`. `git push origin main` IS the deploy. Confirm: `gh run list --workflow=ci.yml --limit 1`. After deploy, `sudo systemctl restart radon-api.service` may be needed. `deploy.sh` lives in the separate `radon-cloud` repo (CI does NOT git-pull it; the VPS working copy is authoritative — edit there + push). It health-gates the relay restart: before tearing services down (while the current radon-api still serves `/health`, so the reading is the gateway's real state, not a just-restarted api's pool warmup), `wait_for_gateway_ready` confirms the IB gateway is authenticated + port_listening (bounded 60s, warn-and-proceed), so a deploy never restarts the relay into a mid-restart/awaiting-2FA gateway. The relay itself self-heals on reconnect and raises a `service_health` row (`ib-realtime-relay`) instead of looping silently on no-ticks (see `project_relay_stale_tick_ladder_2026_06_05`).

Schema: `scripts/db/migrations/0001_init.sql`. Writers: `scripts/db/writer.{js,py}`. Routes prefer DB, fall back to disk.

**Image host:** `https://media.radon.run` (Caddy on Hetzner, fed by laptop rsync over Tailscale). Posts use absolute URLs. Fallback: `RADON_MEDIA_REMOTE=radon@5.78.148.38:/home/radon/radon-cloud/media/`.

**Trades canonical store:** Turso `journal` table. `/journal` and `/orders` both derive from it. `/orders` uses `web/lib/blotter/fromJournal.ts:journalRowsToBlotter()` with fallback to `data/blotter.json` for legacy rows lacking `realized_pnl`/`cost_basis`/`proceeds`. See `docs/cloud-services.md`.

---

## Commands

| Command | Action |
|---|---|
| `scan` / `discover` | Watchlist / market-wide flow |
| `evaluate [TICKER]` | Full 7-milestone eval (see `docs/evaluation.md`) |
| `portfolio` / `sync` | Positions / pull from IB |
| `blotter` / `blotter-history` | Today / historical |
| `leap-scan` / `garch-convergence` / `seasonal` | IV mispricing / GARCH / seasonality |
| `analyst-ratings [TICKERS]` | Ratings + targets |
| `vcg-scan` / `cri-scan` / `gex-scan` | Vol-credit gap / Crash Risk / Gamma |
| `menthorq-{cta,dashboard,screener,forex,summary,quin}` | MenthorQ tools |

## Critical Data Files

| File | Purpose |
|---|---|
| `data/portfolio.json` | Open positions, bankroll, exposure |
| `data/trade_log.json` | **Append-only** trade journal |
| `data/watchlist.json` | Surveillance tickers |
| `data/tag_taxonomy.json` | Auto-growing UPPERCASE tag list (force-tracked) |
| `data/{vcg,gex}.json` | Scan caches |
| `data/leap.json` | LEAP IV-mispricing cache |
| `data/price_history_cache/` | Auto-pruned at 500 |

`data/replica.db` (libsql embedded replica) decommissioned 2026-05-20. Must NOT exist on any Radon host. Safe to delete if it appears. See `feedback_libsql_replica_one_writer.md`.

---

## Startup Checklist

- [ ] `scripts/cloud.sh` (default) or `scripts/local.sh`
- [ ] `curl http://localhost:8321/health` → `ib_gateway.port_listening: true`
- [ ] Reconciliation, exit orders, CRI scan auto-running
- [ ] Market hours: `TZ=America/New_York date +"%A %H:%M"` (9:30–16:00 ET, Mon–Fri)

## Output Discipline

- Always `signal → structure → Kelly math → decision`
- State probabilities; flag uncertainty
- Failing gate = stop, name the gate
- **Never rationalize a bad trade**
