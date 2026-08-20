# Progress — 2026-03-29

## Session: Local Dev + Cloud IB Gateway Setup

### What was done

1. **Switched IB Gateway default from cloud to docker in code**
   - `ib_gateway.py` and `ib_realtime_server.js` defaults changed to `docker`
   - VPS `.env` explicitly sets `IB_GATEWAY_MODE=cloud` (overrides code default)

2. **Fixed WS ticket flow for local dev**
   - Created `web/app/api/ib/ws-ticket/route.ts` (Next.js proxy → FastAPI)
   - `wsTicket.ts` uses same-origin `/api/ib/ws-ticket` (works locally + behind Caddy)
   - `verify_clerk_jwt` dependency: localhost bypass added (matches middleware)
   - WS relay: localhost bypass for ticket validation

3. **Created dev mode scripts** (`scripts/cloud.sh`, `scripts/local.sh`)
   - `cloud.sh`: VPS gateway via Tailscale + local dev services (default workflow)
   - `local.sh`: fully local Docker gateway + local dev services

4. **VPS Tailscale setup for hybrid dev**
   - New VPS joined tailnet as `ib-gateway` with `tag:ib-gateway`
   - Tailscale ACL: SSH as `root`, `radon`, `mdw` for `autogroup:admin` → `tag:ib-gateway`
   - Port 4001 ACL: `tag:mdw-client` + `autogroup:admin` → `tag:ib-gateway:4001`

5. **VPS Docker port mapping fix**
   - Changed `127.0.0.1:4001:4001` → `0.0.0.0:4001:4003` (socat proxy)
   - gnzsnz image: Java Gateway listens on localhost:4001 inside container, socat on 4003 forwards
   - External connections (Tailscale) must go through socat:4003, not Java:4001 directly

6. **IB Gateway GUI: unchecked "Allow connections from localhost only"**
   - Via VNC (localhost:5900), Configure → API → Settings
   - Without this, Tailscale connections get `ECONNRESET`
   - Setting persists in Docker volume (`ib-config`)

7. **radon-cloud repo updates**
   - `docker-compose.yml`: port mapping `0.0.0.0:4001:4003`
   - `.env.production` + `.env.example`: added `IB_GATEWAY_MODE=cloud`
   - `README.md`: documented env loading flow and gateway mode
   - VPS live `.env`: added `IB_GATEWAY_MODE=cloud`

### Commits (radon)
- `12115a3` — switch IB Gateway default from cloud to local Docker
- `90d76df` — WS ticket flow: proxy through Next.js, bypass auth for localhost
- `f5fe7e5` — WS relay: bypass ticket auth for localhost connections
- `ea15139` — docs: auth, local dev, startup docs
- `f6890bf` — cloud.sh for hybrid dev
- `23cead6` — docs: cloud.sh as default workflow
- `4ed3c5a` — move scripts to scripts/ directory

### Commits (radon-cloud)
- `70b1fc5` — add IB_GATEWAY_MODE=cloud to env example
- `15e125c` — document IB_GATEWAY_MODE and env loading on VPS
- `6eb76bd` — bind port 4001 to all interfaces for Tailscale
- `8015384` — correct port mapping 4001:4003 for socat

### Key Learnings
- gnzsnz IB Gateway image uses socat:4003 → Java:4001 internally; external port must map to 4003
- `jts.ini` `TrustedIPs` doesn't override the GUI "localhost only" checkbox — must use VNC to change it
- `NEXT_PUBLIC_RADON_API_URL` only matters for ws-ticket; solved by routing through Next.js API proxy
- `verify_clerk_jwt` Depends() runs independently of middleware — both need localhost bypass
- WS relay needs separate localhost bypass from FastAPI
- VPS `.env` loaded via systemd `EnvironmentFile=`; root radon `.env` doesn't exist on VPS (gitignored)
