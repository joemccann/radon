# Progress — 2026-03-29

## Session: Switch IB Gateway from Cloud to Local Docker

### What was done

1. **Switched IB Gateway from Hetzner VPS to local Docker**
   - Updated `.env`: `IB_GATEWAY_HOST=127.0.0.1`, `IB_GATEWAY_MODE=docker`
   - Changed code defaults from `cloud` to `docker` in `ib_gateway.py` and `ib_realtime_server.js`
   - Updated all docs (CLAUDE.md, README.md, AGENTS.md, .env.example)

2. **Ensured VPS compatibility**
   - Added `IB_GATEWAY_MODE=cloud` to VPS live `.env` and `radon-cloud/.env.example`
   - Documented env loading flow in radon-cloud README (systemd EnvironmentFile → Python load_dotenv → code defaults)
   - VPS won't break on `git pull` because systemd env overrides code defaults

3. **Fixed WS ticket flow for local dev**
   - Created Next.js API route `/api/ib/ws-ticket` that proxies to FastAPI server-to-server
   - Updated `wsTicket.ts` to always use same-origin `/api/ib/ws-ticket` (works locally and behind Caddy)
   - Added localhost bypass to `verify_clerk_jwt` dependency (middleware had it, but `Depends()` ran independently)

4. **Created `local.sh` script**
   - One command to: stop VPS gateway via SSH, start local Docker, wait for healthy, launch `npm run dev`

5. **Stopped VPS IB Gateway** via `docker compose down` on the Hetzner VPS

6. **Installed missing Python deps** — `PyJWT` and `cryptography` for local Python 3.13

### Commits
- `12115a3` — fix: switch IB Gateway default from cloud to local Docker
- `90d76df` — fix: WS ticket flow for local dev — proxy through Next.js, bypass auth for localhost

### radon-cloud commits
- `70b1fc5` — fix: add IB_GATEWAY_MODE=cloud to env example
- `15e125c` — docs: document IB_GATEWAY_MODE and env loading on VPS

### TODOs
- Run full test suite when market opens Monday to verify live data flow
- Consider adding a `cloud.sh` script (reverse of `local.sh`) for switching back to VPS
