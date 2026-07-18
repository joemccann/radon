# Radon Cloud Backend — Phase 1

> **Archived implementation plan.** This pre-monorepo plan is historical
> context only. Its `radon-cloud` paths, symlink commands, standalone deploy
> script, and service-management instructions are superseded by
> [`cloud/CLAUDE.md`](CLAUDE.md) and
> [`docs/monorepo-cloud-migration.md`](../docs/monorepo-cloud-migration.md).
> Do not execute commands from this document on production.

## Context

Radon's backend (FastAPI, WebSocket relay, monitor daemon, Next.js, IB Gateway) currently runs on the local Mac. The goal is to colocate **everything** on the Hetzner VPS alongside IB Gateway, exposed via HTTPS/WSS with OAuth authentication. This eliminates the Tailscale raw TCP tunnel for data clients and enables access from any browser.

**Phase 1 scope**: Single-tenant (your IB account, your access only). Next.js runs on VPS (not Vercel) because API routes depend on local filesystem (`data/*.json`) and Python subprocess execution. Vercel migration is Phase 2 after API routes are consolidated into FastAPI.

---

## Architecture

```
Browser (any device)
  → Clerk OAuth (Google, GitHub, X)
  → HTTPS/WSS to api.radon.app

Hetzner VPS (api.radon.app)
  ├── Caddy (443 → reverse proxy, auto-TLS)
  │     ├── /          → Next.js (localhost:3000)
  │     ├── /api/ib/*  → FastAPI (localhost:8321)
  │     └── /ws        → Node relay (localhost:8765)
  ├── Next.js (localhost:3000) — full app including /app/api/* routes
  ├── FastAPI (localhost:8321) — IB-backed endpoints, order management
  ├── Node.js relay (localhost:8765) — realtime price streaming
  ├── Monitor daemon — fill tracking, exit orders
  ├── Data refresh timer — CRI/VCG scans
  └── IB Gateway (Docker, localhost:4001)

All services talk to IB Gateway over localhost. No raw TCP tunnel.
Tailscale retained for SSH management only.
```

---

## Stack Decisions

| Concern | Choice | Why |
|---------|--------|-----|
| **Auth** | Clerk | Managed OAuth + JWKS, prebuilt UI, 10K MAU free, JWT validation ~2ms |
| **Ingress** | Caddy | Auto-TLS, native WebSocket, zero added latency, simple config |
| **Deploy** | GitHub Actions + SSH (in radon repo) | Push-to-deploy, ~30-60s, audit trail |
| **Frontend** | Next.js on VPS (Phase 1) | API routes need local filesystem + Python. Vercel in Phase 2. |

---

## Task Dependency Graph

```
T1 (New repo + deps manifest)
T2 (Data migration) ← T1
T3 (VPS: systemd + Caddy + Next.js) ← T2
T4 (Auth: Clerk + JWT + user allowlist) ← T3
T5 (Deploy pipeline in radon repo) ← T3
T6 (Verify end-to-end) ← T4, T5
T7 (Docs)
```

### T1: New repo scaffold + dependency manifest
`depends_on: []`

**1a. Create `~/dev/apps/finance/radon-cloud`:**

```
radon-cloud/
├── docker-compose.yml          # IB Gateway (already working on VPS)
├── services/
│   ├── radon-nextjs.service    # systemd: Next.js (port 3000)
│   ├── radon-api.service       # systemd: FastAPI (port 8321)
│   ├── radon-relay.service     # systemd: Node.js relay (port 8765)
│   ├── radon-monitor.service   # systemd: monitor daemon
│   ├── radon-refresh.service   # systemd: data refresh (oneshot)
│   └── radon-refresh.timer     # systemd: market hours schedule
├── caddy/
│   └── Caddyfile
├── scripts/
│   ├── setup-vps.sh            # One-time bootstrap
│   ├── deploy.sh               # Pull + install + restart
│   └── migrate-data.sh         # Snapshot + transfer data/
├── .env.example
├── CLAUDE.md
└── README.md
```

**1b. Create `requirements-deploy.txt` in radon repo:**

Audit current imports and create a complete dependency list for VPS (FastAPI, uvicorn, ib_insync, python-dotenv, numpy, scipy, pandas, requests, PyJWT, etc.). The current `requirements.txt` is incomplete and `pyproject.toml` has no `[project]` metadata for `pip install -e .`.

### T2: Data migration
`depends_on: [T1]`

**Pre-cutover snapshot** of runtime state from local Mac to VPS:

