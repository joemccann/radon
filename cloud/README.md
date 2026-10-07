> **Monorepo:** `cloud/` in [`joemccann/radon`](https://github.com/joemccann/radon) is the source of truth for production infrastructure.
> The standalone `radon-cloud` checkout is legacy compatibility only; `/home/radon/radon-cloud/.env` is its sole external-secrets exception.
> Lifecycle, rollback, and bootstrap contract: [`cloud/CLAUDE.md`](CLAUDE.md) and [`docs/monorepo-cloud-migration.md`](../docs/monorepo-cloud-migration.md).

# Radon Cloud

Deployment infrastructure for the [Radon](https://github.com/joemccann/radon) trading terminal backend on a Hetzner VPS.

---

## What this directory contains

Production configuration for the Radon monorepo. Application code and infrastructure are tested and deployed at the same Git SHA.

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
└── README.md
```

---

## Architecture

The [host-split owner](../docs/spof-host-split.md) describes app and broker
roles. [Runtime planes](CLAUDE.md#runtime-planes) owns process isolation;
checked-in [units](services/), [Caddy](caddy/Caddyfile) and
[production compose](docker-compose.yml) own exact routes, ports and bindings.
Do not use the app host as a Gateway endpoint.

## Stack

| Concern | Choice |
|---------|--------|
| **Auth** | [Clerk](https://clerk.com) — OAuth (Google, GitHub, X), JWT validation |
| **Ingress** | [Caddy](https://caddyserver.com) — auto-TLS, reverse proxy, WebSocket |
| **Deploy** | GitHub Actions + SSH — push-to-deploy in ~30-60s |
| **Frontend** | Next.js on the VPS. Marketing site is the separate `site/` Vercel project. |
| **VPS** | Hetzner, Ubuntu 24.04, Ashburn VA |

---

## Prerequisites

- Hetzner Cloud account + VPS provisioned
- Domain with DNS A record pointing to VPS IP
- Tailscale access to the app and broker paths declared in [the policy](tailscale/policy.hujson)
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

Read the [provisioning and privileged-bootstrap owner](CLAUDE.md#privileged-bootstrap)
and [setup source](scripts/setup-vps.sh) before provisioning. Setup uses anonymous
HTTPS for the public repository and does not generate a GitHub SSH credential.
Do not run setup against active production services as an upgrade shortcut.

### 2. Complete setup

The [environment owner](CLAUDE.md#environment-handling) and
[post-setup source](scripts/post-setup.sh) own prerequisite checks and staging.
Use the [host-split runbook](../docs/spof-host-split.md) for host identity and
[Gateway readiness](../docs/ib-gateway-recovery.md#readiness-verification)
for authentication; a coarse HTTP response is not the release gate.

### 3. Deploy application code

The [deployment owner](CLAUDE.md#deployment-contract) owns exact-SHA admission,
required image builds, service transitions, health gates and interrupted-release
recovery. The [CI workflow](../.github/workflows/ci.yml) and
[deploy source](scripts/deploy.sh) own job dependencies, deadlines and artifact
reuse. Main pushes deploy only after required gates succeed; do not substitute
an older green check or the build-only workflow for the release's own checks.

### Control-plane changes

Root-owned helpers, sudoers, polkit rules, and systemd units covered by the
control-plane manifest are installed or updated only through the root bootstrap
transaction, run from root's own clone of the pinned remote (never the
radon-owned checkout):

```bash
# as root; converges on the GitHub main tip via root's own clone
/usr/local/sbin/radon-deploy-root sync-control-plane
```

It serializes with deploy and Gateway transitions, validates and atomically
installs its managed artifacts, reloads systemd once, verifies the manifest, and
publishes readiness. The next release performs the installed-control-plane
manifest preflight before it may transition services. Do not use
`setup-vps.sh` as a live upgrade shortcut or install managed files directly.
See [the cloud operating contract](CLAUDE.md) and [the monorepo lifecycle
runbook](../docs/monorepo-cloud-migration.md) for the canonical procedure.

Allowlisted scheduled units (`config/auto-sync-units.txt`) are not part of
that bootstrap set. After the helper and sudoers from this checkout are
installed once, each green CI deploy runs `radon-deploy-root
sync-scheduled-units`: git objects at the GitHub main tip, manifest hash
match, `0644 root:root` install, `daemon-reload` only.

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
ssh radon@radon-app

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

[Operator controls](../docs/operations.md) and the
[host-split owner](../docs/spof-host-split.md) define host-specific scope.
App-host controls affect the app plane; broker controls own Gateway lifecycle.
Follow [Gateway recovery](../docs/ib-gateway-recovery.md) for holds, leases,
stop conditions and authenticated verification. Timer-owned jobs remain
scheduler-controlled. Live control-plane refresh belongs to
[the privileged-bootstrap owner](CLAUDE.md#privileged-bootstrap).

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

The [cloud environment owner](CLAUDE.md#environment-handling) owns staging,
file permissions and build/runtime separation. Exact required names and role
checks live in [required-env.txt](config/required-env.txt) and
[check-env.py](scripts/check-env.py). [Mode switching](../docs/cloud-services.md#mode-switch)
owns launcher effects; [Gateway recovery](../docs/ib-gateway-recovery.md)
owns authentication and cycle semantics.

## Authentication

The [API trust boundary](../scripts/api/CLAUDE.md#authentication) and executable
[auth implementation](../scripts/api/auth.py) own JWT and bypass behavior.
The [WebSocket ticket implementation](../web/app/api/ib/ws-ticket/route.ts)
owns relay admission; do not infer trust from a health response.

### IB Gateway 2FA

Use the [Gateway recovery owner](../docs/ib-gateway-recovery.md), including
its prerequisites, blast radius, diagnosis, stop conditions, verification,
rollback and escalation. Identify the broker through the
[host-split owner](../docs/spof-host-split.md); app-host restart or VNC access
does not target the broker Gateway. The [production compose source](docker-compose.yml)
owns GUI bindings and authentication settings; verify the installed broker
artifact before requesting any lifecycle or GUI change.

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

Use the [deployment and rollback owner](CLAUDE.md#deployment-contract) for
source/artifact provenance, the transition journal, restored topology and the
health gates required before the green marker. The code-controlled gate owns
exact timing and fallback admission; this index does not duplicate its sequence.

## Rollback

Interrupted and failed releases use the same owner's recovery procedure.
Verify the exact restored source, artifacts, topology and health before treating
rollback as complete.

### Full teardown and rebuild

Do not use a copied wipe/bootstrap sequence as recovery. Review the
[destructive wipe source](scripts/wipe-vps.sh), verify recoverable backups
through [the backup owner](../docs/cloud-services.md#db-backup--restore-dur-13),
and follow [the exact-SHA deployment and rollback owner](CLAUDE.md#deployment-contract).
Stop if backups, host identity, installed control-plane provenance or recovery
access are uncertain. This section supplies no executable teardown sequence;
use the backup and deployment recovery owners above.

## Security

- JWT validated with `iss` claim on every request
- User allowlist for single-tenant access control
- WebSocket uses short-lived tickets (no JWT in URLs)
- Caddy enforces HTTPS with auto-redirect
- The [host-split owner](../docs/spof-host-split.md) and [network policy](tailscale/policy.hujson) own Gateway reachability and app/broker trust boundaries.
- `setup-vps.sh` `open_firewall()` resets ufw to the declared app ruleset (default deny incoming, no blanket `tailscale0` allow, 8321 only from `10.0.0.4` and operator `/32`s) and enables it; the rules are mirrored from the operator tool `cloud/scripts/host-firewall.sh` (runbook: `docs/operations.md` "Host firewalls")
- Public 22/tcp is open but keys-only (password and keyboard-interactive auth disabled by the sshd drop-in `setup-vps.sh` installs); Tailscale SSH is the primary route
- `.env` and `.env.production` are gitignored and must never be committed. A credential-shaped example previously entered repository history; credential rotation and a coordinated destructive history rewrite remain required separately.
- GitHub Actions pinned by commit SHA
- Deploy sudoers grants only exact invocations of the root-owned `/usr/local/sbin/radon-deploy-root` helper (`stop-clean`, `restart-managed`, `recover`, `verify-restored`, `verify-control-plane`, `commit-transition`, `install-units`, `revert-units`, `sync-scheduled-units`, plus `publish-caddy` in its own fragment); the helper discovers non-beta Radon units with a required core-service floor, owns the fixed stale-replica cleanup paths, and installs the manifest-pinned timer-owned units (`install-units`) during each deploy; a rollback reverts exactly those changes (`revert-units`). `sync-scheduled-units` re-reads allowlisted units from git objects at the GitHub main tip and never starts, stops, or enables units.
- [Gateway daily-cycle semantics](../docs/ib-gateway-recovery.md#daily-cycle) and [production compose](docker-compose.yml) own restart behavior; blank fields do not disable the stored cycle.
- Cloud CI fetches full Git history and scans it with default Gitleaks rules plus literal TWS-assignment and credential-example rules
- Unit files are copied to root-owned `/etc/systemd/system/` (not symlinked from user-writable paths)

### DNS / TLS

- Caddy provisions TLS certificates automatically via Let's Encrypt
- If your domain has existing **CAA records**, add: `0 issue "letsencrypt.org"`
- Configure your SSH client with `IdentityFile`:
  ```
  Host radon-app
    HostName <VPS_IP>
    User radon
    IdentityFile ~/.ssh/id_ed25519
  ```

---

## Cost

| Service | Monthly |
|---------|---------|
| Hetzner VPS | host-dependent |
| Clerk (free tier, 10K MAU) | $0 |
| Domain | ~$1/mo |
| **Total** | **~$5-7/mo** |

---

## Related

- This directory is part of [joemccann/radon](https://github.com/joemccann/radon), not a standalone repo.
- Archived Phase 1 plan: [`docs/archive/sessions/cloud-phase1-plan.md`](../docs/archive/sessions/cloud-phase1-plan.md) (do not execute).
- [market-data-warehouse](https://github.com/joemccann/market-data-warehouse) shares the VPS IB Gateway.

---

## License

Proprietary. See [`LICENSE`](../LICENSE). No MIT grant.
