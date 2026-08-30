# RADON — CLAUDE.md

**Radon** = market-structure reconstruction. Surfaces convex opportunities from dark pool / OTC flow, vol surfaces, cross-asset positioning. **Flow signal or nothing.**

Brand: `docs/brand-identity.md` · Structures: `docs/options-structures.{json,md}` · UW spec: `docs/unusual_whales_api.md` · Cloud runbook: `docs/cloud-services.md`

---

## Subsystem-specific rules — loaded on cwd

Sub-directory CLAUDE.md files auto-load when cwd is anywhere under that subtree. Only the relevant subset is in context for any given session.

- **`web/CLAUDE.md`** — Next.js frontend: calculations, components cheat sheet, theme system, cache contract, combo / BAG order guardrails, IB error rendering, build constraint, mobile shell.
- **`site/CLAUDE.md`** — radon.run marketing site (Next 16 + Tailwind v4, atomic design): the "Editorial Quant Research" direction, the product-plate screenshot methodology (chrome-cdp capture + `radon-user` anonymization + theme-aware light/dark + cookie-gated newsfeed image-wait + the portfolio recreation), the ⛔ PII rule (plates still show real account FIGURES), and Vercel auto-deploy on push to main.
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
- `docs/grok-page-responder.md` — VPS P1 auto-fix + live-deploy Pushover
- `docs/factory.md` — Foreman software factory (sibling Vercel app `joemccann/radon-factory`): label `factory`, draft PR, human merge
- `docs/unusual_whales_api.md` — UW endpoint surface

---

## Behavioral Guidelines

**Think before coding.** State assumptions, surface tradeoffs, ask when unclear. Don't pick silently between interpretations.

**Simplicity first.** Minimum code that solves the problem. No speculative features, abstractions for single-use code, "flexibility" not requested, or error handling for impossible scenarios. If 200 lines could be 50, rewrite.

**Surgical changes.** Touch only what you must. Don't "improve" adjacent code/comments/formatting or refactor unrelated things. Match existing style. Remove orphans YOUR changes created; leave pre-existing dead code alone unless asked. Every changed line should trace to the user's request.

**Goal-driven execution.** Transform tasks into verifiable goals ("add validation" → "write tests for invalid inputs, then make them pass"). State a brief plan for multi-step work. Strong success criteria let you loop independently.

---

## ⛔ Mandatory Rules

1. **Be extremely terse.** See §Response Format — it is a hard rule, not a preference.
2. **Red/green TDD always.** Vitest (unit), chrome-cdp / Playwright (E2E). Target 95% coverage.
3. **E2E browser verification for all UI work.** Primary `chrome-cdp`, fallback Playwright (`web/playwright.config.ts`).
4. **API keys** in `.env` files. Never `~/.zshrc` unless fallback.
5. **No raw hex in UI.** Use brand tokens. 4px max border-radius on panels.
6. **No em dashes in user-facing copy.**
7. **Yahoo is last resort.** Never make Yahoo the scheduled, primary, or only source for a series IB or UW can serve. 2FA, unattended timers, and "historical needs a gateway" do not skip IB or UW. Try IB, then UW, then Robinhood (read-only MCP, when configured), then Yahoo.

## ⛔ Four Gates — Sequential, No Exceptions

| Gate | Rule |
|---|---|
| 1. Convexity | Gain ≥ 2× loss. Defined-risk only. |
| 2. Edge | Specific, data-backed dark-pool / OTC signal that hasn't moved price. |
| 3. Risk | Fractional Kelly. Hard cap 2.5% bankroll / position. |
| 4. ~~No naked shorts~~ | **DISABLED 2026-04-30.** Logic preserved as `_*Impl`. Re-enable: `docs/naked-short-reenable.md`. |

Any gate fails → stop. Name the gate.

## Testing & Verification (TDD is the default)

