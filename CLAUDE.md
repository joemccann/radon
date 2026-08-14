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
- **`factory/CLAUDE.md`** — Foreman software factory (eve on Vercel). Draft PRs only. Rails: `docs/factory.md`.

## Reference docs — read explicitly when needed

- `docs/evaluation.md` — 7-milestone trade eval pipeline + signal interpretation + intraday dark-pool interpolation
- `docs/reports.md` — HTML report templates + P&L card spec
- `docs/ib-gateway-recovery.md` — 2FA push lock + backoff ladder + watchdog self-heal state machine
- `docs/options-structures.{json,md}` — structure taxonomy
- `docs/brand-identity.md` — brand tokens, signal semantics, voice
- `docs/cloud-services.md` — Hetzner runbook (services, schedulers, deploy)
- `docs/grok-page-responder.md` — VPS P1 auto-fix + live-deploy Pushover
- `docs/factory.md` — Foreman software factory: label `factory`, draft PR, human merge
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

## UI Copy Rules

- Never hardcode freshness/cadence copy (e.g. 'Refreshes 5m', 'Updated hourly'). Derive it from the actual job schedule or data timestamp, and grep the whole repo for existing instances of the string before shipping related changes.

## Data Source Priority

1. Interactive Brokers (TWS / Gateway) — real-time
2. Unusual Whales (`$UW_TOKEN`) — dark pool, sweeps, alerts
3. Yahoo — fallback
4. Web scrape — last resort

Never skip to Yahoo / web without trying IB → UW first. Clients live in `scripts/clients/`.

## Credentials

| File | Loaded by | Contains |
|---|---|---|
| `.env` (root) | python-dotenv | MenthorQ creds, Clerk JWKS / issuer / allowlist, optional local archive keys |
| `.env.ib-mode` (root, gitignored) | overlayed after `.env` | `IB_GATEWAY_MODE`, `IB_GATEWAY_HOST` — toggled by `scripts/ib mode local\|cloud` |
| `web/.env` | Next.js | `ANTHROPIC_API_KEY`, `UW_TOKEN`, `EXA_API_KEY`, `CEREBRAS_API_KEY`, Clerk keys |

**Clerk MFA (operator-scoped):** MFA is scoped to the operator account, not required instance-wide. Clerk's MFA policy is set to "available/optional" and the operator account has a second factor (TOTP) enrolled. Clerk always challenges a user who has an enrolled factor, so the operator is MFA-gated on every app.radon.run sign-in; demo trial users (same instance, no enrolled factor) sign in with the first factor only, keeping the demo frictionless. Clerk enforces the challenge server-side and only mints a complete session JWT once any enrolled factor is satisfied, so the Next.js middleware and FastAPI Bearer auth need no MFA-specific code. Do NOT flip the instance policy to "required for all users" — that would force every demo signup through second-factor enrollment.

**IB Flex / Gateway env (Hetzner `/home/radon/radon-cloud/.env` mode `0600`):** `IB_FLEX_TOKEN`, `IB_FLEX_QUERY_ID=1422766` (blotter), `IB_FLEX_NAV_QUERY_ID=1497709` (CashTransactions — don't repurpose for trade pulls), `IB_GATEWAY_MODE=cloud` (production; FastAPI must not own Compose), `IB_GATEWAY_COMPOSE_DIR=/home/radon/radon/cloud` (monorepo path; not `~/radon-cloud`), `RADON_MODE=hetzner`. Journal rehydrate uses query `1442520` via `IB_FLEX_QUERY_ID` at runtime.

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

**Auto-deploy on push to main.** `.github/workflows/ci.yml` runs the Vitest + pytest gate (including `cloud/tests`) then deploys on green: it SSHes to Hetzner, materializes an immutable `cloud/` runner from the release SHA under `~/.radon-deploy-runners/`, and runs that runner's `deploy.sh '$SHA'`. The deploy job remains bound to the GitHub Environment `Production` for deployment history, URL metadata, environment-scoped configuration, and a main-only deployment branch policy; it has no required-reviewer rule, so no manual approval is needed after the automated gates pass. Host secrets stay at `~/radon-cloud/.env` (`RADON_DEPLOY_ENV_FILE`). After root bootstrap publishes `/var/lib/radon/control-plane-ready`, legacy dual-checkout deploy is retired for new releases; pre-ready SHAs still use the compatibility path. Confirm: `gh run list --workflow=ci.yml --limit 1`. Migration/rollback: `docs/monorepo-cloud-migration.md`. Cutover lessons: `tasks/lessons.md` (2026-07-11). The deploy health-gates the relay restart: before tearing services down (while the current radon-api still serves `/health`), `wait_for_gateway_ready` confirms the IB gateway is authenticated + port_listening (bounded 60s, warn-and-proceed). The relay self-heals on reconnect and raises a `service_health` row (`ib-realtime-relay`) instead of looping silently on no-ticks.

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

**Answer only what was asked. Bulleted lists by default.**

- No preamble, no recap of the request, no narration of what you are about to do.
- **Bullets over prose.** Prose paragraphs only when a bullet genuinely cannot carry it.
- **Ship the outcome, not the journey.** No "what surfaced", "worth noting", "interesting", "one thing you should know", "also found", "for the record", "lessons". If it is not the answer to the prompt, cut it.
- No tangents about adjacent bugs, other sessions' work, test flake, or process observations unless they BLOCK the requested task — then one bullet, no story.
- No self-narration of reasoning, corrections, or how hard something was.
- Verification = one line of evidence (counts, status codes, SHAs). Not a transcript.
- Don't restate what a diff already says.
- Follow-ups: at most one line, only if genuinely actionable. Otherwise omit.
- Length target: under ~150 words unless the user asked for depth.

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