```bash
# On Mac: snapshot current state
cd ~/dev/apps/finance/radon
tar czf /tmp/radon-data-snapshot.tar.gz \
  data/portfolio.json data/orders.json data/trade_log.json \
  data/daemon_state.json data/option_close_cache.json \
  data/cri.json data/vcg.json data/scanner.json \
  data/*.json

# Transfer to VPS
scp /tmp/radon-data-snapshot.tar.gz radon@ib-gateway:~/radon/

# On VPS: restore
ssh radon@ib-gateway 'cd ~/radon && tar xzf radon-data-snapshot.tar.gz'
```

**Rollback**: Keep the snapshot on the VPS. If migration fails, restore from snapshot.

### T3: VPS services + Caddy + Next.js
`depends_on: [T2]`

**3a. Systemd service files**

Each service runs from the radon repo clone on VPS (`/home/radon/radon`):

```ini
# radon-api.service
[Unit]
Description=Radon FastAPI server
After=network.target docker.service
[Service]
Type=simple
User=radon
WorkingDirectory=/home/radon/radon
EnvironmentFile=/home/radon/radon-cloud/.env
ExecStart=/home/radon/radon/.venv/bin/uvicorn scripts.api.server:app --host 127.0.0.1 --port 8321
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
```

```ini
# radon-nextjs.service
[Unit]
Description=Radon Next.js frontend
After=network.target
[Service]
Type=simple
User=radon
WorkingDirectory=/home/radon/radon/web
EnvironmentFile=/home/radon/radon-cloud/.env
ExecStart=/usr/bin/npm run start
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
```

Similar for relay (Node), monitor daemon, data refresh timer.

```ini
# radon-ib-gateway.service (ensure Docker Compose starts on boot)
[Unit]
Description=IB Gateway Docker Compose
After=docker.service
Requires=docker.service
[Service]
Type=oneshot
RemainAfterExit=yes
User=radon
WorkingDirectory=/home/radon/radon-cloud
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
[Install]
WantedBy=multi-user.target
```

This guarantees IB Gateway starts on boot before the app services that depend on it.

**3b. Caddy**

```
api.radon.app {
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

Caddy auto-provisions TLS. DNS A record: `api.radon.app` → VPS public IP.

**3c. Firewall**

```bash
sudo ufw allow 80/tcp    # ACME challenge
sudo ufw allow 443/tcp   # HTTPS
```

**3d. CORS update in radon FastAPI**

Use `allow_origin_regex` (not exact match) for flexibility:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://.*\.radon\.app|http://localhost:3000",
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**3e. Sudoers for deploy user**

```bash
echo "radon ALL=(root) NOPASSWD: /usr/bin/systemctl restart radon-*" | sudo tee /etc/sudoers.d/radon-deploy
```

### T4: Auth — Clerk + JWT + user allowlist
`depends_on: [T3]`

**4a. Clerk setup**

1. Create Clerk application, enable OAuth (Google, GitHub, X)
2. Configure JWT template: include `sub`, `email`, `iss`, `aud`, `azp`
3. Note JWKS URL

**4b. FastAPI JWT middleware with full claim validation**

```python
# scripts/api/auth.py
import os, jwt
from jwt import PyJWKClient
from fastapi import Request, HTTPException

CLERK_JWKS_URL = os.getenv("CLERK_JWKS_URL")
CLERK_ISSUER = os.getenv("CLERK_ISSUER")  # https://<app>.clerk.accounts.dev
ALLOWED_USERS = set(os.getenv("ALLOWED_USER_IDS", "").split(","))

jwks_client = PyJWKClient(CLERK_JWKS_URL, cache_keys=True)

async def verify_clerk_jwt(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing token")
    token = auth.removeprefix("Bearer ")
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token, signing_key.key,
            algorithms=["RS256"],
            issuer=CLERK_ISSUER,
            options={"verify_aud": False},  # Clerk doesn't set aud by default
        )
        # Single-tenant: only allowlisted users
        if payload["sub"] not in ALLOWED_USERS:
            raise HTTPException(403, "Not authorized")
        return payload
    except jwt.exceptions.PyJWTError as e:
        raise HTTPException(401, str(e))
```

`/health` excluded from auth. All other routes require valid JWT from an allowlisted user.

**4c. WebSocket auth — short-lived ticket flow (not query param)**

Avoid passing JWT in WebSocket URL (leaks in logs/proxies). Instead:

1. Client calls `POST /api/ib/ws-ticket` with Bearer JWT → returns short-lived ticket (UUID, 30s TTL)
2. Client connects `wss://api.radon.app/ws?ticket=<UUID>`
3. Node relay validates ticket against FastAPI's ticket store, then upgrades

This keeps the long-lived JWT out of WebSocket URLs.

**4d. Complete inventory of backend callers**

All files in `web/app/api/` that call FastAPI or open WebSocket connections must be updated to pass auth. Full audit:

