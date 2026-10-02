# Operations Runbook

Live-trading operational concerns: IB Gateway connection modes, background services, watchdogs, deploy flow. The authoritative developer runbook is [`CLAUDE.md`](../CLAUDE.md). The cloud-services architecture deep dive is [`docs/cloud-services.md`](cloud-services.md).

## Environment Variables

### Web app (`web/.env`)

```bash
# ANTHROPIC_API_KEY is NOT required: subscriptions only (2026-09-18), the
# Claude Max grant in ~/.claude/.credentials.json is the meter.
UW_TOKEN=
EXA_API_KEY=
CEREBRAS_API_KEY=                       # optional, last-rung model ladder

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

**Robinhood token file.** `ROBINHOOD_MCP_TOKEN_FILE` (production `/var/lib/radon/rh-mcp/rh-mcp.json`, `0600`, radon-owned, in a `0700` radon-owned dir that `radon-app-runtime.sh` binds read-write into the radon-api container and points the container's `ROBINHOOD_MCP_TOKEN_FILE` at; host timers and the API share this one store, so the rotating refresh token is never split) holds `access_token` / `refresh_token` / `client_id` / `expires_at`; the env vars `ROBINHOOD_MCP_TOKEN`, `ROBINHOOD_MCP_REFRESH_TOKEN`, `ROBINHOOD_MCP_CLIENT_ID` only bootstrap it on first run. The file is rewritten atomically by the client's refresh against `https://api.robinhood.com/oauth2/token/` — it cannot live inside the read-only env file, and it must never be committed. Access tokens expire ~3 days; with no credentials at all every ladder skips Robinhood and falls through to Yahoo.

`scripts/cta_sync_service.py` and `scripts/run_cta_sync.sh` parse `.env` values literally instead of shell-sourcing them, so unquoted secrets containing shell metacharacters (`$`, backticks, etc.) survive the scheduled CTA path.

**Service token.** `RADON_SERVICE_TOKEN` is the shared Next.js to FastAPI bearer. When set, `radonFetch` (`web/lib/radonApi.ts`) sends it as `X-Radon-Service-Token` and `is_trusted_service_request` (`scripts/api/auth.py`) admits the caller as a trusted service. It is set only on the demo deployment (Vercel `radon-demo` project plus the demo VM `.env`, same value in both; see `docs/demo-environment.md`). Leave it unset on prod: there the header is never sent and the API stays loopback/Clerk JWT gated.

`.env.ib-mode` overlays `.env` and stores the IB mode toggle from `scripts/ib mode local|cloud`.

### Encrypted credential store (profile Credentials tab)

