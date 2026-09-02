# Operations Runbook

Live-trading operational concerns: IB Gateway connection modes, background services, watchdogs, deploy flow. The authoritative developer runbook is [`CLAUDE.md`](../CLAUDE.md). The cloud-services architecture deep dive is [`docs/cloud-services.md`](cloud-services.md).

## Environment Variables

### Web app (`web/.env`)

```bash
ANTHROPIC_API_KEY=
UW_TOKEN=
EXA_API_KEY=
CEREBRAS_API_KEY=                       # optional, newsfeed text tagger

# Clerk authentication
# MFA is scoped to the operator account (Clerk policy "optional" + operator has TOTP enrolled),
# NOT required instance-wide. Clerk challenges any user with an enrolled factor, so the operator
# is MFA-gated while demo users (same instance, no enrolled factor) stay frictionless.
# Do NOT set the Clerk policy to "required for all users" — it would force MFA on every demo signup.
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_...
CLERK_SECRET_KEY=sk_...
NEXT_PUBLIC_CLERK_SIGN_IN_URL=/sign-in
NEXT_PUBLIC_CLERK_SIGN_UP_URL=/sign-up
```

### Root `.env`

The variable inventory is owned by the example files, not by this page:
[`.env.example`](../.env.example) (laptop and combined-host root),
[`cloud/.env.example`](../cloud/.env.example) (template for Hetzner
`/etc/radon/env`, including `RADON_HOST_ROLE` and the split-topology
`RADON_IB_REMOTE_*` block) and
[`cloud/config/required-env.txt`](../cloud/config/required-env.txt) (what the
deploy preflight refuses without). Production values: `IB_GATEWAY_MODE=cloud`,
`RADON_MODE=hetzner`, `IB_GATEWAY_COMPOSE_DIR=/home/radon/radon/cloud`;
`IB_GATEWAY_HOST` is loopback on a combined or broker host and the broker's
`10.0.0.4` on an app host. Read Flex query ids from the live env, never from a
doc (root `CLAUDE.md` "Credentials").

**Robinhood token file.** `ROBINHOOD_MCP_TOKEN_FILE` (production `/etc/radon/rh-mcp.json`, `0600`, radon-owned) holds `access_token` / `refresh_token` / `client_id` / `expires_at`; the env vars `ROBINHOOD_MCP_TOKEN`, `ROBINHOOD_MCP_REFRESH_TOKEN`, `ROBINHOOD_MCP_CLIENT_ID` only bootstrap it on first run. The file is rewritten atomically by the client's refresh against `https://api.robinhood.com/oauth2/token/` — it cannot live inside the read-only env file, and it must never be committed. Access tokens expire ~3 days; with no credentials at all every ladder skips Robinhood and falls through to Yahoo.

`scripts/cta_sync_service.py` and `scripts/run_cta_sync.sh` parse `.env` values literally instead of shell-sourcing them, so unquoted secrets containing shell metacharacters (`$`, backticks, etc.) survive the scheduled CTA path.

`.env.ib-mode` overlays `.env` and stores the IB mode toggle from `scripts/ib mode local|cloud`.

### Encrypted credential store (profile Credentials tab)

**The store wins over `.env`.** Keys entered in the profile Credentials tab
are AES-256-GCM-encrypted rows in a host-local SQLite file
`~/.radon/secrets.db` (0600, override `RADON_SECRET_STORE_PATH`) that never
leaves the host — deliberately NOT Turso, so plaintext and ciphertext stay on
the machine that uses them (operator decision 2026-09-01, PR #125; no
migration planned). On FastAPI startup every stored registry name is exported
into `os.environ` over the deployed `.env` value
(`bootstrap_exported_names()` in `scripts/api/routes/credentials.py`), and
subprocesses inherit it. Rotating a key in `.env` alone does nothing while a
stored value exists: rotate in the Credentials tab, or delete the stored
value first. Deleting a stored secret does not unset the already-exported
value in the running process — it takes effect at the next FastAPI restart.

**Master key.** Resolution order: systemd credential
`radon-secret-store-key` in `$CREDENTIALS_DIRECTORY` (production
`LoadCredentialEncrypted=`), then the key file at
`$RADON_SECRET_STORE_KEY_FILE` (default `~/.radon/secret_store.key`,
auto-generated 0600 on first use). There is no escrow: losing the key makes
the ciphertext unrecoverable — back up the key file together with
`secrets.db`, or plan to re-enter every credential. Field inventory:
`scripts/credentials_registry.py`. Implementation: `scripts/secret_store.py`.

### First-run setup wizard (`/setup`)