- Write a failing test that reproduces the bug BEFORE fixing it, then make it pass.
- After any UI change, verify live in the browser (Playwright or claude-in-chrome) and capture a screenshot as evidence — do not claim a fix works based on tests alone.
- If tests pass/fail inconsistently, re-run the suspect test file in isolation before concluding your change caused it; test-ordering pollution and pre-existing flake are common in this repo.
- Always confirm `pwd` before running vitest/pytest — cwd drift has repeatedly produced bogus failures.
- **A gated action is tested at the wire, not at the button.** Any control that fires a network call from behind a guard (a `disabled` prop, an acknowledgement, a confirm step, an `okToSubmit` / `permitted` / `armed` flag) needs a test that clicks it in its ARMED state and asserts the REQUEST — full URL string, method, payload shape — plus a paired assertion that nothing fired while the gate was still closed. Stub `fetch` and render the component that OWNS the fetch, not a presentational child: a test that stops at a `vi.fn()` prop, at `button.disabled === false`, at label text, or at dialog visibility has verified the gate and nothing on the wire. Match the full path (`"/api/admin/services/radon-api.service/stop"`), never `url.includes("/api/…")`, so a wrong unit, action, or endpoint fails. Reference: `web/tests/admin-action-request-assertions.test.tsx`, `web/tests/chain-transmit-gate.test.tsx`.
- **`react-hooks/exhaustive-deps` is a WARNING here and does not block CI.** Treat a `useCallback` handler whose dep array omits a guard or state value its body reads as a defect to fix on sight — that is exactly how an armed Transmit button shipped closed over a stale acknowledgement and silently sent no order (2026-08-27).

## UI Copy Rules

- Never hardcode freshness/cadence copy (e.g. 'Refreshes 5m', 'Updated hourly'). Derive it from the actual job schedule or data timestamp, and grep the whole repo for existing instances of the string before shipping related changes.

## Data Source Priority

1. Interactive Brokers (TWS / Gateway) — real-time
2. Unusual Whales (`$UW_TOKEN`) — dark pool, sweeps, alerts
3. Robinhood (official trading MCP, READ-ONLY; tokens in the 0600 file `$ROBINHOOD_MCP_TOKEN_FILE`, auto-refreshed — access tokens expire ~3 days) — quote/chain failover + retail-crowding overlay only. Never above IB or UW; execution stays on IB. No dark pool, OTC, sweeps, GEX, or vol surface.
4. Yahoo Finance — **ABSOLUTE LAST RESORT**
5. Web scrape — after Yahoo

Never make Yahoo the scheduled, primary, or only source for a series IB or UW can serve. Try IB every cycle. Skip the IB socket only when `/health` `auth_state` is set and not `authenticated`; then UW; then Robinhood (skipped cleanly when unconfigured); then Yahoo. Specialized official feeds (Cboe, Treasury, FINRA) may sit ahead of Robinhood and Yahoo when a script documents them as the source for that metric — the full order is IB > UW > Cboe > Robinhood > Yahoo. Clients live in `scripts/clients/`.

## Credentials

| File | Loaded by | Contains |
|---|---|---|
| `.env` (root) | python-dotenv | MenthorQ creds, Clerk JWKS / issuer / allowlist, optional local archive keys |
| `.env.ib-mode` (root, gitignored) | overlayed after `.env` | `IB_GATEWAY_MODE`, `IB_GATEWAY_HOST` — toggled by `scripts/ib mode local\|cloud` |
| `web/.env` | Next.js | `ANTHROPIC_API_KEY`, `UW_TOKEN`, `EXA_API_KEY`, `CEREBRAS_API_KEY`, Clerk keys |

**Clerk MFA (operator-scoped):** Clerk's MFA policy is "available/optional"; the operator account has TOTP enrolled, so Clerk challenges it on every sign-in, while demo trial users (same instance, no enrolled factor) sign in with the first factor only. Clerk enforces this server-side before minting a session JWT, so Next.js middleware and FastAPI Bearer auth need no MFA-specific code. Do NOT flip the instance policy to "required for all users" — that would force every demo signup through second-factor enrollment.