**The store wins over `.env`.** Keys entered in the profile Credentials tab
are AES-256-GCM-encrypted rows in a host-local SQLite file. Host mode defaults
to `~/.radon/secrets.db`; the production container pins
`/home/radon/radon/data/secret_store/secrets.db` on its persistent data bind
(`0600`, override `RADON_SECRET_STORE_PATH`). The store never leaves the host —
deliberately NOT Turso, so plaintext and ciphertext stay on the machine that
uses them (operator decision 2026-09-01, PR #125; no migration planned).
`radon-app-runtime` creates the `data/secret_store/` directory without
following symlinks (`0700`, owned by the app user) and exits 78 if the path
is a symlink or unusable.
Before Uvicorn starts, `scripts/db/migrate.py --boot` applies pending Turso
migrations in a killable child bounded at 20s. A real migration error blocks
startup. A Turso stall boots only when `data/schema_version.prod` shows the
schema is already at the release's newest migration; unverified schema fails
closed (exit 75) and systemd retries.
Then `scripts/secret_store.py` opens the configured store and
authenticates every encrypted row; a missing, replaced, or malformed key fails
the unit instead of starting credential-degraded. After that preflight, every
stored registry name is exported into `os.environ` over the deployed `.env`
value (`bootstrap_exported_names()` in
`scripts/api/routes/credentials.py`), and subprocesses inherit it. Rotating a
key in `.env` alone does nothing while a
stored value exists: rotate in the Credentials tab, or delete the stored
value first. Exception: the IB Gateway password. Saving it in the tab does
not rotate what the Gateway reads (`TWS_PASSWORD_FILE` / docker secrets).
The Next.js process that proxies a save also applies the per-request web keys
(`UW_TOKEN`, `ANTHROPIC_API_KEY`, `CEREBRAS_API_KEY`, `XAI_API_KEY`,
`EXA_API_KEY`; `LIVE_WEB_ENV_KEYS` in `web/lib/setup/envFiles.ts`) to its own
`process.env` and `web/.env`, so they take effect without a restart. Another
Next.js instance (laptop vs Hetzner) still picks a change up only at its next
restart, and the boot-read Clerk / Turso keys always need one.
The tab also refuses a `TURSO_DB_URL` that is not `libsql://` or `https://`,
whose host is not under `*.turso.io`, or whose host differs from the
`TURSO_DB_URL` already in the environment;
point a deployment at a different Turso database by editing `.env` and
restarting, not from the tab. Deleting a stored secret does not unset the
already-exported value in the running process — it takes effect at the next
FastAPI restart.

The LLM regime collector runs as a separate systemd process, so
`radon-ai-cycle.service` loads the same encrypted store and master key directly.
Its Profile group exposes OpenRouter, Artificial Analysis (including the fixed
model basket), Vast.ai, EIA and the SEC contact user agent. Stored values win
over `/etc/radon/env` on the collector's next run.

The subscription-token vault reuses this same store rather than adding a second
crypto system. `scripts/subscription_tokens.py` seals each agent CLI's OAuth
credential file verbatim under the registry names
`SUBSCRIPTION_TOKEN_ANTHROPIC`, `SUBSCRIPTION_TOKEN_CODEX`,
`SUBSCRIPTION_TOKEN_GROK` and `SUBSCRIPTION_TOKEN_GEMINI`, and restores or
refreshes them on a timer. A store that fails to open is reported as
`store_unavailable` (exit 78), never as an empty vault. Runbook:
[`docs/subscription-tokens.md`](subscription-tokens.md).

**An unopenable store is reported, never silently skipped.** A store that fails to open after the preflight used to fall back to the deployed `.env` values without a word, so a rotated credential kept serving the stale one. `bootstrap_exported_names()` now surfaces the failure instead of degrading quietly. The setup flow's two env files (`web/lib/setup/envFiles.ts`) are written as a pair that rolls back, so an interrupted save can no longer leave one file updated and the other stale, and the setup token now expires after `SETUP_TOKEN_TTL_MS` (1h from first use, `web/lib/setup/setupToken.ts`), so an abandoned wizard cannot leave a credential-writing token alive for the process lifetime.

The first container cutover is a one-time migration: before any restart, copy
the live container's `~/.radon/secrets.db` and its exact matching key into
`data/secret_store/`, verify every row decrypts there, and only then install the
encrypted credential and restart. Never let the `--rm` container disappear
first. Both host and container units pin the same persistent database path so
runtime-mode rollback cannot select a different store. Production rejects a
different `RADON_SECRET_STORE_PATH` while `RADON_MODE=hetzner`, and the
container wrapper checks that invariant before removing the running container.
`data/secret_store/` and the repo-root operator recovery key are gitignored.

Failure modes (REL-217/REL-218, 2026-09-03): any store-constructor failure —
`SecretStoreError` or OSError-class (key-file path is a directory, permission
denied) — surfaces as HTTP 503 `CREDENTIAL_STORE_UNAVAILABLE`, never a raw
500. The setup wizard's `.env` materialization (`web/lib/setup/envFiles.ts`)
is quote-continuation aware: multiline quoted values already in the file are
preserved verbatim, and a value neither dotenv dialect can encode is dropped
from the env write and reported as an `env_refused` outcome while setup still
completes (the encrypted store keeps the value; REL-216).

**Master key.** Resolution order: systemd credential
`radon-secret-store-key` in `$CREDENTIALS_DIRECTORY`, then the key file at
`$RADON_SECRET_STORE_KEY_FILE` (default `~/.radon/secret_store.key`,
auto-generated 0600 on first use). Production
`radon-api.service` loads
`/etc/credstore.encrypted/radon-secret-store-key` with
`LoadCredentialEncrypted=`. The root container wrapper validates the decrypted
value is a regular, non-symlink 32-byte file, stages a copy under
`/run/radon-app-runtime/credentials/`, and mounts only that directory
read-only into the API container. The key is never passed through Docker
arguments or environment values; the staged plaintext is removed by
`ExecStopPost` after the container stops. The wrapper runs the container
with Podman (`--cgroups=split`, so it lives in the unit's own cgroup) when
`/usr/bin/podman` exists, and falls back to Docker otherwise or when
`RADON_CONTAINER_ENGINE=docker`; credential staging is identical on both
(REL-087).

**Container environment rendering (REL-158 / R-438).** Before an app
container starts, the wrapper renders a private temporary environment copy,
checks rendering and newsfeed allowlist-filter errors, and atomically publishes
the complete 0600 file. Failure exits 71 without starting the container or
overwriting its previous rendered copy. An empty successful newsfeed allowlist
is permitted; a failed filter is not.

The container notify proxy accepts only `READY=1`, `WATCHDOG=1` and `STATUS=`
notices (REL-158 / R-439). It drops lifecycle, PID and timeout-control fields,
including those mixed into an otherwise valid datagram. The proxy socket stays
0600 and is owned by the container user.

**Container stop (2026-09-25).** Each app drop-in runs
`ExecStop=radon-app-runtime halt %n <grace>`, which stops the container by
name through the engine (`stop --time <grace>`: SIGTERM to the container
init, SIGKILL after the grace) before systemd signals the foreground
`podman run` client. Stops previously relied on that client proxying
SIGTERM; it intermittently never arrived (app logged no SIGTERM, kept
working until the 90s SIGKILL), and the deploy helper's 60s inactive wait
rolled six deploys back. Graces: 30s for api, nextjs, monitor and newsfeed,
5s for relay (`TimeoutStopSec=10`), 110s for research (150s helper wait).
`cloud/tests/test_container_stop_delivery.py` pins grace + 5s inside both
the unit's `TimeoutStopSec` and the helper wait. A missing container is
success (ExecStop also runs after a crash); one that survives a failed stop
exits 75. `ExecStopPost` still reaps.

**Subscription credential binds (2026-09-18).** `radon-app-runtime` also
binds the operator's CLI subscription grants, each read-only and only when the
directory exists on the host: `/home/radon/.grok`, `/home/radon/.codex` and
`/home/radon/.claude` land at the same paths inside the containers whose unit
runs an LLM consumer (`radon-api`, `radon-newsfeed`, `radon-research`,
`radon-nextjs`), with `HOME=/home/radon` pinned so `Path.home()` and
`os.homedir()` resolve to them. Next.js hosts `/api/newsfeed/share` and
`/api/assistant`; excluding it (2026-09-19) 502'd every share rewrite with
`Missing Anthropic subscription`. Never the whole home directory, and never
into the relay. The Python and Next.js model ladders use the
[subscription-tier billing and recovery policy](oauth-subscription-auth.md#radon-http-model-ladder-server).
Prepaid fallback for those tiers requires the explicit `RADON_LADDER_ALLOW_PREPAID`
opt-in; funding a prepaid wallet alone does not recover a missing subscription.
NVIDIA and Cerebras have separate rung policies in that owner. Check subscription
availability and the credential binds before changing billing policy.
`radon-subscription-tokens`
keeps the files live on the host ([subscription-tokens.md](subscription-tokens.md)).

The staged copy is `root:radon-secrets 0040` in a `root:radon-secrets 0050`
directory, and the container is granted that gid at start with
`--group-add` (R-619). The API container runs `--user radon`, so a copy owned
by uid `radon` would be readable by anything else that account can start; the
owner bits are empty and only root can grant `radon-secrets`, so the delivery
channel is one the `radon` account cannot open for itself. `radon` is never a
member: `setup-vps.sh` creates the system group and refuses to continue if it
finds the account in it, and `radon-app-runtime` exits 78 before staging
anything if the group is missing or `radon` has joined it. Adding the
`radon-panic-index` service/timer pair to the setup-vps inventory does not
change this staging path, the `radon-secrets` group, or docker-group stripping.

`radon` is deliberately NOT in group `docker` (root-equivalent on this
host): `setup-vps.sh` never adds it and strips a membership left by an
older provision (`gpasswd -d radon docker`); Gateway compose calls go
through the root-owned `radon-docker-gw` shim instead. **Operator (live
hosts provisioned before this change):** run `sudo gpasswd -d radon docker`,
then verify with `id -nG radon` (no `docker` in the output).

**Privileged file-op hardening (2026-09-20).** `setup-vps.sh` stages
root-installed artifacts from committed git blobs (`git cat-file`) rather
than the working tree, and refuses to publish `mcp.env` through a
non-regular destination (writes to a temp file, then atomic rename). The
security loops' post-run hook (`scripts/runner/hooks/security_post.sh`)
refuses a symlink at the phase report path and at its private scratch before
reading them, and pins GitHub's known host key in a fresh `mktemp` file for
every push. Contracts:
`cloud/tests/test_setup_vps_privileged_paths.py`,
`scripts/tests/test_runner_security_hooks.py`.

**Provisioning provenance.** Follow the [privileged bootstrap owner](../cloud/CLAUDE.md#privileged-bootstrap)
for the trusted-release prerequisite, provenance refusal conditions, and the
boundary between provisioning and live control-plane refresh. Setup and the
root helper read installed bytes from root's own clone of the pinned remote,
`/opt/radon-provision/radon.git`, never the radon-owned checkout store.
Both refuse a privileged install from a commit older than the one recorded in
`/opt/radon-provision/control-plane-floor`.

**Newsfeed least privilege.** `radon-newsfeed.service` runs Chromium against
third-party web content. Chromium keeps its own sandbox: the container runs
under `--security-opt seccomp=/etc/radon/seccomp/chromium.json` (source
`cloud/config/seccomp/chromium.json`: the engine default from moby/profiles
plus `clone`/`unshare`/`setns`/`chroot` for the namespace sandbox, the
addition jessfraz's `chrome.json` makes), without host IPC, with a 512m
private `/dev/shm`. The runtime refuses to start the unit (exit 78) if the
profile is missing. If the host still refuses the sandbox at launch,
`scripts/newsfeed/browser.js` retries with `--no-sandbox` and logs
`chromium sandbox unavailable`; `PLAYWRIGHT_CHROMIUM_SANDBOX=0` in
`/etc/radon/env` forces that mode. Also,
`radon-app-runtime` hands it a filtered env file — only the keys the
newsfeed code reads (`NODE_ENV`, model-ladder keys including `ANTHROPIC_API_KEY` and `CEREBRAS_API_KEY` last,
Turso, media, `PLAYWRIGHT_CHROMIUM_SANDBOX`, replica toggles, and
`RADON_NEWSFEED_*`), never the full production secret set — and starts its
container on an isolated bridge network (egress only) instead of the host
stack every other unit uses. Adding an env var the newsfeed needs means
extending the allowlist in `render_env_file`; the contract tests in
`cloud/tests/test_app_runtime.py` pin both behaviors.

**App startup and rollback images.** The nextjs container starts through
`next-clerk-guard`, which requires the runtime Clerk publishable key to match
an entire key token in the baked client bundle before starting Next.js.

Pre-pull compares registry and local image digests before reusing cached
release tags. Cleanup preserves the target and durable rollback SHAs, takes
the existing deploy lock nonblockingly, and skips pruning when rollback
metadata or the running app population is unavailable.

There is no escrow, and `secrets.db` is
bound to its key by fingerprint (`key_binding` table): with rows present and
the key file missing, the store refuses to open rather than minting a new key
over them, and a replaced key refuses writes. Every `/credentials` route then
answers 503 `CREDENTIAL_STORE_UNAVAILABLE`. Recovery is to restore the
original key, or delete the configured `secrets.db` and re-enter every
credential. Back up the exact key together with `secrets.db`; any local
recovery copy must be mode `0600` and gitignored. Field inventory:
`scripts/credentials_registry.py`. Implementation: `scripts/secret_store.py`.

**Validation is throttled.** Saving (`PUT /credentials/{service}`) and the
dry-run check (`POST /credentials/{service}/validate`) both run the vendor
validator, which can hold a thread for up to `SLOW_LOGIN_TIMEOUT_S` (90s on
the browser-login services). The route bounds it: at most
`VALIDATOR_CONCURRENCY` (2) validators in flight per process, and one run per
service per `VALIDATOR_COOLDOWN_S` (5s). A request inside the window gets
`429` with `Retry-After` and code `VALIDATION_COOLDOWN`, and makes no vendor
call; on the PUT path nothing is stored. Constants and the chokepoint
(`_run_validator`) live in `scripts/api/routes/credentials.py`. Vast.ai returns
HTTP 404 with `error=auth_error` for a rejected bearer key; its validator
classifies that explicit provider response as invalid so Profile blocks the
save, while unrelated 404 and transport failures remain retryable errors.

**Values are validated at the persist chokepoint.** `PUT
/credentials/{service}` rejects values the process environment cannot hold
(NUL bytes, unencodable text) before anything is written, and both env-export
sites skip-and-log a bad stored row the same way the undecryptable-row path
does, so a legacy row can never abort the FastAPI lifespan bootstrap
(`scripts/api/routes/credentials.py`).

### First-run setup wizard (`/setup`)

Setup token, credential validation, registry/backend and completion failures appear in persistent dismissible toasts. Correct the values and retry with the wizard controls; dismissing a toast does not change validation or enable completion. Successful validation and stored credential outcomes remain in the wizard.

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

**IBKR operator hold.** The Gateway shares the operator's IBKR username, so it kicks any operator login. On the broker as root, `radon ib release` (installed by `setup-vps.sh install_ib_hold` as `/usr/local/sbin/radon-ib-hold`) holds the Gateway logged out until `radon ib resume`. Every start path refuses while it is held. Runbook: `docs/ib-gateway-recovery.md`.

**Hetzner control boundary.** `radon-ib-gateway.service`, the watchdog adapter, admin controls, boot, and operator commands all call the installed monorepo helper at `/usr/local/bin/radon-ib-gateway-control` (sourced from `/home/radon/radon/cloud`). FastAPI runs with `IB_GATEWAY_MODE=cloud` and must not inspect or mutate the production Compose project directly. Set `IB_GATEWAY_COMPOSE_DIR=/home/radon/radon/cloud`. Secrets are `/etc/radon/env` (`0640` root:radon); `/home/radon/radon-cloud/.env` is a compatibility symlink. Root demotion of the helper must run from a radon-readable cwd (never leave cwd as `/root`).

Caddy is not in group `radon` (that gid reads the env file). `media.radon.run` is served from `/var/lib/radon/media` via group `radon-media` and a parent traverse ACL. The recursive ACL refresh in `setup-vps.sh` and `publish-caddy` runs as `radon`, which owns the tree; if it reports a permission error, a file under media is not radon-owned (`chown -hR radon /var/lib/radon/media` as root).

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

Knowledge golden eval (`radon-knowledge-eval.timer`) is a nightly VPS oneshot
that is **not enabled**. `setup-vps.sh` skips it on every run. The checked-in
baseline is already a live snapshot (`placeholder: false`). Leave the timer
off while `scripts/knowledge/golden_set.json` is `draft: true`. Dated JSON
lands in `/var/lib/radon/knowledge-eval`. A hit@5 or MRR regression fails the
unit. Enable steps: [`cloud-services.md`](cloud-services.md#knowledge-golden-eval-radon-knowledge-evaltimer).
Contract: [`knowledge-embeddings.md`](knowledge-embeddings.md).

Hetzner host systemd is the production surface. Laptop dev uses launchd plists in `config/`. Laptop `com.radon.data-refresh` must stay unloaded. VPS `radon-flow-refresh.timer` owns hourly scanner/discover/flow during ET RTH.

**Nightly agent loops on the Mac mini.** All six nightly agent loops (reliability, testing, ci-performance, documentation, security, security-deepsec) run on the one-file runner: `scripts/runner/run_loop.sh <loop>` with `scripts/runner/loops/<loop>.env`, installed root-owned in `/usr/local/radon-runner` by `scripts/runner/install.sh` as the LaunchDaemon `com.radon.runner.<loop>`, which runs as the hidden unprivileged user `_radonbot` ([docs/runner.md](runner.md)). Each night the runner deletes and re-clones `main` into `/Users/_radonbot/radon-runner/work/<loop>`, runs the agent with the loop's prompt from `.claude/runner-prompts/<loop>.md`, and pages Pushover. The per-loop wrappers (`scripts/<loop>_nightly.sh`, `~/radon-weekend/<clone>`, `.weekend-runner.lock`, `.radon-<loop>-runner` markers, split gitdirs, per-loop venvs) are retired. The `Fires` column is each loop env's `SCHEDULE_HOUR:SCHEDULE_MINUTE`.

| Loop | Fires (local) | Clone | Runner / daemon | Issue label |
|---|---|---|---|---|
| reliability | 00:00 | `/Users/_radonbot/radon-runner/work/reliability` | `scripts/runner/run_loop.sh reliability` / `com.radon.runner.reliability` LaunchDaemon | `reliability-nightly` |
| testing | 00:10 | `/Users/_radonbot/radon-runner/work/testing` | `scripts/runner/run_loop.sh testing` / `com.radon.runner.testing` LaunchDaemon | `testing-nightly` |
| ci-performance | 00:20 | `/Users/_radonbot/radon-runner/work/ci-performance` | `scripts/runner/run_loop.sh ci-performance` / `com.radon.runner.ci-performance` LaunchDaemon | `ci-performance-nightly` |
| security | 00:40 | `/Users/_radonbot/radon-runner/work/security` | `scripts/runner/run_loop.sh security` / `com.radon.runner.security` LaunchDaemon | `security-nightly` |
| security-deepsec | 00:50 | `/Users/_radonbot/radon-runner/work/security-deepsec` | `scripts/runner/run_loop.sh security-deepsec` / `com.radon.runner.security-deepsec` LaunchDaemon | `security-deepsec` |
| documentation | 03:00 | `/Users/_radonbot/radon-runner/work/documentation` | `scripts/runner/run_loop.sh documentation` / `com.radon.runner.documentation` LaunchDaemon | `documentation-nightly` |

**The security loops on the runner.** `security` and `security-deepsec` use the runner's optional knobs (all in their root-owned loop env, docs/runner.md "Loop config"):

- **Three phases, one session each.** `PHASES="audit:<secs> remediate:21600 deliver:10800"` (audit 2h for security, 8h for DeepSec, which runs `deepsec process` / `revalidate` / `export` in-session). Every phase runs whatever the earlier one returned; each gets its own header (`Phase:`, `State:`), timeout and page. The runner exits 75 when any phase is not OK and 2 when one was refused.
- **Claude only, never fable.** `ALLOWED_AGENTS=claude` refuses any other rung (exit 2, one page, before cloning). `AGENTS_RESOLVER=lib/security_claude_ladder.py` lists the bot's `claude models`, skips the newest tier and prints `claude:<model>` rungs; empty or failed output keeps `AGENTS="claude:claude-opus-5 claude:claude-sonnet-5"` and is logged. Every claude launch passes `--model <rung> --effort medium`, `--disallowedTools ScheduleWakeup Monitor CronCreate` and `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0`, and exports `RADON_RUNNER_MODEL` so the prompt's nested Stage 4 `claude` uses the same model.
- **Subscription only.** `AGENT_UNSET` lists every API-key / auth-token / base-URL / Bedrock / Vertex / Foundry / gateway name the installed Claude Code honors (the per-name review is in [security-approved-tools.md](security-approved-tools.md)), plus `OPENAI_API_KEY`, `CODEX_API_KEY` and `PW_TEST_CONNECT_WS_ENDPOINT`. The runner unsets each and logs `IGNORING: <NAME>`, never the value. `AGENT_ENV` sets `RADON_LADDER_NO_AUTH_FILES=1` and `RADON_WEEKEND_BROWSER_HOST=unavailable:disabled`. The daemon's environment also carries `DISABLE_AUTOUPDATER=1` (rail 8).
- **Pre-run hook** (`scripts/runner/hooks/security_pre.sh`, root-owned): asserts the phase runs in `~/radon-runner/work/<loop>` with `origin` = the loop's repository; `REFUSED`s (exit 2, no agent) a credential file (`.env`, `.env.ib-mode`, `web/.env`) and what `unset` cannot reach: a key line in `.env.local`, `.env.*.local` or any `.deepsec/**/.env*`, or an `apiKeyHelper` / reroute `env` entry in the bot's `~/.claude/settings.json`, `.claude/settings*.json` or the managed settings. Flag names count only when truthy (1/true/yes). Then it runs `claude_cli_env_drift.py --notify` against the bot's claude (non-fatal; one page per CLI version with unreviewed env names), fetches, detaches at the newest CI-green `main` from `nightly_green_base.py` (REL-187), `git clean`s everything but `KEEP_PATHS` and `.venv`, keeps the bare `$LOOP_STATE/held.git`, writes `audit-context.md` for the audit phase and arms the branch-only deliver record before deliver (R-611). Every hook git call pins `core.hooksPath=/dev/null` and `core.fsmonitor=false`.
- **Post-run hook** (`scripts/runner/hooks/security_post.sh`): the phase is `OK` only with a column-0 `SECURITY-NIGHTLY PHASE COMPLETE:` / `SECURITY-DEEPSEC PHASE COMPLETE:` line in this phase's own log slice (R-535; a recital does not count); `TRUNCATED` on the background-ceiling marker, `TIMEOUT` on rc 124, `FAILED (exit N)` otherwise, `INCOMPLETE (exit 0 without the phase-completion marker)` without the stamp. Deliver additionally needs its verdict: the durable record `$LOOP_STATE/.<loop>-deliver/record.json` first (R-613), then the slice's `NIGHTLY DELIVER READY:` / `NIGHTLY DELIVER INCOMPLETE:` line, and every URL must be a same-repo OPEN PR on the loop's prefix (`gh pr view -R joemccann/radon`). A branch-only `launched` record falls through to the verdict line. It then publishes the private report and posts the dead-man.
- **Private operator reports.** At the end of every phase the agent writes `$RADON_RUNNER_LOOP_STATE/scratch/latest-report-<phase>.md`. The post-run hook (never the agent) takes it only if it is newer than the phase start and not a symlink, redacts known secret shapes, and pushes it to the PRIVATE repository `joemccann/radon-security-reports` at `reports/<loop>/<YYYY-MM-DD>/<phase>.md` from a fresh `mktemp` clone, over `ssh -i ~/.radon-runner-reports-key -o IdentitiesOnly=yes` with GitHub's published ed25519 host key pinned in a `mktemp` known-hosts file (`StrictHostKeyChecking=yes`). The key is the bot's, outside every clone, never exported and named only in that hook. The page links it as `url` / `url_title` "Open private report"; a page without it says `(no private report this phase)`. The public rolling issue never carries the link or the content (rail 7). Never make that repository public.
- **Dead-man reporting.** Each phase posts one sanitized runner-health comment (`**PHASE** STAMP **status**`, optional detail) on the rolling issue labelled `security-nightly` or `security-deepsec`, creating the issue if absent, and one Pushover. The detail is hook-generated, never agent text, and passes the same `/usr/bin/sed` sanitizer as the old wrapper (no route, `file:line`, URL, secret shape, email or account). Older comments are pruned with `nightly_issue_prune.py` only after the new comment's post is confirmed and its numeric id is kept (R-612, R-657). The agent never writes an issue: the root-owned `gh` guard (`GH_GUARD=1`, `/usr/local/radon-runner/guard/<loop>/gh`, loop name and real `gh` baked in) routes every `pr` / `api` / `issue` / `alias` call through `nightly_pr_guard.py`, which refuses merges, aliases, unguarded PR creation and, for these two loops, any issue write. A missing daily comment means the runner did not fire.
- **Killed mid-phase.** On SIGTERM (a bootout, launchd's `ExitTimeOut` of 60, a reboot) the runner kills the agent's process group, reaps leftovers, pages `KILLED` first, then gives the post-run hook 10 seconds for the dead-man, and exits 143.
- **Leftover processes.** After every agent the runner kills the process group, then every bot-owned process whose cwd is under the loop's clone or state, plus pids the agent listed in `$RADON_RUNNER_PIDFILE` that started during the phase. Other loops have their own clone and state, so they are never touched.
- **Night-to-night state.** `$RADON_RUNNER_LOOP_STATE` (`/Users/_radonbot/radon-runner/state/<loop>`, mode 700) is never deleted by the runner: `scratch/` (run-records, `last-audited.json`, reports, `rclone.conf` for the private archive), `.<loop>-deliver/`, `held.git` (unreleased P0/P1 branches, never pushed to origin), `keep/` and the drift-page stamps. The post-run hook prunes `scratch/` run dirs older than 30 days. Releasing a held P0/P1 is still an operator-written `released: <finding-id>` line in the private run-record (docs/runner.md). DeepSec `last-audited.json` also holds a durable `closed_queue`. Before any operator-only item is listed in a PR Next section, `scripts/nightly_deepsec_queue.py reverify` checks it against evidence (`open` / `closed` / `unverifiable`) and the committed ledger `docs/security-deepsec-closed.json` (sanitized IDs only). Closed items stay in private state and are not re-listed in Next; they appear once in that night's deliver report as closed tonight. Confirm `DS-2026-09-20-03` on the next fire: it is seeded `closed` (PR #689, `e5c4e627`, `cloud/scripts/setup-vps.sh` L39-47) and must show in closed tonight once, then only in `closed_queue`.
- **DeepSec workspace.** `KEEP_PATHS=".deepsec data/radon"` moves the operator-bootstrapped `.deepsec/` workspace and DeepSec's incremental `data/radon/` state aside before the re-clone and back after it, at the same clone path, so DeepSec's recorded `rootPath` stays valid. DeepSec itself stays operator-bootstrapped (rail 8): a missing or mis-pinned `.deepsec/` workspace is `OPERATOR_REQUIRED`, never an install trigger. Run from the clone root, DeepSec's reviewer is its vendored Codex SDK on the bot's own `codex` login. A `failed` DeepSec status is operator-only: first confirm the job with `sudo launchctl print system/com.radon.runner.security-deepsec`. Do not bootstrap or restart DeepSec from a nightly run.

**Cycle shape (security and DeepSec).** `audit` records verified findings; `remediate` implements EVERY verified source-actionable finding as root-cause commits on one dated branch `<loop>/<YYYY-MM-DD>`; `deliver` formats ONE substantive PR via `scripts/github_pr_output.py`, publishes through `scripts/nightly_publish.py`, polls CI with `scripts/nightly_deliver.py watch`, fixes red checks on the branch, and ends by printing the verdict line the post-run hook turns into the phase's final notification. Deliver runs even when remediate exited non-zero (committed fixes are durable; CI decides). The loop never merges: the operator merges from the Pushover / issue line. An INCOMPLETE deliver records branch + PR number outside the clone (`$RADON_RUNNER_LOOP_STATE/.<loop>-deliver/record.json`, via `RADON_WEEKEND_ROOT`; the security loops also mirror it in their private run-record) and the next fire resumes that branch and PR before opening a new one.

**Substantive publication gate.** Security, DeepSec and deterministic codemap refresh use `scripts/nightly_publish.py`; the runner prompts for testing and reliability run its `check` before opening a PR. `python3.13 scripts/nightly_publish.py check --base origin/main --head HEAD` returns JSON and exits **0** for substantive changes, **3** for no substantive changes, or **1** for an error. Classification alone does not certify phase completion. Classification compares the effective merged tree against the base, so empty, reverted, already-merged and bookkeeping-only changes do not qualify. The exclusions are `tasks/`, named root audit/status ledgers and timestamp-only codemap refreshes; product documentation, reports, financial data, source, tests and CI changes remain eligible. A real CI experiment may qualify while its measurement status is `VALIDATING` or `INSUFFICIENT_SAMPLE`.

For security, DeepSec and codemap, publication must use `python3.13 scripts/nightly_publish.py publish --base main --head <branch> --title <title> --body-file <path>`. The publisher refreshes the base, classifies the committed changes, owns the push and PR creation/reuse, and reports the actual PR URL and head SHA (exit 0); no-op exits 3 before push or PR creation, and errors exit 1. The security loops' `gh` guard classifies argv the way gh does (any non-bool flag consumes the next token, so a flag value is never read as a subcommand or `api` endpoint). That shim is accident prevention, not a security sandbox; the shared publisher is the publication gate, and the `main-review` ruleset blocks a direct merge by the bot's Write-role token. For security and DeepSec, resume an existing substantive PR before declaring nothing to ship. With none to resume, record `--branch "" --status green` through `scripts/nightly_deliver.py record`, omit PR identifiers and URLs, then print `NIGHTLY DELIVER READY: loop=<slug> prs=0`.

**No-op checkpoints survive disposable clones.** The runner loops (reliability, testing, ci-performance, documentation) read the latest complete rolling-issue checkpoint before any local ledger, because their clone is re-cloned every night. Each successful audit posts `audited-through: <verified-origin-main-sha>` and carries forward every still-open finding, evidence, acceptance criterion and blocked/operator action, including on a zero-delta audit; resolved findings require closure evidence. Incomplete audits do not advance the cursor. Security preserves its checkpoint and findings in its private state and archive and publishes only sanitized health. Issue pruning retains the newest valid audit checkpoint and latest operator report, so a no-PR night cannot erase unfinished work.

Documentation reads the newest #202 `audited-through:` checkpoint (48-hour fallback when none) and posts one rolling comment every night, as specified in [its prompt](../.claude/runner-prompts/documentation.md). Inspect its `RESULT:` line and exact-head PR checks using the [runner completion check](runner.md#9-smoke-test-bot-shell).

**Codemap refresh (02:00, not an agent loop).** `scripts/codemap_nightly.sh` / `config/com.radon.codemap-nightly.plist` runs in `~/radon-weekend/radon-codemap`: regenerates `tools/codemap/*` from `origin/main`, verifies the regenerated artifacts against the live import graph (`CODEMAP_NIGHTLY=1` unskips `TestCommittedArtifacts::test_matches_live_graph`; CI skips it because committed maps trail main between refreshes), then checks the staged tree with `nightly_publish.py check --base HEAD --index`. Timestamp-only refreshes exit without a commit or PR; substantive graph changes are committed and published through the shared publisher as a `codemap/<date>` PR, wait for required checks, then squash-merge. Feature branches never commit those artifacts. Install: `bash scripts/setup_codemap_nightly.sh`.

Stable fx path (2026-09-26). `com.radon.fx-stable-sync` (installed by `bash scripts/setup_fx_stable_sync.sh`) runs `scripts/fx_stable_sync.sh` from an installed copy at load, whenever `~/.local/bin` changes, and daily at 23:45. It copies `~/.local/bin/fx` to `~/.local/share/radon/fx-stable/fx` (APFS clone) only after `/usr/bin/codesign --verify --strict` passes against Vercel's designated requirement (`com.vercel.fx`, team `JW6Y669B67`). The launchd job does not set `RADON_FX_CODESIGN`; that variable exists so a test can supply a signature double without relaxing the requirement. No nightly loop reads that copy any more: the runner's agents run as `_radonbot` from its own `~/.local/bin`.

**ci-performance time-saving delivers.** When that loop (now on the runner, `.claude/runner-prompts/ci-performance.md`) fixes or delivers a CI-time issue, its PR body must include `| Job | Before | After | % change |` from `python3.13 scripts/nightly_issue_format.py ci-time-savings`. `% change = (after - before) / before * 100` (negative = faster). Times come from cited Actions runs; do not invent them. After still `VALIDATING` / `INSUFFICIENT_SAMPLE` prints pending and `TBD until` N samples.

| Service | Cadence | Purpose |
|---------|---------|---------|
| `radon-ib-gateway` | always-on | Broker session for live quotes, execution, reports |
| `radon-api` | always-on | FastAPI on `:8321` |
| `radon-relay` | always-on | IB realtime WebSocket relay on `:8765` |
| `radon-nextjs` | always-on | Next.js terminal at `app.radon.run` |
| `radon-newsfeed` | 120s loop | Headless Playwright scraper for The Market Ear |
| `radon-monitor` | 30s loop | Fills, exit orders, journal sync, cash flow handler |
| `radon-health` | always-on | **Isolated** stdlib health daemon on `:8330` (see Health monitoring below). NO dependency on `radon-ib-gateway`; a Gateway stop never touches it. |
| `radon-aa-frontier-refresh.timer` | Daily 07:00 UTC, up to 5 min jitter | Refreshes the Artificial Analysis frontier basket atomically; failures retain the last-known-good cohort. |
| `radon-ai-cycle-backfill.timer` | Daily 05:30 UTC, up to 5 min jitter | Resumes bounded publisher-floor history ingestion from its durable checkpoint; `ai-cycle-backfill` heartbeat has a 26h freshness budget. [Coverage limits](ai-infrastructure-operations.md#history-and-request-bounds). |
| `radon-ai-cycle.timer` | Daily 07:15 UTC, up to 5 min jitter | Versioned AI infrastructure observations. Partial provider access is explicit; `ai-cycle` heartbeat has a 26h freshness budget. [Collection and source configuration](ai-infrastructure-operations.md). |
| `radon-liquidcompute.timer` | Daily 07:30 UTC, up to 5 min jitter | Liquid Compute homepage GPU index ticker. Host-tagged `liquidcompute` rows, C5 on `/regime/llm`. Third venue versus the rental book; methodology opaque until licensed. Heartbeat `liquidcompute` (26h). Enable: `systemctl enable --now radon-liquidcompute.timer`. |
| `radon-slm-tagger-monitor.timer` | Daily 07:10 UTC, up to 5 min jitter | Newsfeed SLM tagger drift / invalid / label-shift monitor. No-op when `RADON_SLM_TAGGER_MODE` is `off` or `shadow`. Heartbeat `slm-tagger-monitor` (26h). Operator action on breach: `RADON_SLM_TAGGER_MODE=off`. Spec: [`ml/newsfeed-slm-tagger.md`](ml/newsfeed-slm-tagger.md). |
| `radon-refresh.timer` | 60s | Schedules data-refresh sweeps |
| `radon-vcg-refresh.timer` | Mon-Fri 13-21 UTC every 5 min | Autonomous VCG scan |
| `radon-portfolio-sync.timer` | Mon-Fri 04:00-19:59 ET every 60s | Autonomous portfolio sync. Window matches `fill_monitor`'s `session_window=equity_ext` (04:00-20:00 ET) so outsideRth fills reach the positions table instead of waiting for the next cash open; `run_portfolio_refresh.sh` re-checks `is_equity_ext_session_et()` and exits 0 on holidays and outside the session. |
| `radon-cta-sync.timer` | Mon-Fri 18:15 / 19:00 / 21:30 UTC | MenthorQ CTA refresh. Vision cascade: anthropic -> grok -> cursor -> codex -> gemini -> nvidia -> cerebras |
| `radon-bpi.timer` | Mon-Fri 21:30 / 23:30 UTC; Tue-Sat 11:00 UTC | BPI after the close, same-evening Yahoo catch-up, morning catch-up |
| `radon-ma-ratio.timer` | daily 22:45 UTC | SPX pct above 50d MA over pct above 200d MA (after the close; 5 min behind divyield). Spec: [`indicators/ma-ratio.md`](indicators/ma-ratio.md) |
| `radon-rsi-oversold.timer` | daily 23:05 UTC | SPX pct of members with Wilder RSI(14) strictly below 30 (after the close; 20 min behind ma-ratio so the shared member-close store is already fresh). Spec: [`indicators/rsi-oversold.md`](indicators/rsi-oversold.md) |
| `radon-calm-streak.timer` | daily 02:40 + 14:30 UTC | Consecutive SPX sessions without a >1% intraday band, from Cboe `_SPX.json` (conditional GET; unchanged runs are heartbeats). Spec: [`indicators/calm-streak.md`](indicators/calm-streak.md) |
| `radon-bounce-setup.timer` | Mon..Fri 21:10 UTC | BOUNCE SETUP scanner: stretch rank from Turso closes (largecaps), then UW fixed-strike put vol and 25-delta skew on the top 30. Writes `data/bounce_setup.json` + `scan_snapshots` service `bounce-setup`. Spec: [`bounce-setup.md`](bounce-setup.md). |
| `radon-iv-spread.timer` | daily 22:15 UTC | NDX minus SPX 1M ATM implied vol spread from IB (after the close; between ivrank and dispersion). Spec: [`indicators/iv-spread.md`](indicators/iv-spread.md) |
| `radon-watchdog-{intraday,continuous,daily,error}.timer` | varies | Service-health alerting (Pushover) |
| `radon-host-metrics.timer` | every 1 min | Host CPU, memory, loop lag. Details: [`cloud-services.md`](cloud-services.md#host-metrics-dur-12) |
| `radon-tv-alerts.timer` | every 5 min | TradingView alert digest (one Pushover per cycle), 180-day prune. Details: [`cloud-services.md`](cloud-services.md#tradingview-alerts) |
| `radon-equibles-{13f,ats,cot,filings,short-crowding}.timer` | daily / weekly | 13F, ATS, COT, filings, short crowding. Spec: [`equibles-api.md`](equibles-api.md) |
| `radon-vol-cone.timer` | Mon-Fri 20:45 UTC | Completed-session cheap-wing cone (16:45 ET, after the close grace). Spec: [`indicators/vol-cone.md`](indicators/vol-cone.md) |
| `radon-vol-cone-intraday.timer` | Mon-Fri 09:00-16:30 ET every 15 min | Live sample ranked against that stored cone, so the tab is tradeable during the session instead of a day stale. Holds without spending a UW request outside market hours or under a nearly-spent daily budget, and a held pass no longer republishes the shared `vol-cone` snapshot. The 16:45 ET slot is deliberately absent: in EDT it is 20:45 UTC, the EOD writer's own minute (R-128). |
| `radon-vixcor.timer` | daily 02:35 UTC | VIX x COR3M 20-session correlation, 15 min behind `radon-cor`. Spec: [`indicators/vixcor.md`](indicators/vixcor.md) |
| `radon-panic-index.timer` | daily 02:50 + 13:15 UTC | Panic Proxy (not Goldman's index): equal-weight mean of 252-session z-scores of Cboe VIX, VVIX, VIX/VIX3M, SKEW. First production run `--no-alert`. Spec: [`indicators/panic-index.md`](indicators/panic-index.md) |
| `radon-credit-spread.timer` | daily 21:45 UTC | HYG vs SPX credit-equity series. IB first, then Robinhood (when configured), then UW, then Yahoo. Spec: [`indicators/credit.md`](indicators/credit.md). |
| `radon-iei-hyg.timer` | daily 21:55 UTC | IEI/HYG duration-vs-credit ratio. Spec: [`indicators/iei-hyg.md`](indicators/iei-hyg.md) |
| `radon-credit-vix.timer` | daily 22:25 UTC | SHY minus HYG credit proxy vs VIX. Spec: [`indicators/credit-vix.md`](indicators/credit-vix.md) |
| `radon-leap.timer` | Mon-Fri 10:00 ET | LEAP IV-mispricing scan via FastAPI. Capacity-shed case: [`incident-runbook.md`](incident-runbook.md) |
| `radon-garch.timer` | Mon-Fri 14:00 / 17:00 / 20:00 UTC | GARCH convergence scan via FastAPI, 3x per RTH session. Capacity-shed case: [`incident-runbook.md`](incident-runbook.md) |
| `radon-incident-watchdog.timer` | every 5 min | Writes `data/incidents/`. Cases: [`incident-runbook.md`](incident-runbook.md) |
| `radon-grok-page-responder.timer` | 30s after last cycle | Headless Grok auto-fix from dedicated clone. Spec: [`grok-page-responder.md`](grok-page-responder.md) |
| `radon-grok-upgrade.timer` | daily 07:40 UTC, enabled | Track-latest Grok CLI/model. Smoke on scratch, auto-promote on pass, stay on last-known-good on fail. Spec: [`grok-page-responder.md`](grok-page-responder.md) |

The autonomous timers retired Radon's previous "data only refreshes when a browser tab is open" failure mode. Some surfaces remain on-demand by design (`scanner`, `discover`, `flow-analysis`, `analyst-ratings`, `gex-scan`, `orders-read-compare`).

**Operator CLI.** `/usr/local/bin/radon` wraps every loaded `radon-*` unit **except `radon-health`**. Auto-enumerates via `systemctl list-units 'radon-*'` (then filters out `radon-health.service`), so new timers don't require script edits. `radon-health` is deliberately excluded so the health daemon keeps reporting while `radon stop|restart` cycles the trading stack — manage it explicitly with `systemctl restart radon-health`.

```bash
radon stop      # stop IB + all radon-* units
radon start     # start them all (IB Gateway first)
radon restart
radon status
```

From the laptop: `ssh root@ib-gateway radon stop`. The operator CLI is installed from the monorepo [`cloud/scripts/operator-radon.sh`](../cloud/scripts/operator-radon.sh) control-plane source. `radon stop|start|restart` also never touches `radon-control.service` (below).

### Host control socket (`radon-control.service`)

The `/admin` Service controls modal runs on the app host, where `radon-api` is a container with every capability dropped, `no-new-privileges`, and no `systemctl` or `sudo`. `radon-control.service` ([`scripts/control_service/serve.py`](../scripts/control_service/serve.py), stdlib only) is its one path to unit control:

- **Transport.** A unix socket at `/run/radon-control/control.sock` (systemd `RuntimeDirectory`, dir `0700`, socket `0600`, owner `radon`, kept across restarts). No TCP port. `radon-app-runtime run radon-api.service` pre-creates that directory symlink-safe and bind-mounts it into the radon-api container only, with `RADON_CONTROL_SOCKET`. No other container mounts it, and it is a path socket, not an abstract one, so the host-network containers cannot reach it through the shared netns.
- **Peer and no token.** `SO_PEERCRED` must be the daemon's own uid (`radon`) or root. No shared secret: every principal that can open the socket is already uid `radon`, which holds the same `sudoers.d/radon-ops` grant, so a token would gate nothing new. No new group and no new sudoers line.
- **Allowlist.** One JSON line per request. Ops are `ping`, `status`, `unit` and `stack-restart`; unknown fields are refused. A unit must be in the live `systemctl list-units 'radon-*'` registry, match `radon-NAME.{service,timer}` and the API's `is_valid_unit`, and not be the Gateway, its adapters, a beta unit, or `radon-control` itself. Verbs are `start|stop|restart`.
- **Execution.** Exactly `sudo -n /usr/local/bin/radon unit <verb> <unit>` or `sudo -n /usr/local/bin/radon restart`, argv list, no shell. The operator CLI holds the deploy lock; a held lock comes back as exit 74 and the API answers 409.
- **Self-protection.** `radon-api` and `radon-nextjs` serve the panel: Stop is refused (`allowed_actions` is `["restart"]`, so the button is disarmed), and Restart, like Restart All, is answered first and runs about 1s later, detached, after a deploy-lock check. One detached action at a time. The operator CLI excludes `radon-control` from `radon stop|start|restart`, because the stack restart runs inside its cgroup.
- **Audit.** Each request writes a `radon-control audit {json}` journald line (actor from the API, peer uid, op, unit, verb, result, rc). FastAPI also logs `admin service action actor=... unit=... action=... ok=... rc=...`. Read with `journalctl -u radon-control -u radon-api | grep -E 'radon-control audit|admin service action'`.

`/admin/services` reports `status_source` once (`systemd`, `host-control`, `host-health`, `unavailable`) instead of repeating it per row. With the socket missing or the daemon down, rows fall back to `radon-health` and controls stay disarmed. Install path: `installed-units.sha256` pin, so `install-units` copies the unit on deploy; as a `.service` it is not enabled automatically (one-time `systemctl enable --now radon-control.service` on the app host; `setup-vps.sh` enables it on a fresh host).

## Network trust (Ops Plane step 2)

Every change here ships inert. Deploy never applies a tailnet policy, a host firewall or a Hetzner Cloud Firewall; each is an operator step below. Keep a public-IP SSH session open on the host you are changing until the new path is verified.

### Tailnet policy (`cloud/tailscale/policy.hujson`)

Replaces the allow-all grant. Tags `tag:radon-app` (ib-gateway), `tag:radon-broker`, `tag:radon-ops`, `tag:radon-gpu`, all owned by `group:operator`. Grants: operator devices reach each other (`autogroup:self`); `group:operator` reaches TCP 22 on every Radon tag and TCP 8321 on the app (cloud-thin `RADON_API_URL`); `tag:radon-app` reaches `tag:radon-gpu` TCP 8350 (SLM); `tag:radon-ops` reaches TCP 8341 on app and broker and TCP 443 on other ops nodes. Nothing reaches broker 4001 or 8340 over the tailnet: the order path is the private net. The file's `tests` block asserts `tag:radon-ops` cannot reach 8321, 4001, 8330, 8340 or 22; the admin console and the API refuse a save that fails it. `cloud/tests/test_tailnet_policy.py` pins the same properties in CI.

The repo is public, so `group:operator` holds the placeholder `__OPERATOR_LOGIN__`. Apply (operator, once, off RTH):

1. Save the live policy for rollback: admin console, Access controls, copy the editor contents to a local file (or `GET https://api.tailscale.com/api/v2/tailnet/-/acl` with an API access token, saved as `policy.before.hujson`).
2. Add the four `tagOwners` entries to the LIVE policy first and save, keeping its current grants. Then tag the servers: Machines, `ib-gateway`, Edit ACL tags, `tag:radon-app`; `radon-broker`, `tag:radon-broker`. The app host is logged out of Tailscale (2026-10-01): run `tailscale up --advertise-tags=tag:radon-app` on it and approve the login.
3. Render and validate: `sed 's/__OPERATOR_LOGIN__/<your tailnet login>/g' cloud/tailscale/policy.hujson > /tmp/radon-policy.hujson`, then `POST` it to `https://api.tailscale.com/api/v2/tailnet/-/acl/validate` (`Content-Type: application/hujson`, API access token): the response must be `{}`.
4. Apply: paste `/tmp/radon-policy.hujson` into the console editor and save, or `POST` it to `.../tailnet/-/acl` the same way.
5. Verify from the laptop: `ssh ib-gateway true`, `ssh radon-broker true`, `curl -fsS http://ib-gateway:8321/health`. From the broker: `nc -zvw3 <app tailnet ip> 22` must fail.
6. Remove stale devices (`asymmetric-mbp-joe`, `iphone-15-pro`, `claude-code-dev`); `autogroup:member` still covers any device left logged in.

Rollback: re-apply `policy.before.hujson` through step 4. Public SSH (ufw `OpenSSH` any) is the recovery path if a tag was missed.

### Tailnet trust narrowing (`scripts/api/auth.py`)

FastAPI's JWT bypass trusted all of `100.64.0.0/10`. It now trusts loopback plus `RADON_TRUSTED_TAILNET_PEERS` (IPv4 `/32`s in the tailnet range; wider or non-tailnet entries are ignored, so a typo only shrinks trust). The private-net `GET /health` probe scope (`10.0.0.0/16`, `is_private_net_probe`) is unchanged.

| `RADON_TAILNET_TRUST_MODE` | Unlisted tailnet peer | Empty list |
|---|---|---|
| unset / `log` (default) | trusted, logged `would refuse (log-only)` | whole tailnet trusted |
| `enforce` (or any other value) | refused, logged `refused` | loopback-only |

Logs are one line per peer per 10 minutes on logger `radon.auth`.

Rollout (app host `/etc/radon/env`):

1. Set `RADON_TRUSTED_TAILNET_PEERS=100.98.36.17/32` (operator laptop, cloud-thin `RADON_API_URL`). Nothing else calls `:8321` over the tailnet: the broker watchdog uses `10.0.0.2`, the Mac mini runners use SSH. `radon restart`.
2. Soak at least a week: `journalctl -u radon-api --since -7d | grep 'tailnet peer'`. Every line names a caller enforce would refuse; add it or account for it.
3. With no unexpected lines, set `RADON_TAILNET_TRUST_MODE=enforce`, `radon restart`. Verify from the laptop: `curl -fsS http://ib-gateway:8321/health` still carries `ib_gateway.auth_state`.

Rollback: unset `RADON_TAILNET_TRUST_MODE` (back to log-only) and `radon restart`.

### IBC trusted API clients (`cloud/ibc-overrides/trusted-ips.txt`)

`TrustedTwsApiClientIPs=127.0.0.1,10.0.0.2` (was `100.0.0.0/8`: every tailnet node plus public space). No laptop or Mac mini `/32`: local mode runs its own Docker Gateway on `127.0.0.1` (`scripts/ib mode local`), and cloud mode has no tailnet `4001` to reach since the split (broker binds `10.0.0.4` and loopback only).

The file is a reference. Nothing in deploy, setup or compose reads it (`cloud/tests/test_ibc_trusted_ips.py` pins that), so merging changes nothing live. It is also moot for the Docker Gateway: the gnzsnz image relays `4003 -> 127.0.0.1:4001` with socat inside the container, so every API client reaches the Gateway as `127.0.0.1`. Who can open `4001` is decided by the compose bind address and the host and Hetzner firewalls below, not by IBC. Operator step: none. If a Gateway is ever run without the socat relay, copy this value into its IBC `config.ini` and restart it (one 2FA).

### Host firewalls (`cloud/scripts/host-firewall.sh`)

Declared ufw rulesets, operator-run only. Deploy (`deploy.sh`, the root helper, `ci.yml`) never calls it; `setup-vps.sh` `open_firewall` applies the same app set at bootstrap (`cloud/tests/test_host_firewall.py` pins the parity and the no-deploy fact). Default is a dry run that prints the exact `ufw` commands; `--apply` (root) runs `ufw --force reset`, the rules, `ufw --force enable`.

Sources are env, IPv4 `/32` only (anything wider is refused): `RADON_FW_OPERATOR_SOURCES` (laptop `100.98.36.17`, Mac mini `100.87.184.89`, phone `100.113.204.20`, plus a public recovery address) and `RADON_FW_OPS_SOURCES` (future ops hosts; leave empty until they exist). Each ops `/32` gets `allow 8341/tcp` then `deny` everything else, ahead of every broad allow (ufw is first-match).

- **App:** 22/tcp any (CI deploys over SSH from GitHub-hosted runners, whose addresses are not fixed; narrowing 22 needs the deploy path moved first), 80/443 any, 41641/udp, 8321 from operator `/32`s and from `10.0.0.4` (broker watchdog). The blanket `allow in on tailscale0` and the `10.0.0.0/16` 8321 rule are gone.
- **Broker** (ufw inactive today): 22 from operator `/32`s only (refuses to run with none), 4001 and 8340 from `10.0.0.2`, 41641/udp, deny else.
- **Docker caveat:** ports Docker publishes (broker `10.0.0.4:4001`) are DNAT'd before ufw's INPUT chain, so the 4001 rule documents intent but does not filter. 4001 is protected by its bind address: `10.0.0.4` is reachable only from radon-private, whose only other member is the app.

Rollout, one host at a time, off RTH, with a second SSH session open:

1. `RADON_FW_OPERATOR_SOURCES='100.98.36.17 100.87.184.89 100.113.204.20 <recovery-ip>' bash cloud/scripts/host-firewall.sh --role broker` and read the output.
2. Same command with `--apply` as root on the broker. Verify: `ssh radon-broker true` from the laptop; from the app, `curl --cacert ... https://10.0.0.4:8340/healthz` (spof-host-split.md) and `/health` `auth_state=authenticated`.
3. Repeat with `--role app` on the app host. Verify `curl -fsS https://app.radon.run/health`, the broker watchdog's `GET http://10.0.0.2:8321/health`, and a CI deploy (`gh run list --workflow=ci.yml --limit 1`).

Rollback: `ufw disable` (broker: its prior state), or restore the backup `ufw --force reset` wrote under `/etc/ufw/*.rules.<timestamp>`.

## Health monitoring (isolated daemon + edge surface)

The health surface is **decoupled from the trading stack** so it keeps reporting precisely when the stack is down. Two layers plus an off-box witness:

- **`radon-health.service`** (`scripts/health_service/`, stdlib-only) — a standalone daemon on `127.0.0.1:8330` with **no `Requires=`/`After=radon-ib-gateway`**. (Since 44e89e1b no app unit is `PartOf=` the Gateway either: `radon-api`, `radon-relay`, `radon-monitor` carry `After=` ordering only, so a Gateway stop or 2FA restart leaves the app plane running. A unit that IS cleanly stopped does not `Restart=always` back; use `radon restart`.) `Restart=always` + `StartLimitIntervalSec=60`/`StartLimitBurst=5` so a crash-loop parks as `failed`, not an invisible hot-loop. Imports **nothing** from the trading stack (enforced by a subprocess isolation test).
  - `GET /healthz` — zero-I/O static `200` (liveness pin).
  - `GET /status` — **always `200`**; concurrent live probes (`radon-api` via `/health/lite`, relay/Next.js/IB-gateway TCP) + cached `systemctl` unit states (`active(exited)` reads `up`) + the Turso `service_health` table (read over stdlib libSQL HTTP — no libsql import; degrades to `unknown` on any failure). Degraded sources are body fields, never error codes.
- **Caddy edge** (`app.radon.run`): `GET /edge-health/ping` — static `respond "ok" 200`, the **never-502 floor** (depends only on Caddy). `GET /edge-health/status` → `reverse_proxy 127.0.0.1:8330`. **Caveat:** every failure mode of `/edge-health/status` is ALSO `200`: an upstream 5xx (`handle_response @down`) and a dial-refused daemon (`handle_errors`, the Caddy-synthesized 502) are both rewritten to `{"reachable":false,"observer":"caddy"}`, i.e. `200` with `reachable:false` and no `ok` field. A status-code-only uptime monitor therefore reads UP in every state except Caddy dead: pin the external monitor on the body (`ok` is a boolean and `overall_state` is `up`), never on the status code. The repo prober already does (`scripts/health_probe/probe.py` `_classify_status_payload` treats the synthetic body as `invalid`). `/edge-health/ping` is the guaranteed floor.
- **Off-box prober (Tier-3):** `.github/workflows/external-health-probe.yml` hits the public edge from off the VPS and UPSERTs to the Turso `external_probe` table (`scripts/health_probe/`), so a whole-box outage is still recorded externally. `scripts/health_probe/reader.py` flags a stale `external_probe` row after two hours (`STALE_AFTER_SECONDS`). The workflow cron is `1-56/5 * * * *` (every five minutes, off the top of the hour). GitHub's scheduler had stretched those runs to 2-5 hour gaps by 2026-09-26, past that window, so the Mac mini dispatches the same workflow every 300 seconds and the cron is the fallback while the mini is down. Install and verify are in [External probe dispatch](#external-probe-dispatch). Repo secrets the workflow reads: `TURSO_DB_URL`, `TURSO_AUTH_TOKEN`, and `RADON_PROBE_FRESHNESS_TOKEN` (no freshness token fails the job while the market is open).

**Consumers:** the always-on IB status chip (`web/lib/IBStatusContext.tsx`) reads `/edge-health/status` in prod (falls back to `/api/admin/health` in dev / as a prod safety net). The admin panel stays on `/api/admin/health` (needs `managed_accounts`). The `/health` payload itself is **trust-scoped**: public/proxied callers get `{"status":"ok"}` only; account/state detail goes to trusted peers only (loopback, tailnet `100.64.0.0/10`, Hetzner private net `10.0.0.0/16`; never a request carrying reverse-proxy forwarding headers). Any watchdog or off-box probe that needs the full payload must originate from one of those peers, not via Caddy. See `scripts/api/CLAUDE.md` and `scripts/health_service/CLAUDE.md`.

**Recovery heartbeat:** the `awaiting_2fa → authenticated` pool reconnect (`pool.reconnect_all`) is driven server-side by a FastAPI lifespan task (`_ib_recovery_heartbeat_loop`, 15s) — independent of any browser poll, since the chip is now a read-only consumer. The every-minute `radon-ib-watchdog` `/health` curl is the slower backstop.

### External probe dispatch

The Mac mini launchd agent asks GitHub to run the Tier-3 probe when the hosted cron slips. It does not probe the edge itself and it does not restart any trading service.

**Install** on the mini, from any checkout of this repo:

```bash
bash scripts/setup_external_probe_dispatch.sh
```

`scripts/setup_external_probe_dispatch.sh` copies `config/com.radon.external-probe-dispatch.plist` to `~/Library/LaunchAgents/com.radon.external-probe-dispatch.plist`, substitutes `__HOME__` with `$HOME`, lints the plist, then `launchctl bootout`, `bootstrap`, and `enable` for `gui/$(id -u)/com.radon.external-probe-dispatch`. `StartInterval` is 300 seconds and the job runs at load. Each fire is `gh workflow run external-health-probe.yml -R joemccann/radon --ref main`. The probe job runs only when `github.ref` is `refs/heads/main`, so a dispatch on another ref is skipped and never receives the Turso or freshness secrets. Those secrets live in the `health-probe` GitHub Environment, whose deployment branch policy is `main` only, not in repository secrets; the research cut reads a read-only Turso token from the `research-cut` Environment. A branch cut from an older, unguarded commit therefore gets no production database credential. The workflow concurrency group `external-health-probe` queues at most one run and does not cancel an in-flight probe, so a mini dispatch cannot cut a cron run short.

**Prerequisite.** `gh auth status` must succeed on the mini. The script still loads the agent when it does not, prints a warning, and the next interval retries. A dispatch with no `gh` login writes the stderr log and does not change Turso.

**Logs.**

- `$HOME/radon-weekend/logs/external-probe-dispatch.log`
- `$HOME/radon-weekend/logs/external-probe-dispatch.err`

**Verify** (read-only). `launchctl print "gui/$(id -u)/com.radon.external-probe-dispatch"` shows the label and the 300-second interval. The stdout log should show a `gh` dispatch rather than a traceback. A green workflow run is not proof a row landed: `scripts/health_probe/reader.py` still flags a stale `external_probe` row after two hours whichever trigger wrote it.

**Stop.** `launchctl bootout "gui/$(id -u)/com.radon.external-probe-dispatch"` removes the mini dispatch. Leave `.github/workflows/external-health-probe.yml` in place: its cron is the fallback while the mini is down.

**IVRank snapshot heartbeat (2026-09-26).** `scripts/fetch_ivrank.py` writes a heartbeat to the `service_health` table on every cycle. Previously, a Turso read timeout on the snapshot write (`upsert_scan_snapshot`) would skip the heartbeat entirely, leaving the previous day's `ok` row in place while the snapshot JSON advanced — the admin panel read "overdue" with no error to explain it. Now both the row upsert and the snapshot write are bounded independently and their failures are **folded into the heartbeat** instead of silencing it. When either write fails, the cycle records an `error` heartbeat with:
- `class: "db_write_failed"`
- `message` containing the underlying exception text (e.g., `"ivrank snapshot write failed: <error>"` or `"ivrank row upsert failed: <error>"`)

An on-call engineer seeing an `ivrank` row with `status: "error"` and `class: "db_write_failed"` now knows the root cause immediately — the write path is blocked, not the data source. The JSON fallback at `data/ivrank.json` still advances on every run, so the API surface remains available even when the DB write is degraded.

## Service Health & Watchdogs

Every dual-write service writes a row to the `service_health` Turso table on every cycle, including no-op short-circuits. The Next.js `<ServiceHealthBanner />` reads the latest row per service and renders a category-aware banner.

| Category | Stale state |
|----------|-------------|
| `scheduled` | Red — banner alerts; treated as outage |
| `on-demand` | Amber — dormant chip; suppressed from alerts |

Staleness windows live in `web/lib/serviceHealthWindows.ts`. Cycle-driven writers (`newsfeed-scraper`, `journal-sync`, `cri-scan`) use tight windows (~cadence × 3). Event-driven writers (`replica-watchdog`, `watchdog-alerts`) use 24h windows because "no event" is the healthy state. Equibles writers are registered there: daily 26h (`equibles-short-crowding`, `equibles-filing-forensics`), weekly 8d (`equibles-13f`, `equibles-ats-venue-share`, `equibles-cot-positioning`). An `ok` row with null `last_error` that still renders stale is a registration gap, not a dead writer.

**A row that NEVER appears is worse than a stale one.** A registered service whose row was never written renders as an outage the banner cannot explain and the watchdog cannot attribute. All five Equibles producers hit exactly that: `EquiblesClient()` raises `EquiblesAuthError` at construction when `EQUIBLES_API_KEY` is unset or rejected, and the construction sat OUTSIDE the block that owns health reporting, so each oneshot died before any `_record_health` call. No `service_health` row was written at all — not even an error row — and with the key then missing from `cloud/config/required-env.txt` the deploy preflight passed happily. Every producer must construct its client, resolve its ticker universe, and take its `parser.error(...)` exits INSIDE the health-reporting block, so an auth failure, an exhausted allowance, or an empty watchlist all leave an `error` row before the process exits. `EQUIBLES_API_KEY` is in `cloud/config/required-env.txt` as of PR #104, so the deploy preflight gates on it and the per-producer `error` heartbeat is the second layer that catches a key the API rejects at run time. `scripts/tests/test_service_registration_completeness.py` enforces the construction rule statically. The units and operator checks are in [`cloud-services.md`](cloud-services.md) §Equibles.

**A partial cycle is not a healthy cycle (R-294 / R-295).** Both `fetch_equibles_ats_venue_share` and `fetch_equibles_short_crowding` used a write gate that passes on ONE ticker: `payload_has_data` / `is_payload_valid` only test that the row list is non-empty. A run that served 3 of 40 tickers and then hit the daily allowance therefore wrote `ok` AND replaced the complete snapshot underneath it, and looked identical to a full cycle in `service_health` and on the panel. Both now apply a coverage gate — below `MIN_COVERAGE_RATIO` (60%) of the resolved universe the cycle records `error`, keeps the previous snapshot, and persists `requested` / `covered` / `failed` in the health row (including on the abort path, since a failure at ticker 3 and one at ticker 39 are different operational facts). The ratio is scoped to universes of `MIN_UNIVERSE_FOR_RATIO` (5) or more: one failure out of two is 50%, and a two-ticker run is a deliberate `--tickers` override rather than the scheduled sweep.

**A wall-clock budget abort is a third class, and it is not an allowance failure.** `fetch_equibles_ats_venue_share` and `fetch_equibles_filing_forensics` each bound the whole sweep with `SWEEP_BUDGET_S` and each call with `TICKER_FETCH_BUDGET_S`, so a tarpitted Equibles endpoint no longer runs into the unit's `TimeoutStartSec`. A per-ticker timeout replaces the shared Session and continues the walk; only a spent sweep budget stops. ATS scores coverage against portfolio ∪ watchlist, not the rotating Nasdaq-100 / Russell 2000 / S&P 500 tail, so an unreached index name is not a thin cycle. An empty-cycle `message` includes `codes` so a timeout is not read as a missing series. Raising `TimeoutStartSec` is the wrong move ([`incident-runbook.md`](incident-runbook.md) `equibles-ats-sweep-timeout`). The budget values live in the two scripts and are pinned against their units by `test_sweep_budget_fits_inside_unit_start_timeout`.

**A suppression window needs a cause (R-615 / R-616).** `fetch_equibles_ats_venue_share` stamped `next_attempt_at` with the next weekly fire on EVERY error branch, and the watchdog suppresses re-pages until that deadline — so a revoked key or a wedged client bought one page and then seven days of silence. The embargo is now written only for a cadence-bound failure (`EquiblesRateLimitError`); every other failure keeps paging until an operator acts. The same handler also normalises a naive `now`, so the error path can no longer raise a `TypeError` and exit with no `service_health` row at all.

**A rate limit is cycle-fatal, not a ticker gap.** `EquiblesRateLimitError` and `EquiblesAuthError` both subclass `EquiblesAPIError`, which is what the per-ticker handlers caught. Once the allowance is exhausted every remaining ticker fails for the same reason, so the loop recorded 37 individual "errors" for one condition and still finished. Both now propagate out of the per-ticker handler; a rejected key is tried once, not once per ticker. A genuine per-ticker condition (a 404 on a delisted name) still isolates.

**Ticker scope.** Both producers refresh only the Turso `watchlist` table, while `/api/equibles-smart-money-13f` and `/api/equibles-filing-forensics` accept any ticker. An off-watchlist ticker has no row, and both routes serve `missing: true` — the same shape a watchlist ticker gets when no institution has filed.

**Incident artifacts.** `scripts/incident_watchdog` writes `data/incidents/incident-*.json`. Laptop `com.radon.incident-responder` (`scripts/incident_responder.py`, 10 min) mirrors to `data/incidents_remote/` and analyzes open files older than 12 min. Cases: [`incident-runbook.md`](incident-runbook.md). Triage: `/incident <path>`.

**Grok P1 responder (VPS clone).** A delivered watchdog P1 also inserts `watchdog_pages`. `radon-grok-page-responder.timer` runs headless Grok from `/home/radon/radon-page-responder` and, unless the runbook says stand down, TDD-ships. After the live deploy gate, `scripts/deploy_notify.py` sends `radon deploy live` (priority 0). Spec: [`grok-page-responder.md`](grok-page-responder.md).

**Probe bearer.** `/api/service-health` is Clerk-protected. The loopback nextjs-db-watchdog sends `Authorization: Bearer $RADON_PROBE_FRESHNESS_TOKEN`. HTTP 401/403 is unknown (auth perimeter), never a Turso wedge. Do not add the route to `isPublicRoute`.

**Watchdog** (`scripts/watchdog/`) runs in four buckets (`intraday`, `continuous`, `daily`, `error`), each with its own timer. Alerts route to Pushover (P1 only) with per-service cooldown and hysteresis, plus an always-on `watchdog-alerts` row in `service_health` so the dashboard banner reflects fires even without an external channel. A delivered P1 also inserts `watchdog_pages`; laptop `com.radon.grok-page-responder` (30s) runs headless Grok to diagnose and, unless the runbook says stand down, ship a fix. After a release passes the live deploy gate, `scripts/deploy_notify.py` sends a normal-priority Pushover (`radon deploy live`, never P1). Ack with `python -m scripts.watchdog ack <service>`. The `error` bucket explicitly skips `watchdog-alerts` itself to avoid recursive alerting. (Discord support was removed 2026-05-19.)

**Banner humanization.** `service_health.last_error` JSON payloads are rewritten into operator-friendly copy before render (`humanizeServiceHealthError` in `web/lib/serviceHealthError.ts`).

**Database access pattern (post-2026-05-20):** every Radon process now goes direct-to-cloud — the code default since DUR-07 (replica opt-in only via `RADON_DB_USE_REPLICA=1`), with the `RADON_DB_NO_REPLICA=1` kill switch applied fleet-wide through the `radon-.service.d/common.conf` prefix drop-in. The libsql embedded-replica architecture (`data/replica.db`) was retired after multi-writer WAL contention and then single-writer frame conflicts between the replica owner and direct-cloud writers. Reads cost +30–60 ms per cloud round-trip, absorbed by SWR caching. The `replica_watchdog` handler still exists in `monitor_daemon` as a vestigial safety net (it sits idle in the no-replica world), but `data/replica.db` itself should not exist on any host. See `feedback_libsql_replica_one_writer.md`.

**Market-hours gate.** Handlers tagged `requires_market_hours=True` (`fill_monitor`, `exit_orders`, `journal_sync`) only run during their session window: 09:30-16:00 ET by default, or 04:00-20:00 ET for `fill_monitor` (`session_window = "equity_ext"`, so outsideRth stock fills reach `/orders` after the cash close; the FastAPI orders-sync tick uses the same window). The daemon converts UTC to ET via `zoneinfo.ZoneInfo("America/New_York")` so DST is handled automatically; a fail-open UTC-5 fallback fires only if the host is missing `tzdata`. Never hardcode a fixed offset for ET — it silently shifts the window 1h every DST season.

## Cash Flows

`scripts/cash_flow_sync.py` parses `CashTransaction` rows from an IBKR Flex Activity statement and upserts into the `cash_flows` Turso table. Surfaces on `/orders` via `web/components/CashFlowsSection.tsx`.

**Cadence:** the sFTP-delivered Activity statement: `radon-flex-pull.timer` (Tue..Sat 07:30 ET) -> `scripts/flex_delivery_ingest.py` -> `cash_flow_sync --from-file`. That ingest is the only path that writes `cash_flows` and it owns the `cash-flow-sync` service-health row (`ok` after a successful run or an already-applied duplicate statement, `error` with the exit code when the run fails). The row reports the batch's worst outcome: once any file in a run heartbeats `error`, later `ok` heartbeats in the same process are suppressed (REL-210, `scripts/flex_delivery_ingest.py`), because the sftp listing is unsorted and a stale duplicate routinely sorts after a failing new statement. The monitor daemon's `CashFlowSyncHandler` is not registered (2026-09-02); a weekday SendRequest is off by policy. Query ids and the pull unit: [`cloud-services.md`](cloud-services.md) "Flex sFTP pull".

**Throttle backoff.** Only Flex code 1018 is a rate limit; the breaker ladder is 90s -> 5m -> 15m -> 1h. 1001/1009 take the soft lane; 1019 on a poll is not an error. Detail: `scripts/monitor_daemon/CLAUDE.md`.

## Legacy Flex aggregate gross coverage

For operator-only rebuilding from saved execution-level statements, follow the
[Flex recovery procedure](cloud-services.md#legacy-flex-aggregate-cleanup),
including backup, review, stop conditions and post-commit recovery.

## Deployment

`git push origin main` triggers `.github/workflows/ci.yml`. Superseded test jobs
may cancel independently, but the production deploy job uses a non-canceling
`deploy-production` concurrency group. It SSHes to Hetzner as `radon`, extracts
`cloud/` from the tested `${{ github.sha }}` into an immutable support runner at
`/home/radon/.radon-deploy-runners/<sha>.<run>/cloud`, and invokes that runner's
deploy script. The monorepo [`cloud/`](../cloud/) directory is the canonical
source for deploy code, systemd units, Caddy, and the IB Gateway Compose
project; host secrets are `/etc/radon/env` (`0640` root:radon), with
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

## Private Dropbox research runtime

`radon-research.service` is optional and starts only after explicit activation. Its container receives a private read/write bind from `/var/lib/radon-private/research` to `/var/lib/radon/research`; the API receives the same bind read-only. The host anchor is root-owned `0700`, its research child is radon-owned `0700`, and provisioning rejects symlinked or writable ancestors. Seed approved batches through the container bind; host user radon cannot traverse the root-only anchor. Research files never enter the public media bind or demo mirror.

The API credential staging/cleanup contract remains in force when research mount provisioning fails: decrypted credential files must be removed on failure as well as normal stop. The research worker receives no API master-key mount, public media mount, or IB lease mount. Dropbox offline credentials and the model provider key remain in restricted runtime configuration. Apply migration 71 and verify private media before publication, then enable the worker. Full import and activation order: [Dropbox research](dropbox-research.md).

## Production Build Constraint

Next.js 16 prerender crashes on `/_global-error` and `/_not-found` because the root ClerkProvider context isn't materialised in isolated workers. `web/package.json` build pins `next build --experimental-build-mode=compile`. The error and not-found shells (`app/error.tsx`, `app/[ticker]/not-found.tsx`, `app/global-error.tsx`) use plain `<a>` and pure JSX (no `next/link`, `useEffect`, or `globals.css`) for the same reason.

### User-facing request errors

Page and setup failures use the shared safe error presentation boundary (`web/lib/userError.ts`). UI messages explain the failure and recovery without exposing JSON envelopes, HTML, backend paths or stack traces. Scanner retries preserve prior results and requested tickers; unconfirmed order requests instruct the operator to check order status first. HTTP statuses and server diagnostic bodies remain unchanged. The route-family review is recorded in [the page error audit](audits/page-error-audit-20260917.md).