With NO Clerk key configured and no completion latch, the whole app collapses
to `/setup` plus its API: other pages redirect there and other APIs return
503 `SETUP_MODE` (`web/middleware.ts`, `web/lib/setup/setupMode.ts`). The
wizard is gated by a one-shot token printed to the console that launched
Radon (`RADON_SETUP_TOKEN` overrides it for automation; only read while no
Clerk key is set); `POST /api/setup/complete` consumes it, so a replay is
rejected. It writes collected values into the secret store AND materializes
root `.env` / `web/.env` (`web/lib/setup/envFiles.ts`: Next.js and
python-dotenv need real files at boot). Values are quoted per consumer,
python-dotenv dialect for the root `.env` and `@next/env` dialect for
`web/.env`; a value neither dialect can encode (a newline, or a quote or
backslash mixed with `$`) is refused with an error instead of being written,
so enter it in the Credentials tab or by hand. Writes are temp-file + rename
(never truncate-in-place) and every duplicate occurrence of a managed key is
rewritten. Only the web subset (`WEB_ENV_KEYS` in `envFiles.ts`: the Clerk
keys, Turso, and the model and data API keys) reaches `web/.env`.

**Completion latch.** Completion writes `<repo root>/.radon/setup-complete`
(0600, gitignored) and sets `RADON_SETUP_COMPLETE=1` in the running process;
`web/instrumentation.ts` re-promotes the marker into that flag at every Node
boot (Edge middleware reads only the flag). Setup mode ends the moment the
latch is set, not at restart: until the stack is restarted with the Clerk
keys loaded, every page and API answers 503 `AUTH_MISCONFIGURED` ("Restart
the stack") and the setup APIs answer 403 `SETUP_ALREADY_COMPLETE`. After that
restart the setup surface hard-refuses with 404. Completion returns 500
`SETUP_REPO_ROOT_INVALID` unless the directory above `web/` holds both
`package.json` and `web/package.json`. To re-run the wizard, delete the
marker and unset `RADON_SETUP_COMPLETE` while the Clerk keys are still
absent. The wizard can never activate while any Clerk key exists, so
production is untouched.

## IB Gateway

Three deployment modes selected by `IB_GATEWAY_MODE`:

| Mode | Description |
|------|-------------|
| `docker` (default; local development) | Local `ghcr.io/gnzsnz/ib-gateway` via Docker Compose with `restart: "no"`. Local start/restart paths acquire the shared lease. Reports Docker `container_state` / `container_health`. |
| `cloud` (Hetzner production) | Lifecycle is externally owned by `/usr/local/bin/radon-ib-gateway-control` on the Hetzner VM. FastAPI performs TCP/API health checks only and reports `service_state=reachable` when the port/API path is up; local Compose restart returns 503. |
| `launchd` (legacy) | IBC under macOS launchd. |

**2FA-aware restart.** After every restart, IB Gateway sits at the IBKR Mobile push prompt with the API socket already open, so port probes alone falsely report success. `restart_ib_gateway()` runs an explicit `managedAccounts()` probe; non-empty resets backoff, empty advances it (1m → 2m → 5m → 15m → 30m → 60m capped). `/health` exposes `auth_state` (`authenticated | awaiting_2fa | unreachable | unknown | remote`), `service_state` (`healthy | unhealthy | starting | reachable | unknown`), `upstream_dead`, and `restart_backoff` (attempt count, next attempt in seconds, push lock holder/TTL, last outcome). Schema-v2 `/status` treats nested broker degradation (`awaiting_2fa`, `upstream_dead`, unhealthy service) as aggregate-down even when FastAPI returns HTTP 200, and treats cloud-mode `reachable` as healthy. `POST /ib/reset-backoff` is the operator escape hatch after manually approving 2FA. **Watchdog stuck-2FA self-heal (2026-05-20):** after 3 consecutive `auth_state=awaiting_2fa` cycles with no active push or scheduled retry, the watchdog acquires the cross-process lease and invokes the fixed `radon-ib-gateway-preheld-restart.service` adapter. The adapter consumes that exact lease once and calls `/usr/local/bin/radon-ib-gateway-control`; boot, admin, operator, and laptop cloud starts use the same helper. Never run raw Docker or unmanaged `systemctl restart radon-ib-gateway.service` when the helper is installed.

**Hetzner control boundary.** `radon-ib-gateway.service`, the watchdog adapter, admin controls, boot, and operator commands all call the installed monorepo helper at `/usr/local/bin/radon-ib-gateway-control` (sourced from `/home/radon/radon/cloud`). FastAPI runs with `IB_GATEWAY_MODE=cloud` and must not inspect or mutate the production Compose project directly. Set `IB_GATEWAY_COMPOSE_DIR=/home/radon/radon/cloud`. Secrets are `/etc/radon/env` (`0600`); `/home/radon/radon-cloud/.env` is a compatibility symlink. Root demotion of the helper must run from a radon-readable cwd (never leave cwd as `/root`).

**ib_insync request bounding.** `ib_insync` has no built-in timeout on its async API calls — `qualifyContractsAsync`, `reqHistoricalDataAsync`, and `reqMktData` will block forever when the gateway is logged in but the user session isn't authenticated (the 2FA-pending state). Any script that imports `ib_insync` directly must wrap each await in `asyncio.wait_for(..., timeout=15)` and pre-check `auth_state == "authenticated"` against FastAPI `/health` before instantiating `IB()`. `cri_scan.py` is the reference implementation.

**Client ID ranges.**

| Range | Usage |
|-------|-------|
| 0–9 | FastAPI IBPool (sync=3, orders=4, data=5) |
| 10–19 | WS relay |
| 20–49 | Subprocess scripts AND monitor_daemon handlers — always `client_id="auto"` |
| 50–69 | Scanners |
| 90–99 | CLI |

As of 2026-05-20 monitor_daemon handlers (`fill_monitor`, `exit_orders`, `journal_sync`) use `client_id="auto"` too — the prior 70/71/72 hardcoded daemon range left them one CLOSE_WAIT socket away from "client id already in use" on every transient gateway hiccup. The auto-allocator rotates around in-use IDs.

**Troubleshooting.**

```bash
# Health
curl -s http://localhost:8321/health | python3.13 -m json.tool

# Gateway reachable?
bash -c 'echo > /dev/tcp/ib-gateway/4001' && echo OK || echo FAIL

# Connections on remote host
ssh root@ib-gateway "ss -tnp | grep 4001"

# Fresh client probe
python3.13 -c "from ib_insync import IB; ib=IB(); ib.connect('ib-gateway',4001,clientId=99,timeout=10); print('OK'); ib.disconnect()"
```

**Management commands** (laptop alias → SSH-wrapped; same names on the VPS):

| Command | Action |
|---------|--------|
| `ibstart` | Start container, wait for port 4001 |
| `ibstop` | Stop and remove container |
| `ibrestart` | Restart container |
| `ibstatus` | Container state, port check, active connections |
| `iblogs [N]` | Tail container logs |
| `ibhealth` | Docker healthcheck status |

Deeper troubleshooting and full Docker setup live in [`docs/ib-gateway-docker.md`](ib-gateway-docker.md) and [`docs/ib-connection-troubleshooting.md`](ib-connection-troubleshooting.md).

## Background Services

Hetzner host systemd is the production surface. Laptop dev uses launchd plists in `config/`. Laptop `com.radon.data-refresh` must stay unloaded. VPS `radon-flow-refresh.timer` owns hourly scanner/discover/flow during ET RTH.

**Nightly loops on the Mac mini** (launchd, staggered 10 minutes apart, audit then remediate). Each runs in its own clone under `~/radon-weekend/` that hard-resets to `origin/main` every phase, uses a per-loop venv (`~/radon-weekend/venv-<loop>`) plus the shared `~/radon-weekend/.env`, and holds a per-clone `.weekend-runner.lock`. A wrapper refuses the clone unless it carries BOTH `.radon-weekend-runner` and that loop's own `.radon-<loop>-runner` marker, so pointing one loop at another's clone is a `REFUSED`, not a cross-run collision. The shared `.env` is not imported wholesale: each wrapper's `_notify_curl` reads only `PUSHOVER_USER` and `PUSHOVER_TOKEN` from it in bash and pages via `/usr/bin/curl` (never python). Model spend rides the claude.ai subscription only: every wrapper unsets each API-key / auth-token / base-URL / Bedrock / Vertex / Foundry / gateway variable the installed Claude Code honors (naming it on stderr and as `ignored=` on the phase-start line, never the value) and runs anyway, scrubs those lines out of a provisioned `web/.env` in place (except the security loop, whose clone is credential-free: any `.env` / `.env.ib-mode` / `web/.env` present there is `REFUSED`, not scrubbed), and `REFUSED`s only what `unset` cannot reach: a `.deepsec/.env*` / `.env.local` key line or a Claude Code settings-level `apiKeyHelper` / `env` reroute. Never point another job, worktree, or responder at these clones. The `Fires` column is generated from each plist's `StartCalendarInterval`. Loop semantics live in `.claude/skills/<loop>/SKILL.md`; wrapper mechanics in the wrapper script; state on the rolling GitHub issue carrying the label.

| Loop | Fires (local) | Clone | Wrapper / plist | Issue label |
|---|---|---|---|---|
| reliability | 00:00 | `~/radon-weekend/radon` | `scripts/reliability_weekend.sh` / `config/com.radon.reliability-daily.plist` | `reliability-nightly` |
| testing | 00:10 | `~/radon-weekend/radon-testing` | `scripts/testing_weekend.sh` / `config/com.radon.testing-daily.plist` | `testing-nightly` |
| ci-performance | 00:20 | `~/radon-weekend/radon-ci-performance` | `scripts/ci_performance_nightly.sh` / `config/com.radon.ci-performance-daily.plist` | `ci-performance-nightly` |
| documentation | 00:30 | `~/radon-weekend/radon-documentation` | `scripts/documentation_nightly.sh` / `config/com.radon.documentation-daily.plist` | `documentation-nightly` |
| security | 00:40 | `~/radon-weekend/radon-security` | `scripts/security_nightly.sh` / `config/com.radon.security-daily.plist` | `security-nightly` |

**The loops pin their own model. The global default does not reach them.** Every wrapper passes `--model` explicitly, so changing `model` in `~/.claude/settings.json` has no effect on any nightly run. The rungs are tried in order. A rung is dropped when that round's log shows the Claude CLI credits line, You've hit your Opus/Sonnet limit, Request rejected (429), 529 Overloaded, or experiencing high load (not a session or weekly cap, which is shared across models, and not the CLI tool-skip categories (rate-limited) and (overloaded), which retry the same model). Override the whole ladder with `RADON_WEEKEND_MODEL_LADDER` (space-separated model ids) in the wrapper's environment. Do not hand-edit a wrapper while a cycle is running: the shell reads the script incrementally and an edit strands the live run at a stale byte offset. The security loop additionally exports its current rung as `RADON_WEEKEND_MODEL` (re-exported after every drop), because its skill spawns a second `claude` for the Claude Security scan and a `--model` flag on the wrapper does not reach a child process; that scan reads the export. When every rung is exhausted the security loop reports `INCOMPLETE (all model quotas exhausted; top up at claude.ai/settings/usage)` and exits 75, and the other four report the same text under `FAILED`. Either way no audited SHA advances and the next fire resumes the same phase. The default ladder is pinned by `scripts/tests/test_weekend_model_ladder.py`; it is not repeated here.

**Subscription only.** A reroute variable (key, token, base URL, Bedrock/Vertex/Foundry/gateway flag) in the launch environment is IGNORED, not fatal: the wrapper names it on stderr and as `ignored=` on the phase-start line (never the value), unsets it, and runs on the claude.ai subscription. Reroute lines in a provisioned `web/.env` (the product assistant key) are scrubbed out of the clone copy in place, same inode. The wrapper REFUSEs only what `unset` cannot reach: a key line in a gitignored env file a scanner reloads itself (`.deepsec/.env*`, `.env.local`) or a Claude Code settings-level `apiKeyHelper` / `env` reroute. Flag variables like `CLAUDE_CODE_USE_BEDROCK` count only when truthy (1/true/yes, with or without dotenv quotes); 0/false/no lock those reroutes off. Exception: the security clone is credential-free by rail — any `.env`, `.env.ib-mode`, or `web/.env` present in it is a `REFUSED` exit 2, never a scrub.

**Dead-man reporting.** Each phase posts one runner-health comment (`**PHASE** STAMP **status**`, optional detail) on its rolling issue and one Pushover. That comment is not the three-section write-up. A missing issue comment is itself the dead-man signal. The issue is created once with a timeless rolling-dead-man description; run history stays in comments; the wrapper does not `gh issue edit` the body. The vocabulary is `OK`, `TRUNCATED` (the harness killed background work but the agent still exited 0), `TIMEOUT after <cap>s`, `FAILED (exit N)`, `CRASHED (exit N)` (the wrapper died first), `KILLED (SIG…)`, `REFUSED …` (a rail rejected the clone or environment) and `GROUND TRUTH FAILED` (the clone could not be refreshed, so the phase never ran). `INCOMPLETE` exists only in the testing loop (agent exited 0 with no commit on the nightly branch) and the security loop (agent exited 0 without printing the phase-completion marker, or every model rung reported an exhausted quota — a provider spend stop, which that loop's skill classifies as incomplete and resumable, never failed); the other three have no `INCOMPLETE` arm. Every security `INCOMPLETE` exits 75 and says the audited SHA was not advanced and the next fire resumes the same private run. None of the five paste a run-log tail. Pushover is in-wrapper `_notify_curl` via `/usr/bin/curl -q` (`-q` argv[1], so a planted curlrc cannot intercept) with `PUSHOVER_TOKEN` / `PUSHOVER_USER` off curl argv and off disk (piped `--config -`). `gh` and `timeout` are resolved before the venv is prepended to PATH (`timeout` next to `net_bounded`, before `--lock-lib-only`); Pushover user/token are snapshotted next to `GH_BIN` from WEEKEND_ROOT/.env before the agent. `report()`, `net_bounded`, and the agent wall-clock invoke those snapshots. `_notify_curl` `--config` values escape backslash, double-quote, and newlines. The security loop sanitizes with `/usr/bin/sed` and is wrapper-only (the agent does not `gh issue comment`); the other four agents still post the three-section issue update (Issue discovered / What was done / Next).

| Service | Cadence | Purpose |
|---------|---------|---------|
| `radon-ib-gateway` | always-on | Broker session for live quotes, execution, reports |
| `radon-api` | always-on | FastAPI on `:8321` |
| `radon-relay` | always-on | IB realtime WebSocket relay on `:8765` |
| `radon-nextjs` | always-on | Next.js terminal at `app.radon.run` |
| `radon-newsfeed` | 120s loop | Headless Playwright scraper for The Market Ear |
| `radon-monitor` | 30s loop | Fills, exit orders, journal sync, cash flow handler |
| `radon-health` | always-on | **Isolated** stdlib health daemon on `:8330` (see Health monitoring below). NO dependency on `radon-ib-gateway`; a Gateway stop never touches it. |
| `radon-refresh.timer` | 60s | Schedules data-refresh sweeps |
| `radon-vcg-refresh.timer` | Mon-Fri 13-21 UTC every 5 min | Autonomous VCG scan |
| `radon-portfolio-sync.timer` | Mon-Fri 13-21 UTC every 60s | Autonomous portfolio sync |
| `radon-cta-sync.timer` | Mon-Fri 18:15 / 19:00 / 21:30 UTC | MenthorQ CTA refresh |
| `radon-bpi.timer` | Mon-Fri 21:30 / 23:30 UTC; Tue-Sat 11:00 UTC | BPI after the close, same-evening Yahoo catch-up, morning catch-up |
| `radon-ma-ratio.timer` | daily 22:45 UTC | SPX pct above 50d MA over pct above 200d MA (after the close; 5 min behind divyield). Spec: [`indicators/ma-ratio.md`](indicators/ma-ratio.md) |
| `radon-iv-spread.timer` | daily 22:15 UTC | NDX minus SPX 1M ATM implied vol spread from IB (after the close; between ivrank and dispersion). Spec: [`indicators/iv-spread.md`](indicators/iv-spread.md) |
| `radon-watchdog-{intraday,continuous,daily,error}.timer` | varies | Service-health alerting (Pushover) |
| `radon-host-metrics.timer` | every 1 min | Host CPU, memory, loop lag. Details: [`cloud-services.md`](cloud-services.md#host-metrics-dur-12) |
| `radon-equibles-{13f,ats,cot,filings,short-crowding}.timer` | daily / weekly | 13F, ATS, COT, filings, short crowding. Spec: [`equibles-api.md`](equibles-api.md) |
| `radon-vol-cone.timer` | Mon-Fri 20:45 UTC | Completed-session cheap-wing cone (16:45 ET, after the close grace). Spec: [`indicators/vol-cone.md`](indicators/vol-cone.md) |
| `radon-vol-cone-intraday.timer` | Mon-Fri 09:00-16:30 ET every 15 min | Live sample ranked against that stored cone, so the tab is tradeable during the session instead of a day stale. Holds without spending a UW request outside market hours or under a nearly-spent daily budget, and a held pass no longer republishes the shared `vol-cone` snapshot. The 16:45 ET slot is deliberately absent: in EDT it is 20:45 UTC, the EOD writer's own minute (R-128). |
| `radon-vixcor.timer` | daily 02:35 UTC | VIX x COR3M 20-session correlation, 15 min behind `radon-cor`. Spec: [`indicators/vixcor.md`](indicators/vixcor.md) |
| `radon-credit-spread.timer` | daily 21:45 UTC | HYG vs SPX credit-equity series. IB first, then UW, then Robinhood (when configured), then Yahoo. Spec: [`indicators/credit.md`](indicators/credit.md). Units are in `setup-vps.sh`; root install-copy still owed. |
| `radon-iei-hyg.timer` | daily 21:55 UTC | IEI/HYG duration-vs-credit ratio. Spec: [`indicators/iei-hyg.md`](indicators/iei-hyg.md) |
| `radon-leap.timer` | Mon-Fri 10:00 ET | LEAP IV-mispricing scan via FastAPI. Capacity-shed case: [`incident-runbook.md`](incident-runbook.md) |
| `radon-garch.timer` | Mon-Fri 14:00 / 17:00 / 20:00 UTC | GARCH convergence scan via FastAPI, 3x per RTH session. Capacity-shed case: [`incident-runbook.md`](incident-runbook.md) |
| `radon-incident-watchdog.timer` | every 5 min | Writes `data/incidents/`. Cases: [`incident-runbook.md`](incident-runbook.md) |
| `radon-grok-page-responder.timer` | 30s after last cycle | Headless Grok auto-fix from dedicated clone. Spec: [`grok-page-responder.md`](grok-page-responder.md) |

The autonomous timers retired Radon's previous "data only refreshes when a browser tab is open" failure mode. Some surfaces remain on-demand by design (`scanner`, `discover`, `flow-analysis`, `analyst-ratings`, `gex-scan`, `orders-read-compare`).

**Operator CLI.** `/usr/local/bin/radon` wraps every loaded `radon-*` unit **except `radon-health`**. Auto-enumerates via `systemctl list-units 'radon-*'` (then filters out `radon-health.service`), so new timers don't require script edits. `radon-health` is deliberately excluded so the health daemon keeps reporting while `radon stop|restart` cycles the trading stack — manage it explicitly with `systemctl restart radon-health`.

```bash
radon stop      # stop IB + all radon-* units
radon start     # start them all (IB Gateway first)
radon restart
radon status
```

From the laptop: `ssh root@ib-gateway radon stop`. The operator CLI is installed from the monorepo [`cloud/scripts/operator-radon.sh`](../cloud/scripts/operator-radon.sh) control-plane source.

## Health monitoring (isolated daemon + edge surface)

The health surface is **decoupled from the trading stack** so it keeps reporting precisely when the stack is down. Two layers plus an off-box witness:

- **`radon-health.service`** (`scripts/health_service/`, stdlib-only) — a standalone daemon on `127.0.0.1:8330` with **no `Requires=`/`After=radon-ib-gateway`**. (Since 44e89e1b no app unit is `PartOf=` the Gateway either: `radon-api`, `radon-relay`, `radon-monitor` carry `After=` ordering only, so a Gateway stop or 2FA restart leaves the app plane running. A unit that IS cleanly stopped does not `Restart=always` back; use `radon restart`.) `Restart=always` + `StartLimitIntervalSec=60`/`StartLimitBurst=5` so a crash-loop parks as `failed`, not an invisible hot-loop. Imports **nothing** from the trading stack (enforced by a subprocess isolation test).
  - `GET /healthz` — zero-I/O static `200` (liveness pin).
  - `GET /status` — **always `200`**; concurrent live probes (`radon-api` via `/health/lite`, relay/Next.js/IB-gateway TCP) + cached `systemctl` unit states (`active(exited)` reads `up`) + the Turso `service_health` table (read over stdlib libSQL HTTP — no libsql import; degrades to `unknown` on any failure). Degraded sources are body fields, never error codes.
- **Caddy edge** (`app.radon.run`): `GET /edge-health/ping` — static `respond "ok" 200`, the **never-502 floor** (depends only on Caddy). `GET /edge-health/status` → `reverse_proxy 127.0.0.1:8330`. **Caveat:** every failure mode of `/edge-health/status` is ALSO `200`: an upstream 5xx (`handle_response @down`) and a dial-refused daemon (`handle_errors`, the Caddy-synthesized 502) are both rewritten to `{"reachable":false,"observer":"caddy"}`, i.e. `200` with `reachable:false` and no `ok` field. A status-code-only uptime monitor therefore reads UP in every state except Caddy dead: pin the external monitor on the body (`ok` is a boolean and `overall_state` is `up`), never on the status code. The repo prober already does (`scripts/health_probe/probe.py` `_classify_status_payload` treats the synthetic body as `invalid`). `/edge-health/ping` is the guaranteed floor.
- **Off-box prober (Tier-3):** `.github/workflows/external-health-probe.yml` (GitHub Actions, `*/5`) hits the public edge from off the VPS and UPSERTs to the Turso `external_probe` table (`scripts/health_probe/`), so a whole-box outage is still recorded externally. `reader.py` is the dead-man's-switch (flags stale `external_probe` rows). Needs repo secrets `TURSO_DB_URL`/`TURSO_AUTH_TOKEN`.

**Consumers:** the always-on IB status chip (`web/lib/IBStatusContext.tsx`) reads `/edge-health/status` in prod (falls back to `/api/admin/health` in dev / as a prod safety net). The admin panel stays on `/api/admin/health` (needs `managed_accounts`). The `/health` payload itself is **trust-scoped**: public/proxied callers get `{"status":"ok"}` only; account/state detail goes to trusted peers only (loopback, tailnet `100.64.0.0/10`, Hetzner private net `10.0.0.0/16`; never a request carrying reverse-proxy forwarding headers). Any watchdog or off-box probe that needs the full payload must originate from one of those peers, not via Caddy. See `scripts/api/CLAUDE.md` and `scripts/health_service/CLAUDE.md`.

**Recovery heartbeat:** the `awaiting_2fa → authenticated` pool reconnect (`pool.reconnect_all`) is driven server-side by a FastAPI lifespan task (`_ib_recovery_heartbeat_loop`, 15s) — independent of any browser poll, since the chip is now a read-only consumer. The every-minute `radon-ib-watchdog` `/health` curl is the slower backstop.

## Service Health & Watchdogs

Every dual-write service writes a row to the `service_health` Turso table on every cycle, including no-op short-circuits. The Next.js `<ServiceHealthBanner />` reads the latest row per service and renders a category-aware banner.

| Category | Stale state |
|----------|-------------|
| `scheduled` | Red — banner alerts; treated as outage |
| `on-demand` | Amber — dormant chip; suppressed from alerts |

Staleness windows live in `web/lib/serviceHealthWindows.ts`. Cycle-driven writers (`newsfeed-scraper`, `journal-sync`, `cri-scan`) use tight windows (~cadence × 3). Event-driven writers (`replica-watchdog`, `watchdog-alerts`) use 24h windows because "no event" is the healthy state. Equibles writers are registered there: daily 26h (`equibles-short-crowding`, `equibles-filing-forensics`), weekly 8d (`equibles-13f`, `equibles-ats-venue-share`, `equibles-cot-positioning`). An `ok` row with null `last_error` that still renders stale is a registration gap, not a dead writer.

**A row that NEVER appears is worse than a stale one.** A registered service whose row was never written renders as an outage the banner cannot explain and the watchdog cannot attribute. All five Equibles producers hit exactly that: `EquiblesClient()` raises `EquiblesAuthError` at construction when `EQUIBLES_API_KEY` is unset or rejected, and the construction sat OUTSIDE the block that owns health reporting, so each oneshot died before any `_record_health` call. No `service_health` row was written at all — not even an error row — and with the key then missing from `cloud/config/required-env.txt` the deploy preflight passed happily. Every producer must construct its client, resolve its ticker universe, and take its `parser.error(...)` exits INSIDE the health-reporting block, so an auth failure, an exhausted allowance, or an empty watchlist all leave an `error` row before the process exits. `EQUIBLES_API_KEY` is in `cloud/config/required-env.txt` as of PR #104, so the deploy preflight gates on it and the per-producer `error` heartbeat is the second layer that catches a key the API rejects at run time. `scripts/tests/test_service_registration_completeness.py` enforces the construction rule statically. The units and operator checks are in [`cloud-services.md`](cloud-services.md) §Equibles.

**A partial cycle is not a healthy cycle (R-294 / R-295).** Both `fetch_equibles_ats_venue_share` and `fetch_equibles_short_crowding` used a write gate that passes on ONE ticker: `payload_has_data` / `is_payload_valid` only test that the row list is non-empty. A run that served 3 of 40 tickers and then hit the daily allowance therefore wrote `ok` AND replaced the complete snapshot underneath it, and looked identical to a full cycle in `service_health` and on the panel. Both now apply a coverage gate — below `MIN_COVERAGE_RATIO` (60%) of the resolved universe the cycle records `error`, keeps the previous snapshot, and persists `requested` / `covered` / `failed` in the health row (including on the abort path, since a failure at ticker 3 and one at ticker 39 are different operational facts). The ratio is scoped to universes of `MIN_UNIVERSE_FOR_RATIO` (5) or more: one failure out of two is 50%, and a two-ticker run is a deliberate `--tickers` override rather than the scheduled sweep.

**A wall-clock budget abort is a third class, and it is not an allowance failure.** `fetch_equibles_ats_venue_share` and `fetch_equibles_filing_forensics` each bound the whole sweep with `SWEEP_BUDGET_S` and each call with `TICKER_FETCH_BUDGET_S`, so a tarpitted Equibles endpoint no longer runs into the unit's `TimeoutStartSec`. At the deadline the process abandons the in-flight request, records the unreached tickers with `code: "budget"`, heartbeats `error`, and exits — leaving them for the next fire. An `error` row whose deferred tickers carry `code: "budget"` means the endpoint was slow, NOT that the key, the allowance, or the watchlist is wrong: re-running walks into the same tarpit, and raising `TimeoutStartSec` is explicitly the wrong move ([`incident-runbook.md`](incident-runbook.md) `equibles-ats-sweep-timeout`). The budget values live in the two scripts and are pinned against their units by `test_sweep_budget_fits_inside_unit_start_timeout`.

**A rate limit is cycle-fatal, not a ticker gap.** `EquiblesRateLimitError` and `EquiblesAuthError` both subclass `EquiblesAPIError`, which is what the per-ticker handlers caught. Once the allowance is exhausted every remaining ticker fails for the same reason, so the loop recorded 37 individual "errors" for one condition and still finished. Both now propagate out of the per-ticker handler; a rejected key is tried once, not once per ticker. A genuine per-ticker condition (a 404 on a delisted name) still isolates.

**Ticker scope.** Both producers refresh only the Turso `watchlist` table, while `/api/equibles-smart-money-13f` and `/api/equibles-filing-forensics` accept any ticker. An off-watchlist ticker has no row, and both routes serve `missing: true` — the same shape a watchlist ticker gets when no institution has filed.

**Incident artifacts.** `scripts/incident_watchdog` writes `data/incidents/incident-*.json`. Laptop `com.radon.incident-responder` (`scripts/incident_responder.py`, 10 min) mirrors to `data/incidents_remote/` and analyzes open files older than 12 min. Cases: [`incident-runbook.md`](incident-runbook.md). Triage: `/incident <path>`.

**Grok P1 responder (VPS clone).** A delivered watchdog P1 also inserts `watchdog_pages`. `radon-grok-page-responder.timer` runs headless Grok from `/home/radon/radon-page-responder` and, unless the runbook says stand down, TDD-ships. After the live deploy gate, `scripts/deploy_notify.py` sends `radon deploy live` (priority 0). Spec: [`grok-page-responder.md`](grok-page-responder.md).

**Probe bearer.** `/api/service-health` is Clerk-protected. The loopback nextjs-db-watchdog sends `Authorization: Bearer $RADON_PROBE_FRESHNESS_TOKEN`. HTTP 401/403 is unknown (auth perimeter), never a Turso wedge. Do not add the route to `isPublicRoute`.

**Watchdog** (`scripts/watchdog/`) runs in four buckets (`intraday`, `continuous`, `daily`, `error`), each with its own timer. Alerts route to Pushover (P1 only) with per-service cooldown and hysteresis, plus an always-on `watchdog-alerts` row in `service_health` so the dashboard banner reflects fires even without an external channel. A delivered P1 also inserts `watchdog_pages`; laptop `com.radon.grok-page-responder` (30s) runs headless Grok to diagnose and, unless the runbook says stand down, ship a fix. After a release passes the live deploy gate, `scripts/deploy_notify.py` sends a normal-priority Pushover (`radon deploy live`, never P1). Ack with `python -m scripts.watchdog ack <service>`. The `error` bucket explicitly skips `watchdog-alerts` itself to avoid recursive alerting. (Discord support was removed 2026-05-19.)

**Banner humanization.** `service_health.last_error` JSON payloads are rewritten into operator-friendly copy before render (`humanizeServiceHealthError` in `web/lib/serviceHealthError.ts`).

**Database access pattern (post-2026-05-20):** every Radon process now goes direct-to-cloud — the code default since DUR-07 (replica opt-in only via `RADON_DB_USE_REPLICA=1`), with the `RADON_DB_NO_REPLICA=1` kill switch applied fleet-wide through the `radon-.service.d/common.conf` prefix drop-in. The libsql embedded-replica architecture (`data/replica.db`) was retired after multi-writer WAL contention and then single-writer frame conflicts between the replica owner and direct-cloud writers. Reads cost +30–60 ms per cloud round-trip, absorbed by SWR caching. The `replica_watchdog` handler still exists in `monitor_daemon` as a vestigial safety net (it sits idle in the no-replica world), but `data/replica.db` itself should not exist on any host. See `feedback_libsql_replica_one_writer.md`.

**Market-hours gate.** Handlers tagged `requires_market_hours=True` (`fill_monitor`, `exit_orders`, `journal_sync`) only run during 09:30-16:00 ET. The daemon converts UTC to ET via `zoneinfo.ZoneInfo("America/New_York")` so DST is handled automatically; a fail-open UTC-5 fallback fires only if the host is missing `tzdata`. Never hardcode a fixed offset for ET — it silently shifts the window 1h every DST season.

## Cash Flows

`scripts/cash_flow_sync.py` pulls `CashTransaction` rows from IBKR Flex (`IB_FLEX_NAV_QUERY_ID=1497709`) and upserts into the `cash_flows` Turso table. Surfaces on `/orders` via `web/components/CashFlowsSection.tsx`.

**Cadence:** once per ET trading day at 17:00 ET (1h after the close). Flex publishes once per day, so faster polling buys nothing. Holidays and weekends are skipped via `utils.market_calendar`. Late-fires past 18:00 ET if the daemon was off.

**Throttle backoff.** Flex codes 1001 / 1018 / 1019 raise `FlexThrottleError` on the first hit (no internal retry, no sleep), and the handler advances an exponential breaker (24h → 48h → 72h → 168h capped) persisted across daemon restarts. The breaker composes with the daily window; embargo expiry waits until the next 17:00 ET slot.

## Deployment

`git push origin main` triggers `.github/workflows/ci.yml`. Superseded test jobs
may cancel independently, but the production deploy job uses a non-canceling
`deploy-production` concurrency group. It SSHes to Hetzner as `radon`, extracts
`cloud/` from the tested `${{ github.sha }}` into an immutable support runner at
`/home/radon/.radon-deploy-runners/<sha>.<run>/cloud`, and invokes that runner's
deploy script. The monorepo [`cloud/`](../cloud/) directory is the canonical
source for deploy code, systemd units, Caddy, and the IB Gateway Compose
project; host secrets are `/etc/radon/env` (`0600`), with
`/home/radon/radon-cloud/.env` a compatibility symlink.

The deploy holds its activity lock, validates the external env and target SHA,
then verifies the installed root control plane against its manifest **before**
any dependency build, service stop, or transition journal mutation. It builds
in a detached target-SHA worktree, journals and restores the active topology,
and gates FastAPI `/health/lite`, Next.js HTTP, relay TCP/HTTP, and stable core
service restart counts. IB state remains advisory.

The deploy job is capped at 60 minutes and SSH at 55 minutes. The inner deploy
gets 900 seconds plus a 30-second kill window. Root mutation actions get 180
seconds, verify/commit actions get 30 seconds, and lifecycle-lock contention may
consume 190 seconds once per recovery. The tested double-recovery bound is
2,150 seconds, leaving at least ten minutes inside SSH for file restoration and
gate overhead.

Git HEAD equality alone is not success. Confirm the durable release with
`gh run list --workflow=ci.yml --limit 1`. For the exact privileged bootstrap,
recovery, and rollback sequence, follow [`cloud/CLAUDE.md`](../cloud/CLAUDE.md)
and [`docs/monorepo-cloud-migration.md`](monorepo-cloud-migration.md) rather
than duplicating commands in this runbook.

## Production Build Constraint

Next.js 16 prerender crashes on `/_global-error` and `/_not-found` because the root ClerkProvider context isn't materialised in isolated workers. `web/package.json` build pins `next build --experimental-build-mode=compile`. The error and not-found shells (`app/error.tsx`, `app/[ticker]/not-found.tsx`, `app/global-error.tsx`) use plain `<a>` and pure JSX (no `next/link`, `useEffect`, or `globals.css`) for the same reason.