**IB Flex / Gateway env (Hetzner `/home/radon/radon-cloud/.env` mode `0600`):** `IB_FLEX_TOKEN`, `IB_FLEX_QUERY_ID=1422766` (blotter), `IB_FLEX_NAV_QUERY_ID=1442520` (Activity query "Equity Summary in Base"; carries THREE sections — NAV in Base, Cash Transactions and Transfers — don't repurpose for trade pulls), `IB_GATEWAY_MODE=cloud` (production; FastAPI must not own Compose), `IB_GATEWAY_COMPOSE_DIR=/home/radon/radon/cloud` (monorepo path; not `~/radon-cloud`), `RADON_MODE=hetzner`.

**Verify Flex query ids against the real env before trusting one written down anywhere** — a stale id documented here once pointed the runbook at a query that does not exist for this account. `IB_FLEX_FLOWS_QUERY_ID` is deliberately unset: `_flows_query_id()` falls back to the NAV id and `resolve_flows` reuses the one fetched document, so a run makes ONE Flex request. Setting it to a second id doubles the request rate against a token that has already taken a 24h-to-168h throttle embargo.

**Backblaze B2 (portfolio cold-archive, production required):** `RADON_ARCHIVE_S3_ENDPOINT`, `RADON_ARCHIVE_S3_BUCKET`, `RADON_ARCHIVE_S3_ACCESS_KEY_ID`, `RADON_ARCHIVE_S3_SECRET_ACCESS_KEY`, `RADON_ARCHIVE_S3_REGION` (+ optional `RADON_ARCHIVE_S3_PREFIX`). S3-compatible API to bucket `radon-archive`. Used by `radon-portfolio-archive.service` / `scripts/archive_portfolio_snapshots.py`. Not Cloudflare R2. Full contract: root `.env.example`, `docs/cloud-services.md` "Portfolio archive".

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

**Auto-deploy on push to main.** `.github/workflows/ci.yml` runs the Vitest + pytest gate (including `cloud/tests`) then deploys on green: it SSHes to Hetzner, materializes an immutable `cloud/` runner from the release SHA under `~/.radon-deploy-runners/`, and runs that runner's `deploy.sh '$SHA'`. The deploy job remains bound to the GitHub Environment `Production` for deployment history, URL metadata, environment-scoped configuration, and a main-only deployment branch policy; it has no required-reviewer rule, so no manual approval is needed after the automated gates pass. Host secrets stay at `~/radon-cloud/.env` (`RADON_DEPLOY_ENV_FILE`). After root bootstrap publishes `/var/lib/radon/control-plane-ready`, legacy dual-checkout deploy is retired for new releases; pre-ready SHAs still use the compatibility path. Before `deploy.sh`, the deploy job runs `cloud/scripts/sync-control-plane.sh` → `radon-deploy-root sync-control-plane`, which installs the GitHub-main-tip control-plane bundle (helper, sudoers, polkit, control-plane units, drop-ins) via that tip's own bootstrap, so control-plane edits and root hot-patches need no manual bootstrap (R-430, 2026-08-29). Confirm: `gh run list --workflow=ci.yml --limit 1`. Migration/rollback: `docs/monorepo-cloud-migration.md`. Cutover lessons: `tasks/lessons.md` (2026-07-11). The deploy health-gates the relay restart: before tearing services down (while the current radon-api still serves `/health`), `wait_for_gateway_ready` confirms the IB gateway is authenticated + port_listening (bounded 60s, warn-and-proceed). The relay self-heals on reconnect and raises a `service_health` row (`ib-realtime-relay`) instead of looping silently on no-ticks.

Schema: `scripts/db/migrations/0001_init.sql`. Writers: `scripts/db/writer.{js,py}`. Routes prefer DB, fall back to disk.

## Data Persistence

- All scanner/indicator/journal writes must go through Turso, not host-local disk. Reads should be Turso-first. Host-local SQLite files are ephemeral on the VPS and will silently lose data after deploys.
- When adding a new persisted table, include a migration and verify the row lands in Turso in production before calling the task done.

**Image host:** `https://media.radon.run` (Caddy on Hetzner, fed by laptop rsync over Tailscale). Posts use absolute URLs. Fallback: `RADON_MEDIA_REMOTE=<user>@<prod-host>:/path/to/media/`.

**Trades canonical store:** Turso `journal` table. `/journal` and `/orders` both derive from it. `/orders` uses `web/lib/blotter/fromJournal.ts:journalRowsToBlotter()` with fallback to `data/blotter.json` for legacy rows lacking `realized_pnl`/`cost_basis`/`proceeds`. See `docs/cloud-services.md`.

## Commit Hygiene

- NEVER use `git add -A`, `git add .`, or whole-file/whole-directory staging. Stage only the specific files you edited (`git add path/to/file`), and run `git status` before every commit to confirm no untracked WIP (journals, scratch files, notebooks) was swept in.
- Before pushing, wait for the previous deploy to finish. Do not push rapid-fire commits — cancelled in-flight deploys have corrupted the Next.js build and caused a production outage.

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
| `data/tag_taxonomy.json` | Auto-growing UPPERCASE tag list (gitignored, runtime-owned by newsfeed; canonical tags in Turso `tag_taxonomy`). Untracked 2026-07-15 — was force-tracked, but runtime mutation drifted it and tripped `deploy.sh`'s tracked-drift guard, blocking deploys. |
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

## Response Format

⛔ **This is a HARD FORMAT, not a style preference.** Every closing message uses this shape and nothing else:

```
**Done**
- <one line per outcome, with its evidence inline: count, SHA, status code, path>

**Next**
- <one line per action the user must take, command first>
```

- **100 words max.** Over that, delete lines — do not compress prose. Only a direct request for depth ("explain", "why", "walk me through") lifts the cap.
- **Bullets only.** No prose paragraph anywhere. One line per bullet. No sub-bullets.
- **Omit `Next` entirely when there is nothing for the user to do.** Never pad it.
- **Causes go in the commit message and the PR body, never in chat.** The user reads chat for state and next action; they read the PR for the story. If it explains WHY something broke, it does not belong here.

**Banned outright** — these have all shipped in this repo and each one cost the user a re-read:

| Banned | Instead |
|---|---|
| "Worth knowing…", "Worth noting…", "The sharpest find is…" | cut |
| "Two things that changed…", "Correction to my earlier report…" | fix it in one bullet under Done, no narration |
| A table or code block re-explaining a diagnosis | link the PR |
| A paragraph offering follow-up work | one bullet under Next, or cut |
| Restating what a commit message, PR body, or diff already says | cut |
| Preamble, recap of the request, "I'll now…" | cut |

- Verification is evidence, not a transcript: `8482 passed, 0 failed`, `e40107b4`, `30/30 gating green`. One fragment, inline.
- Mid-task progress messages follow the same shape. Length creep in this repo has always started with "while that runs, here is what I found".

## Trade Output Discipline

- Always `signal → structure → Kelly math → decision`
- State probabilities; flag uncertainty
- Failing gate = stop, name the gate
- **Never rationalize a bad trade**

## Environment Notes

- macOS shell is zsh: quote variables and avoid bare `for f in $(...)` loops (word splitting has broken download loops). Prefer `while IFS= read -r`.
- A background downloads-organizer daemon moves files out of ~/Downloads. After any download, search the organizer's destination folders before reporting a file as missing.
- Only one Chrome instance should be running for browser automation; check which instance is attached before driving the UI.
- PDF rendering: skip WeasyPrint and md-to-pdf (missing native libs / no Chromium). Go straight to headless Chrome.
