# Radon FastAPI — Codex Instructions

Applies under `scripts/api/`. Root and `scripts/AGENTS.md` also apply. Mirrors `scripts/api/CLAUDE.md`.

## Architecture

- FastAPI runs on `localhost:8321`; Next.js calls it via `radonFetch()`.
- No `spawn()` from Next.js. All Python execution goes through FastAPI routes using `run_script` / `run_script_raw`.
- `run_script` expects JSON stdout. `run_script_raw` is for scripts that emit report text or write files.
- Do not return 4xx for legitimate empty/pending states; return 200 with a payload flag such as `missing: true`.
- `/api/pi` and `/pi/exec` script allowlists are security-sensitive. Do not expand without review.

## Auth

- FastAPI routes are JWT-protected by default.
- Localhost bypass covers server-to-server development traffic.
- Auth-exempt: `/health`, `/ws-ticket/validate`, `/docs`, `/openapi.json`, and all `*/share` routes.
- WebSocket tickets have 30s TTL.

## IB Gateway Modes

- `docker`: local and Hetzner Docker Gateway.
- `cloud`: laptop dev via Tailscale `ib-gateway:4001`; TCP probe only, no restart.
- `launchd`: legacy local IBC wrappers.
- Switching mode via `scripts/ib mode {local|cloud}` requires dev stack restart.
- Auto-recovery must verify port down or CLOSE_WAIT before restart. Client ID collision, VOL error, or transient timeout is not restart evidence.

## 2FA Restart Rules

- `/health` exposes `auth_state`, `service_state`, `upstream_dead`, and `restart_backoff`.
- Restart paths must acquire the cross-process 2FA push lock and respect backoff. Do not stack IBKR Mobile pushes.
- `POST /ib/reset-backoff` clears in-memory restart backoff and push lock.
- IBC relogin on 2FA timeout is disabled; do not re-enable.
- Watchdog stuck-2FA self-heal fires only after repeated stuck cycles when no push is in flight and backoff has elapsed.
- If `auth_state=authenticated` but pool clients remain disconnected while a direct probe works, restart `radon-api.service`.

## Service Health / Timers

- Each scan subprocess records its own snapshot + `service_health` row via `scripts/db/scan_mirror.py` (the FastAPI-side mirror is gone).
- Scheduled writers must heartbeat every cycle, including no-change cycles.
- Failures record `state=error` with `last_error.message`.
- Hetzner timers live in `radon-cloud/services/`; wrappers use literal env parsing to avoid `$VAR` expansion.

## Subprocess JSON Discipline

- Script stdout should contain only result JSON.
- Progress/debug output goes to stderr.
- JSON extractors are defensive, but script discipline is the primary guarantee.