- `web/lib/radonApi.ts` — primary fetch wrapper (attach Bearer header)
- `web/app/api/previous-close/route.ts` — opens WS directly (use ticket flow)
- `web/app/api/regime/share/route.ts` — calls `FASTAPI_URL` directly (route through `radonFetch`)
- `web/app/api/portfolio/route.ts` — reads local `data/portfolio.json` (stays as-is on VPS)
- Any other routes that call `FASTAPI_URL` or `fetch("http://localhost:8321/...")`

**4e. Clerk React integration**

```bash
cd radon/web && npm install @clerk/nextjs
```

- Wrap app in `<ClerkProvider>` in `layout.tsx`
- Add `middleware.ts` with Clerk auth
- `<SignIn />` / `<UserButton />` components
- `radonFetch` attaches `getToken()` as Bearer header

### T5: Deploy pipeline (in radon repo)
`depends_on: [T3]`

**5a. One-time VPS bootstrap** (`radon-cloud/scripts/setup-vps.sh`)

```bash
#!/bin/bash
set -euo pipefail

# Clone repos
cd /home/radon
git clone git@github.com:<user>/radon.git
git clone git@github.com:<user>/radon-cloud.git

# Python venv + deps
cd /home/radon/radon
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements-deploy.txt

# Node deps + build
cd /home/radon/radon/web
npm ci
npm run build

# Install Caddy
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update && sudo apt-get install -y caddy

# Caddy config
sudo cp /home/radon/radon-cloud/caddy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy

# IB Gateway docker-compose.yml + .env + secrets already in /home/radon/radon-cloud/
# (migrated from the existing /home/radon/ib-gateway/ setup)

# Symlink systemd services
sudo ln -sf /home/radon/radon-cloud/services/radon-ib-gateway.service /etc/systemd/system/
sudo ln -sf /home/radon/radon-cloud/services/radon-nextjs.service /etc/systemd/system/
sudo ln -sf /home/radon/radon-cloud/services/radon-api.service /etc/systemd/system/
sudo ln -sf /home/radon/radon-cloud/services/radon-relay.service /etc/systemd/system/
sudo ln -sf /home/radon/radon-cloud/services/radon-monitor.service /etc/systemd/system/
sudo ln -sf /home/radon/radon-cloud/services/radon-refresh.service /etc/systemd/system/
sudo ln -sf /home/radon/radon-cloud/services/radon-refresh.timer /etc/systemd/system/

# Reload and enable
sudo systemctl daemon-reload
sudo systemctl enable radon-ib-gateway radon-nextjs radon-api radon-relay radon-monitor radon-refresh.timer

# Start everything
sudo systemctl start radon-ib-gateway
sleep 10  # Wait for IB Gateway to init
sudo systemctl start radon-nextjs radon-api radon-relay radon-monitor radon-refresh.timer

# Open firewall for HTTPS
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

echo "Bootstrap complete. Verify: curl https://api.radon.app/health"
```

**5b. Deploy script** (`radon-cloud/scripts/deploy.sh`)

Health-gated with rollback:

```bash
#!/bin/bash
set -euo pipefail
cd /home/radon/radon
PREV_COMMIT=$(git rev-parse HEAD)

git fetch origin main
git reset --hard origin/main

source .venv/bin/activate
pip install -r requirements-deploy.txt --quiet
cd web && npm ci && npm run build && cd ..

sudo systemctl restart radon-nextjs radon-api radon-relay radon-monitor

# Health gate
sleep 5
if ! curl -sf http://localhost:8321/health > /dev/null; then
  echo "Health check failed! Rolling back to $PREV_COMMIT..."
  git reset --hard "$PREV_COMMIT"
  pip install -r requirements-deploy.txt --quiet
  cd web && npm ci && npm run build && cd ..
  sudo systemctl restart radon-nextjs radon-api radon-relay radon-monitor
  exit 1
fi
echo "Deploy OK: $(git log --oneline -1)"
```

**5b. GitHub Actions** (in **radon** repo: `.github/workflows/deploy.yml`)

```yaml
name: Deploy to VPS
on:
  push:
    branches: [main]
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: appleboy/ssh-action@4a03da89e5c43da56d502c52b5da314be107bb98  # v1.2.0 pinned by SHA
        with:
          host: ${{ secrets.VPS_HOST }}
          username: radon
          key: ${{ secrets.VPS_SSH_KEY }}
          script: bash /home/radon/radon-cloud/scripts/deploy.sh
```

Workflow lives in **radon** repo so pushes to radon trigger deploys. `radon-cloud` repo is deployed manually (infrastructure changes are rare).

### T6: End-to-end verification
`depends_on: [T4, T5]`

