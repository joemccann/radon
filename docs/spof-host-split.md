# SPOF host split — operator runbook

Code in this SHA is combined-host safe. `RADON_HOST_ROLE` defaults to
`combined`. Merging does not create a second VM, move Gateway, or enable
Hetzner backups. Those steps are below and cannot be done by CI.

Do not merge the sudoers-grant commit until you can bootstrap in the same
window. Preflight refuses `scripts/*` / `config/*` mismatches until
`refresh-control-plane-privileged` is installed.

## What this SHA deploys on the current VPS

- Tailscale 4001 bind is `${IB_GATEWAY_TAILSCALE_BIND:-100.112.32.16}`.
- `hhlev` / `hyad` / `vixts` join `auto-sync-units.txt`.
- `refresh-control-plane-privileged` is in sudoers (needs one bootstrap).
- Relay and monitor are no longer `PartOf=` Gateway. A Gateway 2FA restart
  no longer cascade-stops the app plane.
- `RADON_HOST_ROLE=app|broker` is parsed but unused until you set it.

`llm-index` and `mktnews` stay off auto-sync (drift-allowlisted, not
installed). Control-plane timers (`db-backup`, `refresh`, `portfolio-sync`,
`drift-audit`, …) stay off auto-sync.

## Phase 0 — this week, on the box you have

1. Confirm backups. Repo has no record.

   ```bash
   hcloud server describe <current-server> -o json | jq '.public_net, .backup_window, .datacenter'
   ```

   If `backup_window` is empty: enable Hetzner automated backups (20% of
   server price, 7 daily slots). They do not survive server deletion.

2. Named snapshot at a known-green SHA, after a green deploy:

   ```bash
   hcloud server create-image --type snapshot --description "radon-green-<sha>" <server>
   ```

   Ashburn snapshots stay in Ashburn. Not off-site.

3. Copy `/etc/radon/env` to B2 under a tight prefix. Do not put it in git.

4. Restore drill, under 20 minutes, off RTH:

   - Snapshot.
   - Power off the live server (or create a *new* server from the snapshot
     and keep the original off).
   - Boot replacement.
   - One IBKR Mobile tap.
   - `curl -sS http://127.0.0.1:8321/health` → `auth_state=authenticated`.
   - Old VM stays off. Two Gateways, same username, one dies.

5. Optional: set `IB_GATEWAY_TAILSCALE_BIND` in `/etc/radon/env` if the
   tailnet IP is no longer `100.112.32.16`. Then `radon-ib-gateway-control
   restart` (2FA).

## Phase 0B — one root bootstrap (this SHA)

The commit that adds `refresh-control-plane-privileged` cannot auto-apply
that sudoers line. After merge, before the next CI deploy:

```bash
TARGET_SHA=<exact-tested-sha>
sudo -u radon -H git -C /home/radon/radon fetch --prune origin
TARGET_COMMIT="$(sudo -u radon -H git -C /home/radon/radon rev-parse "${TARGET_SHA}^{commit}")"
CURRENT_COMMIT="$(sudo -u radon -H git -C /home/radon/radon rev-parse HEAD)"
if [ "$CURRENT_COMMIT" != "$TARGET_COMMIT" ]; then
  sudo -u radon -H git -C /home/radon/radon merge --ff-only "$TARGET_COMMIT"
fi
test "$(sudo -u radon -H git -C /home/radon/radon rev-parse HEAD)" = "$TARGET_COMMIT"
cd /home/radon/radon
bash cloud/scripts/bootstrap-control-plane.sh
```

Do not restart Gateway. Re-run CI for the same SHA if the in-flight deploy
refused preflight. After this, helper/sudoers/polkit edits deploy without
root SSH.

## Phase 1 — second VM (not this deploy)

Create, in the same Ashburn DC, spread placement group, Hetzner Cloud
Network (RFC1918). Small CX. Tailscale + private net.

Broker VM:

- `RADON_HOST_ROLE=broker`
- `IB_GATEWAY_HOST=127.0.0.1`
- `IB_GATEWAY_TAILSCALE_BIND=<new 100.x>`
- Add a compose private-net bind for `10.x:4001` (not in this SHA; add
  when the 10.x exists).
- Own control-plane bootstrap. Gateway + helper + lease + watchdog only.
- Watchdog `HEALTH_URL=http://<app-10.x>:8321/health`.
- Secrets subset: TWS user/pass, VNC, session policy. Not `UW_TOKEN`.

App VM (current box):

- `RADON_HOST_ROLE=app`
- `IB_GATEWAY_HOST=<broker-10.x>` (RFC1918, not `100.x`, not public)
- Uninstall Gateway units **and** `/usr/local/bin/radon-ib-gateway-control`.
  FastAPI treats helper-exists as ownership. Leave it and boot starts a
  second Gateway.
- Cut complete: `systemctl is-enabled radon-ib-gateway` is not-found;
  `docker ps` has no `ib-gateway`.
- `check-env.py` will refuse until the host is RFC1918.

Move window is off RTH plus one Mobile tap on the broker. App deploy must
leave `auth_state=authenticated` on 4001.

Operator commands after the cut:

- App: `ssh root@ib-gateway radon {start|stop|restart|status}`
  App-plane only. Does not cycle Gateway.
- Broker: `ssh root@<broker> radon {start|stop|restart|status}`
  Gateway via `radon-ib-gateway-control`. Does not require radon-health.
- Admin page Force 2FA / Stop / Start Gateway is hidden on the app host.
  Do not POST `/api/admin/services/radon-ib-gateway.service/*` there.

Do not put the broker in Falkenstein. Do not run two Gateways. Do not
restore a broker snapshot beside a live broker. Do not start a standby
FastAPI pool against a live Gateway.

## Never

- Kubernetes / Compose-as-OS / hot second IBKR username as HA.
- Tailscale as the production order path.
- `hcloud server rebuild` of broker while the original is still logged in.

## Done when

App deploy does not 2FA. App VM kill does not kill the IB session. Broker
restore is exclusive. Unit-only and privileged helper edits need no
standing root session.
