# Radon Cloud

Deployment infrastructure for the Radon trading terminal on a Hetzner VPS. **This repo contains no application code** — only systemd services, Caddy config, Docker Compose, deploy scripts, and environment templates.

## Repo Layout

```
radon-cloud/
├── docker-compose.yml      # IB Gateway container (port 4001, localhost only)
├── services/               # systemd unit files (7 units)
├── caddy/Caddyfile         # Reverse proxy: Next.js + FastAPI + WebSocket
├── scripts/
│   ├── setup-vps.sh        # One-time VPS bootstrap (run as root, two-pass)
│   ├── post-setup.sh       # Local Mac script: env, build, services, data, verify
│   ├── deploy.sh           # Health-gated deploy with auto-rollback
│   ├── migrate-data.sh     # Snapshot + transfer runtime data
│   └── wipe-vps.sh         # Reset VPS to pre-setup state for clean rebuild
├── tests/                  # Pytest suite validating all configs
├── .env.example            # Full environment variable matrix
└── PLAN.md                 # Architecture + task dependency graph
```

## Architecture

All services run on a single Hetzner VPS behind Caddy (auto-TLS):

- **Caddy** (443) → reverse proxy with path-based routing
- **Next.js** (3000) → full web terminal UI + API routes
- **FastAPI** (8321) → IB-backed REST API, order management
- **Node relay** (8765) → realtime WebSocket price streaming
- **Monitor daemon** → fill tracking, exit orders
- **Data refresh timer** → CRI/VCG scans during market hours
- **IB Gateway** (4001) → Interactive Brokers API (Docker, localhost only)

## Related Repos

- `radon` — Application code (FastAPI, relay, Next.js, scanners)
- `market-data-warehouse` — Historical market data pipeline

## Key Conventions

- **Single-tenant**: Only allowlisted Clerk user IDs can access the system
- **Auth**: Clerk JWT validated on every request; WebSocket uses short-lived ticket flow
- **Deploy**: Push to radon `main` triggers GitHub Actions → SSH → deploy.sh (auto-deploy)
- **Rollback**: deploy.sh auto-rolls back on health check failure
- **CI/CD**: Requires `VPS_HOST` and `VPS_SSH_KEY` secrets in the radon repo's GitHub Actions settings
- **IB Gateway port 4001 is never public** — localhost only, always

## Deployment Conventions

- **Full setup is 3 commands**: `setup-vps.sh` (twice, for SSH key), then `post-setup.sh` (env, build, services, data, verify)
- `setup-vps.sh` must be run as root — it installs system packages and creates systemd units
- `post-setup.sh` runs locally from your Mac — writes `.env`, rebuilds Next.js, starts services with 2FA auto-retry, migrates data
- Store production secrets in `.env.production` (gitignored) — `post-setup.sh` reads it automatically
- Package sources: Docker from `download.docker.com`, Python 3.13 from deadsnakes PPA, Node.js 22 from NodeSource
- npm strategy: `npm ci` with fallback to `npm install` when lock file is out of sync
- Unit files are **copied** to `/etc/systemd/system/` (root-owned), not symlinked from the repo
- Sudoers grants only `systemctl restart radon-*` to the `radon` user
- `.env` must exist with required keys before services will start
- `.env` is copied to `radon/web/.env` at build time so `NEXT_PUBLIC_*` vars are baked in
- Unit file changes require manual root copy + `systemctl daemon-reload`
- `MDW_API_KEY` must be the same value in both `radon-cloud/.env` (server) and MDW's `.env` (client)

## Post-Deploy Learnings

Issues discovered during live deployment that inform future setups:

