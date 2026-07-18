> **Monorepo:** `cloud/` in [`joemccann/radon`](https://github.com/joemccann/radon) is the source of truth for production infrastructure.
> The standalone `radon-cloud` checkout is legacy compatibility only; `/home/radon/radon-cloud/.env` is its sole external-secrets exception.
> Lifecycle, rollback, and bootstrap contract: [`cloud/CLAUDE.md`](CLAUDE.md) and [`docs/monorepo-cloud-migration.md`](../docs/monorepo-cloud-migration.md).

# Radon Cloud

Deployment infrastructure for the [Radon](https://github.com/joemccann/radon) trading terminal backend on a Hetzner VPS.

---

## What This Repo Contains

This directory holds production configuration alongside the Radon application in one monorepo. Application code and infrastructure are tested and deployed at the same Git SHA.

```
cloud/
├── docker-compose.yml          # IB Gateway container
├── services/                   # systemd unit files
│   ├── radon-ib-gateway.service
│   ├── radon-nextjs.service
│   ├── radon-api.service
│   ├── radon-relay.service
│   ├── radon-monitor.service
│   ├── radon-health.service    # Isolated health daemon :8330 (no cascade dep)
│   ├── radon-refresh.service
│   └── radon-refresh.timer
├── caddy/
│   └── Caddyfile               # Reverse proxy + auto-TLS (incl. /edge-health/*)
├── scripts/
│   ├── setup-vps.sh            # One-time VPS bootstrap (run as root, two-pass)
│   ├── post-setup.sh           # Local: env, build, services, data, verify
│   ├── deploy.sh               # Pull + build + restart (called by CI)
│   ├── migrate-data.sh         # Snapshot + transfer runtime state
│   └── wipe-vps.sh             # Reset VPS for clean rebuild
├── tests/                      # Configuration validation test suite
├── .env.example                # All required environment variables
├── PLAN.md                     # Codex-reviewed implementation plan
└── README.md
```

---

## Architecture

```
Browser (any device)
  → Clerk OAuth (Google, GitHub, X)
  → HTTPS/WSS

Hetzner VPS
  ├── Caddy (auto-TLS, reverse proxy)
  │     ├── /          → Next.js (localhost:3000)
  │     ├── /api/ib/*  → FastAPI (localhost:8321)
  │     └── /ws        → Node relay (localhost:8765)
  ├── Next.js — full app including API routes
  ├── FastAPI — IB-backed endpoints, order management
  ├── Node.js relay — realtime price streaming via WebSocket
  ├── Monitor daemon — fill tracking, exit orders
  ├── Data refresh timer — CRI/VCG scans (market hours)
  └── IB Gateway (Docker, host 4001 live / 4002 paper)

VPS services reach IB Gateway over loopback (IB_GATEWAY_HOST=127.0.0.1).
Live mode maps host `4001` to the image's container `4003`; paper mode maps
host `4002` to container `4004`. The laptop reaches the live API over Tailscale
at `100.112.32.16:4001`. Neither host port is published on the public NIC.
Tailscale also carries SSH.
```

---

## Stack

| Concern | Choice |
|---------|--------|
| **Auth** | [Clerk](https://clerk.com) — OAuth (Google, GitHub, X), JWT validation |
| **Ingress** | [Caddy](https://caddyserver.com) — auto-TLS, reverse proxy, WebSocket |
| **Deploy** | GitHub Actions + SSH — push-to-deploy in ~30-60s |
| **Frontend** | Next.js on VPS (Phase 1). Vercel migration in Phase 2. |
| **VPS** | Hetzner CPX11, Ubuntu 24.04, Ashburn VA |

---

## Prerequisites

- Hetzner Cloud account + VPS provisioned
- Domain with DNS A record pointing to VPS IP
- Tailscale for SSH management and laptop access to IB Gateway (4001)
- Clerk account (free tier)
- GitHub repo access for the Radon monorepo

---

## System Requirements

The `setup-vps.sh` script automatically installs all dependencies from their official sources:

- **Docker CE** from `download.docker.com` (not the Ubuntu `docker.io` package)
- **Python 3.13** from the [deadsnakes PPA](https://launchpad.net/~deadsnakes/+archive/ubuntu/ppa)
- **Node.js 22** from [NodeSource](https://deb.nodesource.com/)
- **Caddy** from the [official Caddy repo](https://caddyserver.com/docs/install#debian-ubuntu-raspbian)

The one-time provisioning script must be run as root from the monorepo: `ssh root@<VPS_IP> 'bash -s' < cloud/scripts/setup-vps.sh`

---

## Quick Start

### 1. Bootstrap the VPS (one-time)

```bash
# First run — installs everything, generates SSH key, exits
ssh root@ib-gateway 'bash -s' < cloud/scripts/setup-vps.sh

# Add the printed SSH key to GitHub (Settings → SSH keys)

# Second run — clones repos, builds, configures services
ssh root@ib-gateway 'bash -s' < scripts/setup-vps.sh
```

### 2. Complete setup (from your Mac)

```bash
cloud/scripts/post-setup.sh
```

This script handles everything after bootstrap:
1. Writes `.env` to VPS (from local `.env.production`)
2. Writes a mode-`0600` `web/.env` containing only `NEXT_PUBLIC_*` values and rebuilds Next.js with server values injected into the build process
3. Requests one lock-coordinated IB Gateway start and waits up to 90 seconds for 2FA
4. Starts all remaining services
5. Migrates `data/*.json` from local to VPS
6. Verifies health (API, Next.js, Caddy HTTPS)

> **Tip:** Store your production secrets in `.env.production` (gitignored) so `post-setup.sh` can find them automatically.

### 3. Deploy application code

Pushes to the Radon monorepo `main` branch trigger automatic deploys via GitHub Actions. The deploy script:

1. Acquires the nonblocking production lock in an outer process-group supervisor with bounded TERM/KILL recovery
2. Requires the tested SHA to equal the fetched `origin/main` tip; tracked host drift must already byte-match that target, while untracked runtime data is untouched
3. Validates the shared environment contract, then builds both frozen Bun workspaces and all Python wheels in a detached target-SHA worktree before any service teardown
4. Fsyncs a transition journal and backs up the exact live `node_modules`, `.next`, public web environment, and `.venv` artifacts
5. Uses the root-owned fixed-argument helper to snapshot and quiesce every discovered non-beta Radon service and timer except Gateway-owned units, stopping timers first and never replaying oneshot services
6. Promotes the staged artifacts, creates the target `.venv` offline at its final path, and restores only the previously active persistent services and timers
7. Gates the restored topology, FastAPI `/health/lite`, Next.js HTTP, and relay TCP/HTTP; IB state remains advisory
8. Fsyncs the `verified` phase before writing the green marker, commits the topology transition, and only then removes the journal and rollback artifacts

Superseded test jobs may be canceled, but a deploy that has entered SSH is
serialized and never canceled. If an interrupted run already moved Git HEAD,
the durable journal makes the successor either finish a verified target or
restore the exact prior artifacts and active topology before new work begins.
`setup-vps.sh` and `post-setup.sh` refuse live dependency or build mutation while
the affected production services are active.

The GitHub deploy job has a 60-minute ceiling and its SSH command has 55 minutes.
The tested worst case includes pending-journal recovery, the 900-second inner
deadline plus 30-second kill window, and a second root recovery. Root mutation
actions are capped at 180 seconds, verify/commit actions at 30 seconds, and one
190-second lifecycle-lock wait is budgeted per recovery; the resulting 2,150
seconds leaves more than ten minutes of SSH headroom for file and gate overhead.

### Control-plane changes

Root-owned helpers, sudoers, polkit rules, and systemd units covered by the
control-plane manifest are installed or updated only through the root bootstrap
transaction from the exact target monorepo checkout:

```bash
cd /home/radon/radon
sudo bash cloud/scripts/bootstrap-control-plane.sh
```

It serializes with deploy and Gateway transitions, validates and atomically
installs its managed artifacts, reloads systemd once, verifies the manifest, and
publishes readiness. The next release performs the installed-control-plane
manifest preflight before it may transition services. Do not use
`setup-vps.sh` as a live upgrade shortcut or install managed files directly.
See [the cloud operating contract](CLAUDE.md) and [the monorepo lifecycle
runbook](../docs/monorepo-cloud-migration.md) for the canonical procedure.

### 4. Verify

```bash
curl https://your-domain.com/health
```

---

## Services

| Service | Port | systemd Unit | Description |
|---------|------|-------------|-------------|
| IB Gateway | 4001 live / 4002 paper | `radon-ib-gateway` | Interactive Brokers API (Docker) |
| FastAPI | 8321 | `radon-api` | REST API, order management, scans |
| Node relay | 8765 | `radon-relay` | Realtime price streaming (WebSocket) |
| Next.js | 3000 | `radon-nextjs` | Web terminal UI |
| Monitor | — | `radon-monitor` | Fill tracking, exit orders |
| Data refresh | — | `radon-refresh.timer` | CRI/VCG scans during market hours |

### Managing services

```bash
ssh radon@ib-gateway

# Status
sudo systemctl status radon-api
sudo systemctl status radon-relay

# Restart
sudo systemctl restart radon-api

# Logs
journalctl -u radon-api -f
journalctl -u radon-relay -f
```

#### Whole-stack control: `radon` wrapper

A `radon` wrapper at `/usr/local/bin/radon` controls the persistent production daemons and active/enabled timers. Timer-owned oneshot jobs remain scheduler-controlled and are never launched directly. Gateway actions are delegated to the state-aware helper, which inspects actual container liveness and acquires the shared 2FA lease before any cycle:

```bash
radon stop      # stop Gateway, persistent daemons, and currently active timers
radon start     # restore persistent daemons and the pre-stop/enabled timer set
radon restart   # restart Gateway, persistent daemons, and active/enabled timers
radon status    # show systemd inventory plus real Gateway container state
```

From the laptop: `ssh root@ib-gateway radon stop`. Designed for fast off-hours shutdowns from iPhone/Termius.

Initial provisioning installs the checked-in operator and Gateway helpers. For a live update to root-owned helpers, sudoers, polkit rules, or a unit covered by the control-plane manifest, use the root bootstrap transaction below rather than copying files or running `systemctl daemon-reload` directly.

---

## Testing

The project includes a comprehensive test suite (202 tests) validating all configuration files.

### Setup

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements-test.txt
```

### Run tests

```bash
pytest tests/ -v
```

### Test coverage

| Test file | What it validates |
|-----------|-------------------|
| `test_docker_compose.py` | YAML validity, port bindings, health checks, security settings |
| `test_systemd_services.py` | Unit structure, dependencies, restart policies, user permissions |
| `test_caddyfile.py` | Routing rules, security headers, compression, TLS config |
| `test_env_example.py` | Required variables, safe defaults, no leaked secrets |
| `test_scripts.py` | Script structure, idempotency, health checks, rollback logic |
| `test_integration.py` | Cross-file port/path consistency, dependency chains, security |

---

## Environment & Gateway Mode

The radon codebase supports three IB Gateway modes (`docker`, `cloud`, `launchd`). The code default is `docker` (for local dev). On the VPS, systemd services load env vars from `/home/radon/radon-cloud/.env` via `EnvironmentFile=`, which must include:

```
IB_GATEWAY_MODE=cloud
```

Production `.env` also requires **Backblaze B2** keys for portfolio cold-archive
(`RADON_ARCHIVE_S3_*` — S3-compatible API to bucket `radon-archive`). Listed in
`config/required-env.txt` and documented in root `.env.example` /
`docs/cloud-services.md`. `radon-portfolio-archive.service` fails closed without
them. Cloudflare R2 is not used.

The production environment checker also requires `TRADING_MODE=live` with
`IB_GATEWAY_PORT=4001`, or `TRADING_MODE=paper` with `IB_GATEWAY_PORT=4002`.
Unsupported or mismatched pairs fail before any service transition. The
container healthcheck probes internal port `4001` for live and `4002` for paper.

**Why `cloud` on the VPS?** The radon code's `docker` mode tries to manage IB Gateway from `docker/ib-gateway/`, a directory that does not exist on the VPS. On the VPS, `radon-ib-gateway.service`, the operator, boot, and watchdog all delegate lifecycle changes to the monorepo `cloud/` lease-aware control helper. The `cloud` mode tells FastAPI and the WS relay to skip local container lifecycle management and use a TCP health check.

**How env vars flow on the VPS:**

1. systemd starts FastAPI/relay with `EnvironmentFile=/home/radon/radon-cloud/.env`
2. Python's `load_dotenv("/home/radon/radon/.env")` finds no file (root `.env` is gitignored, never created on VPS) — silently skips
3. `os.environ.get("IB_GATEWAY_MODE", "docker")` finds `cloud` from the systemd env — code default `docker` is never reached
4. Node.js relay reads `IB_GATEWAY_MODE` from the same systemd env

**Critical:** If a root `.env` is ever created at `/home/radon/radon/.env`, `load_dotenv()` would load it and could override the systemd env. Don't create one on the VPS.

The deploy script writes only literal `NEXT_PUBLIC_*` lines to `web/.env` and
sets mode `0600`. Server-side build values are parsed without interpolation and
exist only in the build process environment. The production cloud `.env` must
also be mode `0600`. The script does **not** create a root `.env` in the radon
directory.

---

## Authentication

- **Clerk** handles OAuth (Google, GitHub, X) on the frontend
- **JWT validation** on every FastAPI and WebSocket request
- **User allowlist** (`ALLOWED_USER_IDS` in `.env`) restricts access to specific Clerk user IDs
- **WebSocket ticket flow**: client obtains a short-lived ticket via `POST /api/ib/ws-ticket`, then connects with `?ticket=<UUID>` (no JWT in URL)
- `/health` is the only unauthenticated endpoint

### IB Gateway 2FA

On first login, IB Gateway requires second-factor authentication via the IBKR
mobile app. IBC exits after an unattended timeout and Docker does not relaunch
it. The lock-aware watchdog is the sole automated restart owner and applies its persisted
threshold, backoff, and push cap. Use the operator control path for a manual
recovery; do not bypass it with `docker compose up`:

```bash
ssh root@ib-gateway 'radon restart'
```

To access the IB Gateway GUI for debugging, set `VNC_SERVER_PASSWORD` in `.env` and tunnel VNC:

```bash
ssh -L 5900:127.0.0.1:5900 radon@ib-gateway
# Connect VNC client to localhost:5900
```

`EXISTING_SESSION_DETECTED_ACTION=primary` in `.env` ensures the VPS gateway takes over if another IB session is active.

### Clerk Production Setup

Production Clerk requires:

1. **Own OAuth credentials** — Google, GitHub, X OAuth apps with redirect URI: `https://clerk.radon.run/v1/oauth_callback`
2. **5 DNS CNAME records** on your domain:
   - `clerk` → `frontend-api.clerk.services`
   - `accounts` → `accounts.clerk.services`
   - `clkmail` → `mail.<instance>.clerk.services`
   - `clk._domainkey` → `dkim1.<instance>.clerk.services`
   - `clk2._domainkey` → `dkim2.<instance>.clerk.services`
3. **Production API keys** (`pk_live_`, `sk_live_`) in `.env` — dev keys (`pk_test_`, `sk_test_`) won't work
4. **Production user ID** in `ALLOWED_USER_IDS` — differs from dev instance

---

## Caddy Configuration

```
your-domain.com {
    handle /ws* {
        reverse_proxy localhost:8765
    }
    handle_path /api/ib/* {
        reverse_proxy localhost:8321
    }
    handle {
        reverse_proxy localhost:3000
    }
}
```

`handle_path` strips the `/api/ib` prefix before forwarding to FastAPI. Caddy auto-provisions and renews TLS certificates via Let's Encrypt.

---

## Deploy Pipeline

```
Push to radon main
  → GitHub Actions
  → SSH to VPS
  → Fetch and verify the tested SHA
  → Build Bun artifacts and Python wheels in a detached worktree
  → Fsync journal and back up the exact live runtime artifacts
  → Snapshot topology and quiesce all non-beta Radon consumers
  → Promote artifacts and create the target venv offline
  → Restore the prior active topology and run the full release gate
  → Fsync verified state, write the green marker, commit topology
  → Recover from the journal on any failure or interruption
```

---

## Rollback

### Automatic (deploy failure)

The deploy script restores the previous commit, dependency trees, build output,
public web environment, Python virtual environment, and active service/timer
topology if an unverified transition fails. A fresh deploy process resolves any
remaining journal before it can start new work. A verified target is finalized
only if its source, artifacts, virtual environment, topology, and full gate still
pass; otherwise it is rolled back.

### Full teardown and rebuild

```bash
# Wipe everything (keeps SSH, firewall, IP)
ssh root@ib-gateway 'bash -s -- --force' < scripts/wipe-vps.sh

# Bootstrap from scratch
ssh root@ib-gateway 'bash -s' < scripts/setup-vps.sh
# Add SSH key to GitHub
ssh root@ib-gateway 'bash -s' < scripts/setup-vps.sh

# Complete setup
scripts/post-setup.sh
```

---

## Security

- JWT validated with `iss` claim on every request
- User allowlist for single-tenant access control
- WebSocket uses short-lived tickets (no JWT in URLs)
- Caddy enforces HTTPS with auto-redirect
- IB Gateway port 4001 is bound to loopback and the Tailscale interface IP (`100.112.32.16`) only, never to the public NIC. The VPS FastAPI uses loopback; the laptop connects over Tailscale.
- SSH via Tailscale only (ufw blocks public SSH)
- `.env` and `.env.production` are gitignored and must never be committed. A credential-shaped example previously entered repository history; credential rotation and a coordinated destructive history rewrite remain required separately.
- GitHub Actions pinned by commit SHA
- Deploy sudoers grants only five exact invocations of the root-owned `/usr/local/sbin/radon-deploy-root` helper; the helper discovers non-beta Radon units with a required core-service floor and owns the fixed stale-replica cleanup paths
- The IB Gateway image is digest-pinned, and IBC scheduled/cold restarts are blank so only the lease-aware watchdog can initiate a 2FA-producing cycle
- Cloud CI fetches full Git history and scans it with default Gitleaks rules plus literal TWS-assignment and credential-example rules
- Unit files are copied to root-owned `/etc/systemd/system/` (not symlinked from user-writable paths)

### DNS / TLS

- Caddy provisions TLS certificates automatically via Let's Encrypt
- If your domain has existing **CAA records**, add: `0 issue "letsencrypt.org"`
- Configure your SSH client with `IdentityFile`:
  ```
  Host ib-gateway
    HostName <VPS_IP>
    User radon
    IdentityFile ~/.ssh/id_ed25519
  ```

---

## Cost

| Service | Monthly |
|---------|---------|
| Hetzner CPX11 (2 vCPU, 2GB RAM) | ~$4-6 |
| Clerk (free tier, 10K MAU) | $0 |
| Domain | ~$1/mo |
| **Total** | **~$5-7/mo** |

---

## Phase 2 (future)

- Move Next.js to Vercel after consolidating API routes into FastAPI
- Multi-tenant: users bring own IB credentials, per-user container orchestration
- Rate limiting, abuse controls, billing

---

## Related Repos

- [radon](https://github.com/joemccann/radon) — Application code (FastAPI, relay, Next.js, scanners)
- [market-data-warehouse](https://github.com/joemccann/market-data-warehouse) — Historical market data pipeline (shares the same VPS IB Gateway)

---

## License

MIT