1. `curl https://api.radon.app/health` → 200 (no auth)
2. `curl -H "Authorization: Bearer <JWT>" https://api.radon.app/api/ib/portfolio/sync` → positions (Caddy strips `/api/ib` prefix, FastAPI sees `/portfolio/sync`)
3. WebSocket: obtain ticket via `/api/ib/ws-ticket`, connect `wss://api.radon.app/ws?ticket=<UUID>`, subscribe AAPL, verify price stream
4. Open `https://api.radon.app` in browser, sign in with Google, verify full terminal loads
5. Push trivial change to radon, verify VPS restarts within 60s
6. Open on iPhone browser (no Tailscale) — verify it works
7. Attempt access with a non-allowlisted Clerk account → 403

### T7: Documentation
`depends_on: [T6]`

- `radon-cloud/README.md` — Setup, architecture, deploy, troubleshooting
- `radon/CLAUDE.md` — Update with cloud backend references
- `radon-cloud/.env.example` — Full env matrix:
  - `IB_GATEWAY_HOST`, `IB_GATEWAY_PORT`
  - `CLERK_JWKS_URL`, `CLERK_ISSUER`, `ALLOWED_USER_IDS`
  - `UW_TOKEN`
  - `RADON_API_URL`, `NEXT_PUBLIC_IB_REALTIME_WS_URL`
  - `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`

---

## Rollback

**Service rollback** (health check fails):
Deploy script auto-rolls back to previous commit.

**Full rollback to local**:
```bash
# Stop VPS services
ssh radon@ib-gateway 'sudo systemctl stop radon-nextjs radon-api radon-relay radon-monitor'

# Run locally
cd ~/dev/apps/finance/radon
export IB_GATEWAY_HOST=127.0.0.1
# Start local IB Gateway, FastAPI, relay, Next.js as before
```

---

## Security

- [ ] JWT validated with `iss` claim check on every request
- [ ] User allowlist: only specific Clerk `user_id`s can access trading endpoints
- [ ] `/health` is the only unauthenticated endpoint
- [ ] WebSocket uses short-lived ticket flow (no JWT in URL)
- [ ] Caddy enforces HTTPS (auto-redirect)
- [ ] IB Gateway port 4001 never public — localhost only
- [ ] `.env` in `.gitignore`, never committed
- [ ] GitHub Actions pinned by version, secrets via GitHub encrypted secrets
- [ ] `sudoers` scoped to `systemctl restart radon-*` only
- [ ] Rate limiting: add after Phase 1 (single-tenant risk is low)

---

## What Changes in Existing Repos

### radon (code changes)

| File | Change |
|------|--------|
| `scripts/api/server.py` | CORS regex, auth middleware |
| `scripts/api/auth.py` | New: Clerk JWT verification + user allowlist |
| `scripts/api/ws_ticket.py` | New: short-lived WS ticket endpoint |
| `scripts/ib_realtime_server.js` | Validate WS ticket on connect |
| `web/lib/radonApi.ts` | Attach Clerk JWT to all API calls |
| `web/lib/usePrices.ts` | Obtain ticket, connect with ticket param |
| `web/lib/IBStatusContext.tsx` | Same ticket handling |
| `web/app/layout.tsx` | Wrap in `<ClerkProvider>` |
| `web/middleware.ts` | New: Clerk auth middleware |
| `web/app/api/regime/share/route.ts` | Route through `radonFetch` with auth |
| `web/app/api/vcg/share/route.ts` | Route through `radonFetch` with auth |
| `web/app/api/internals/share/route.ts` | Route through `radonFetch` with auth |
| `web/app/api/menthorq/cta/share/route.ts` | Route through `radonFetch` with auth |
| `web/app/api/previous-close/route.ts` | Use WS ticket flow |
| Acceptance criteria | `grep -rn "FASTAPI_URL\|localhost:8321\|localhost:8765\|new WebSocket" web/app/api/` returns zero unpatched hits |
| `requirements-deploy.txt` | New: complete VPS dependency list |
| `.github/workflows/deploy.yml` | New: push-to-deploy |

### market-data-warehouse (no changes)

### radon-cloud (new repo)

Deployment config, systemd services, Caddyfile, scripts, `.env.example`.

---

## Cost

| Service | Cost |
|---------|------|
| Hetzner VPS (existing CPX11) | ~$4-6/mo |
| Clerk (free tier, 10K MAU) | $0 |
| Domain | ~$10-15/yr |
| **Total** | **~$5-7/mo** |

---

## Phase 2 (future, not in scope)

- Move Next.js to Vercel after consolidating all `web/app/api/*` data routes into FastAPI
- Multi-tenant: users bring own IB credentials → per-user container orchestration
- Rate limiting, abuse controls, billing
