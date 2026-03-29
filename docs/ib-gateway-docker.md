# IB Gateway Docker Setup

## Quick Start

```bash
cd docker/ib-gateway
cp .env.example .env        # Edit TWS_USERID
mkdir -p secrets
echo "YOUR_IB_PASSWORD" > secrets/ib_password.txt
chmod 600 secrets/ib_password.txt
scripts/docker_ib_gateway.sh start
```

Approve 2FA on IBKR Mobile when prompted. Docker's `restart: unless-stopped` handles reliability.

## Commands

| Command | Action |
|---------|--------|
| `scripts/docker_ib_gateway.sh start` | Start (validates secrets, checks launchd not running) |
| `scripts/docker_ib_gateway.sh stop` | Stop and remove container |
| `scripts/docker_ib_gateway.sh restart` | Restart container |
| `scripts/docker_ib_gateway.sh status` | Show container status and healthcheck |
| `scripts/docker_ib_gateway.sh logs` | Tail container logs |

## VNC Access (Gateway GUI)

VNC allows you to access the IB Gateway GUI inside the Docker container for configuration changes that can only be made through the UI.

### Setup

1. Set `VNC_SERVER_PASSWORD` in `docker/ib-gateway/.env`:
   ```
   VNC_SERVER_PASSWORD=your-vnc-password
   ```

2. Create `docker/ib-gateway/docker-compose.override.yml` to expose the VNC port:
   ```yaml
   services:
     ib-gateway:
       ports:
         - "127.0.0.1:${IB_VNC_PORT:-5901}:5900"
   ```

3. Recreate the container (restart alone won't pick up new ports):
   ```bash
   scripts/docker_ib_gateway.sh stop
   scripts/docker_ib_gateway.sh start
   ```

4. Connect with TigerVNC:
   ```bash
   open /Applications/TigerVNC.app --args localhost:5901
   ```
   Password: whatever you set in `VNC_SERVER_PASSWORD`.

   Install TigerVNC if needed: `brew install --cask tigervnc-viewer`

### When to Use VNC

- Changing API Precaution settings (order size limits, bypass flags)
- Verifying Gateway login status visually
- Debugging 2FA issues
- Adjusting market data subscriptions

## Recommended Gateway Settings

After connecting via VNC, configure these settings under **Configure > Settings**:

### API > Precautions

Check **"Bypass Order Precautions for API Orders"**. This prevents IB from rejecting API orders based on precautionary limits (e.g., order size > 75 contracts). Radon's Kelly sizing already enforces position limits at Gate 3.

Without this, you'll get IB error 383: "Size exceeds the Size Limit of N. Restriction is specified in Precautionary Settings."

### API > Settings

These should already be set by the Docker image, but verify:

- **Enable ActiveX and Socket Clients**: checked
- **Socket port**: 4001 (live) or 4002 (paper)
- **Allow connections from localhost only**: **unchecked** if using cloud mode (Tailscale remote access). Checked for local-only Docker mode.
- **Read-Only API**: unchecked (Radon places orders)

### Presets (if not bypassing precautions)

If you prefer to keep precautions active instead of bypassing, increase the limits under **Presets**:

- **Size Limit**: 500+ contracts (default 75 is too low for combo orders)
- **Total Value Limit**: increase or disable based on your account size

## 2FA Authentication

- **First start**: Approve 2FA on IBKR Mobile app
- **Daily restart** (23:58 ET): Auto-restarts via `AUTO_RESTART_TIME`, no 2FA needed
- **2FA timeout**: Container auto-restarts and re-sends 2FA notification (`TWOFA_TIMEOUT_ACTION: restart`)
- **Manual restart**: `scripts/docker_ib_gateway.sh restart` triggers new 2FA

## Healthcheck

Docker's built-in healthcheck runs every 30s:
```
bash -c 'echo > /dev/tcp/localhost/4001'
```

**Important:** Uses bash `/dev/tcp` builtin, NOT `nc`/`netcat` — the `gnzsnz/ib-gateway` image doesn't include netcat. Using `nc` causes the healthcheck to always fail, making the container permanently unhealthy and triggering autoheal restart loops.

- **healthy**: IB Gateway API is accepting connections
- **unhealthy**: API not responding (2FA pending, login failed, or Gateway crashed)
- **starting**: Within 120s startup grace period

Check via: `scripts/docker_ib_gateway.sh status` or `curl localhost:8321/health`

## Autoheal (Automatic Unhealthy Restart)

Docker's `restart: unless-stopped` only restarts containers that **exit**. When IB Gateway's API becomes unresponsive (session drop, 2FA expiry), the Java process stays alive but the healthcheck fails — the container goes `unhealthy` but never exits.

The `autoheal` sidecar container watches for this and restarts unhealthy containers automatically:

- Checks every 30s for containers labeled `autoheal=true`
- 180s grace period after container start (allows for IB's slow startup + 2FA)
- When unhealthy detected: `docker restart` the container
- IB Gateway re-authenticates on restart (`RELOGIN_AFTER_TWOFA_TIMEOUT: yes`)

This eliminates the infinite 502 loop where the API keeps trying to connect to an unresponsive Gateway.

**Autoheal logs:**
```bash
docker logs ib-gateway-autoheal-1
```

## Credential Security

- Password stored via Docker secrets at `docker/ib-gateway/secrets/ib_password.txt`
- File must be `chmod 600` (the start script validates this)
- Never committed to git (gitignored)
- VNC password in `.env` (also gitignored)

## Troubleshooting

### Container starts but API connections time out
- Check IBKR Mobile for 2FA approval
- View logs: `scripts/docker_ib_gateway.sh logs`
- Check launcher log: `docker exec ib-gateway-ib-gateway-1 cat /home/ibgateway/Jts/launcher.log | tail -20`

### "DISCONNECT_AUTHORIZATION_FAILED" in logs
- Wrong `TWS_USERID` or password in `.env` / `secrets/ib_password.txt`
- Account may be locked from too many failed attempts

### VNC connection refused
- Ensure `VNC_SERVER_PASSWORD` is set in `.env` (not commented out)
- Container must be recreated after adding VNC (`stop` then `start`, not just `restart`)
- Port 5900 may be in use by macOS Screen Sharing; use 5901 instead
- macOS Screen Sharing app doesn't work; use TigerVNC

### Container keeps restarting
- Check logs for login errors
- May need to wait for IB's login cooldown after failed attempts

### Switching back to launchd (rollback)
```bash
scripts/docker_ib_gateway.sh stop
export IB_GATEWAY_MODE=launchd
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.ibc-gateway.plist
~/ibc/bin/start-secure-ibc-service.sh
```

## Architecture

```
Host (macOS)                    Docker Container
┌─────────────┐                 ┌──────────────────┐
│ FastAPI:8321 │──TCP:4001──────│→ socat:4003 ──→   │
│ WS relay:8765│──TCP:4001──────│  IB Gateway Java  │
│ Subprocesses │──TCP:4001──────│  Xvfb + x11vnc    │
│ TigerVNC     │──TCP:5901──────│→ VNC:5900          │
└─────────────┘                 └──────────────────┘
```

Docker maps `localhost:4001 → container:4003` and `localhost:5901 → container:5900`. All existing scripts connect to `localhost:4001` unchanged.