- **SSH key two-pass flow**: `setup-vps.sh` generates an ed25519 deploy key on first run, prints it, and exits. User must add the key to GitHub, then re-run the script to continue.
- **Root node_modules required**: The root `package.json` has shared deps (`@sinclair/typebox`) used by `lib/tools/` outside `web/`. Both `setup-vps.sh` and `deploy.sh` install root deps before building `web/`.
- **`.env` must be copied to `web/`**: `NEXT_PUBLIC_*` vars are baked at build time. After creating `radon-cloud/.env`, copy it to `radon/web/.env` before building Next.js.
- **Caddy reload vs restart**: On first setup Caddy hasn't started yet, so `systemctl reload caddy` fails. The script falls back to restart.
- **PyJWT requires `cryptography`**: RS256 JWT verification needs the `cryptography` package — listed in `radon/requirements.txt`.
- **Production Clerk user IDs differ from dev**: The `ALLOWED_USER_IDS` in `.env` must use the production Clerk user ID, not the dev instance ID.
- **Clerk production requires own OAuth credentials**: Google/GitHub OAuth in production Clerk needs your own app credentials (dev uses Clerk's shared ones).
- **5 Clerk CNAME records**: Production Clerk requires: `clerk`, `accounts`, `clkmail`, `clk._domainkey`, `clk2._domainkey` — all pointing to `*.clerk.services`.
- **FastAPI skips auth for localhost**: Server-to-server calls from Next.js (localhost:3000 → localhost:8321) bypass Clerk JWT auth since port 8321 is never public.
- **radon user authorized_keys**: If the VPS is wiped, root's authorized_keys must be copied to the radon user's `.ssh/` directory. `setup-vps.sh` handles this automatically.
- **IB Gateway 2FA timeout**: The 2FA push may not arrive on first container start. `post-setup.sh` auto-retries up to 3 times (90s each) with automatic container restart between attempts.
- **Docker group membership**: `usermod -aG docker radon` requires a new session. `setup-vps.sh` ensures group membership in `preflight_checks` on every run, not just during Docker install.
- **MDW API key auth**: The `MDW_API_KEY` env var enables headless machine-to-machine access to historical data endpoints. Scoped to `/contract/qualify`, `/historical/head-timestamp`, `/historical/bars` only — trading routes remain Clerk JWT-only.

## Running Tests

```bash
cd /Users/joemccann/dev/apps/finance/radon-cloud
pip install pytest pyyaml
pytest tests/ -v
```

## Environment Variables

Copy `.env.example` to `.env` and fill in secrets. **Never commit `.env`**.

Required sections:
- IB Gateway credentials
- Clerk authentication keys
- User allowlist (Clerk user IDs)
- API/WebSocket URLs
- Domain configuration

## Deploy Workflow

1. Push to `radon` repo `main` branch
2. GitHub Actions SSHs to VPS, runs `scripts/deploy.sh`
3. deploy.sh: git pull → pip install → npm build → restart services → health check
4. On failure: auto-rollback to previous commit

## Manual VPS Operations

```bash
# Bootstrap (first time only)
ssh radon@ib-gateway 'bash -s' < scripts/setup-vps.sh

# Check service status
ssh radon@ib-gateway 'sudo systemctl status radon-api'

# View logs
ssh radon@ib-gateway 'journalctl -u radon-api -f'

# Restart a service
ssh radon@ib-gateway 'sudo systemctl restart radon-api'
```

### Whole-stack kill switch (`radon` wrapper)

`/usr/local/bin/radon` controls every unit at once (IB Gateway + all `radon-*`). Run as root on the VPS, or remotely:

```bash
ssh root@ib-gateway radon stop      # full shutdown
ssh root@ib-gateway radon start     # full bring-up (IB first)
ssh root@ib-gateway radon restart
ssh root@ib-gateway radon status
```

Covers `radon-{ib-gateway,api,relay,monitor,newsfeed,nextjs}` + `radon-refresh.timer`. Designed for off-hours shutdowns from iPhone/Termius without memorizing the unit list.

**Provisioning gap (2026-05-04):** the wrapper was hand-installed and is **not** part of `setup-vps.sh`. A `wipe-vps.sh` rebuild drops it. To make it persistent across rebuilds, fold the install into `setup-vps.sh` (writes the script to `/usr/local/bin/radon`, `chmod +x`). Source for the wrapper lives in `README.md` § Managing services.

## Editing Guidelines

- Systemd units live in `services/` and are symlinked to `/etc/systemd/system/` on VPS
- After changing a service file, run `sudo systemctl daemon-reload` on VPS
- After changing Caddyfile, run `sudo systemctl reload caddy` on VPS
- All scripts use `set -euo pipefail` and include rollback on failure
- Keep `.env.example` in sync when adding new environment variables
