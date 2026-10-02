# IBKR Session Release (Operator Hold) - Design

Status: Phase 1 implemented (2026-10-01): hold module, shim/helper/unit/watchdog/broker-daemon enforcement and `radon ib release|resume|status`. Phase 3 in part (2026-10-01, later the same day): broker daemon `hold`/`unhold`, FastAPI `/ib/operator-hold` plus 423 refusals, the `/health` mirror, the admin Hold / Resume card, relay and grouping stand-down, the recovery-heartbeat skip, and HELD / cleared pages once per hold. Also in that change: IBC `primaryoverride` and a watchdog auto-hold on IBC's yield line (option C plus auto-hold, section 5). `release` no longer pauses the watchdog timer, so a clear from any path brings recovery back. Not built: the phone forced-command trigger, trading-halt coupling and local-mode guards. Option B (second IBKR username) was declined by the operator on 2026-10-01. The [current operator procedure](ib-gateway-recovery.md#runbook-flatten-from-ibkr-mobile-while-the-app-is-down) owns release, verification, rollback and escalation.

**Design boundary:** the inventories and sections below preserve the historical proposal, not deployed guarantees. `--full`, `--with-trading`, `--force-lease` and `status --json` are not supported by the broker CLI. Release does not set a trading halt, does not cancel resting orders, and does not stop local Gateways. Do not execute the proposed procedures below; use the current operator procedure above.

Goal: one action that releases every IBKR login Radon holds and keeps it released
(no auto-heal re-login) until the operator explicitly resumes, so the operator can
log in to IBKR web / TWS / mobile. Must work when the app host is down.

Trigger incident (2026-09-25 ~15:55-16:01 ET): broker Gateway runs IBC with
`EXISTING_SESSION_DETECTED_ACTION=primary` (`/etc/radon/env` on broker, default in
`cloud/docker-compose.yml:16`). Each operator login to interactivebrokers.com raised
IBC "Existing session detected ... scenario 5" (19:57:44, 19:58:44, 19:59:14 UTC) and
IBC reconnected the Gateway session, kicking the operator.

---

## 0. Key fact that shapes the design

Only an **IB Gateway / IBC JVM holds an IBKR login session**. API clients (FastAPI pool,
relay, monitor, timers) hold sockets to the *Gateway* (`10.0.0.4:4001`), never to IBKR.
They matter only as **re-trigger paths** (they can ask something to restart the Gateway).
Therefore:

- Release = stop every Gateway/IBC JVM on every host.
- Stay released = every path that can start a Gateway must honour one durable hold.

Verified live (read-only, 2026-09-25):

| Host | IBKR session holder | Evidence |
|---|---|---|
| broker `radon-broker` (100.67.204.57 / 10.0.0.4) | container `ib-gateway`, restart policy `no` | container netns: `172.18.0.2 -> 8.17.22.31:4000` ESTABLISHED (IBKR). Host `ss` does not show it (NAT); must use `nsenter -t <pid> -n ss` |
| app `ib-gateway` (5.78.148.38 / 10.0.0.2) | none | uvicorn (3 sockets) + node relay (1) -> `10.0.0.4:4001` only |
| Mac mini `joes-mac-mini` | **latent**: launchd `local.ibc-gateway` (`~/ibc/bin/run-secure-ibc-gateway.sh`, `~/ibc/config.secure.ini` with creds), loaded, `RunAtLoad=false`, `KeepAlive=false`, runs=0 | `launchctl print` |
| laptop | latent: `~/Library/LaunchAgents/local.ibc-gateway.plist.disabled`; local Docker mode `docker/ib-gateway/docker-compose.yml` via `scripts/docker_ib_gateway.sh` / `scripts/local.sh` | not loaded |
| demo `radon-demo` | none (no java, no :400x) | `pgrep`, `ss` |

---

## 1. Inventory: everything that holds or can RE-ESTABLISH a session

### 1a. Session holders
| # | Holder | Host | Source |
|---|---|---|---|
| H1 | `ib-gateway` container (IBC + Gateway JVM) | broker | `/etc/radon/ib-gateway-compose.yml` (root-owned copy of `cloud/docker-compose.yml`) |
| H2 | `local.ibc-gateway` launchd job | mini (loaded, idle), laptop (disabled) | `~/Library/LaunchAgents/local.ibc-gateway.plist` |
| H3 | Local Docker gateway | laptop/mini in `IB_GATEWAY_MODE=docker` | `docker/ib-gateway/docker-compose.yml`, `scripts/docker_ib_gateway.sh`, `scripts/api/ib_gateway.py:_docker_compose` |

### 1b. Gateway start / re-login paths (all must honour the hold)
All production paths converge on **two chokepoints** on the broker:
`/usr/local/bin/radon-ib-gateway-control` (`cloud/scripts/ib-gateway-control.sh`: `start|restart|restart-preheld`)
-> `sudo /usr/local/sbin/radon-docker-gw compose-up` (`cloud/scripts/radon-docker-gw.sh`, root).

| # | Path | Host | Trigger | Source |
|---|---|---|---|---|
| S1 | `radon-ib-gateway.service` `ExecStart=... start` | broker | **boot** (`WantedBy=multi-user.target`), `radon start/restart`, `sudo reboot` | `cloud/services/radon-ib-gateway.service` |
| S2 | `radon-ib-watchdog.timer` -> `scripts.ib_watchdog` -> `systemctl start radon-ib-gateway-preheld-restart.service` -> helper `restart-preheld` | broker | every 60s; restarts after 3 cycles on api-hang, **gateway DEAD/WEDGED when /health unreachable** (`_handle_primary_sensor_down`), stuck-2FA (data-plane window) | `scripts/ib_watchdog.py:_run_cycle_steps`, `:trigger_restart`; `cloud/services/radon-ib-watchdog{.service,.timer}` (broker drop-in `--health-url http://10.0.0.2:8321/health`) |
| S3 | `radon-ib-gateway-remote.service` verbs `start|restart` (mTLS, allow `10.0.0.2`) | broker | app FastAPI | `scripts/ib_gateway_remote/serve.py` (`MUTATIONS`) |
| S4 | FastAPI `POST /ib/restart` -> `admin_services.control_unit(GATEWAY_UNIT,"restart")` -> S3 | app | admin UI, **relay stale-data escalation** | `scripts/api/server.py:ib_restart`; `web/app/api/admin/ib/restart/route.ts` |
| S5 | Relay `escalateStaleData -> requestGatewayRestart()` (POST `IB_RESTART_URL`) | app | RTH, no ticks after reconnect ladder | `scripts/ib_realtime_server.js:778-870` |
| S6 | Admin Gateway Start / `radon restart` from UI | app | operator | `web/components/admin/Ib2faControls.tsx`, `/admin/services/radon-ib-gateway.service/{start,restart}` |
| S7 | `radon start|restart` (`/usr/local/bin/radon`), `sudo radon ...` granted to user radon | broker | operator / scripts | `cloud/scripts/operator-radon.sh:gateway_control`, `cloud/config/sudoers.d/radon-ops` |
| S8 | Laptop `scripts/cloud.sh` `ssh ib-gateway radon-ib-gateway-control start` | laptop | dev start | `scripts/cloud.sh:90-95` (targets app host; stale post-split, still a start path) |
| S9 | IBC internal relogin / `ExistingSessionDetectedAction=primary` | broker (in-container) | **IBKR "existing session" event - today's kicker** | compose env; `TWOFA_TIMEOUT_ACTION=exit`, `RELOGIN_AFTER_TWOFA_TIMEOUT=no`, `AUTO_RESTART_TIME=` already off |
| S10 | Local-mode FastAPI Docker auto-recovery (`restart_ib_gateway`), `scripts/docker_ib_gateway.sh`, `launchctl kickstart local.ibc-gateway` | laptop / mini | `scripts/local.sh` stacks | `scripts/api/ib_gateway.py:1429` |
| S11 | Deploy / bootstrap | app (CI) | CI deploy is app-only and app role never execs helper; broker updates are manual root SSH | `cloud/scripts/deploy.sh:111,354`, `bootstrap-control-plane.sh` |

Not start paths (verified): `restart_ib_gateway()` in cloud mode on app returns a refusal
(`ib_gateway.py:1469`); `_run_ib_script_with_recovery` (`server.py:5703`) uses that refusal;
grok page responder stands down on IB / restart pages (`docs/grok-page-responder.md:43-45`);
`radon-beta-api` has no `IB_GATEWAY_*` env; health daemon reads `/health/lite` only.

### 1c. Gateway socket clients (hold no IBKR session; generate noise / pages when Gateway is gone)
App host: `radon-api` pool (clientIds 3/4/5 + `_ib_recovery_heartbeat_loop` 15s, `recover_stuck_pool` probe clientId 98), `radon-relay` (10), `radon-monitor` (fill/exit-order/journal handlers), IB timers (`radon-portfolio-sync`, `radon-refresh`, `radon-vcg-refresh`, `radon-signals-refresh`, `radon-leap`, `radon-cta-sync`, indicator timers that use IB such as `radon-ivrank`, `radon-dispersion`, `radon-straddle`, `radon-skew*`, `radon-vol-cone*`), and watchdog buckets (`radon-watchdog-*`) that page on IB outage.

---

## 2. Durable operator hold

### Location and authority
- **Authoritative file on the broker**: `/var/lib/radon/ib-operator-hold.json` (owner `root:radon`, `0660`, dir already `StateDirectory=radon`). The broker is the only production host that can log in, so the hold lives where enforcement happens and survives app-host loss, reboots, deploys (broker is not CI-deployed) and container churn.
- Shared stdlib module `scripts/utils/ib_operator_hold.py` (no libsql, runs under `/usr/bin/python3.13` like `ib_2fa_lock.py`), plus a 20-line bash reader in the root shim (no Python dependency at the lowest layer).
- Mirrored read-only to the app host through the existing broker `/status` poll (`ib-gateway-remote` already polled ~2.5s): add `operator_hold` to its payload; FastAPI `/health` + `/health/lite` expose it.

### Schema
```json
{"held": true, "reason": "operator web login", "actor": "ssh:phone-key", "source_ip": "100.x",
 "held_at": "2026-09-25T19:58:00Z", "expires_at": null, "id": "hold-<uuid>"}
```

### Semantics (fail-safe = HELD, mirroring `scripts/trading_halt.py`)
| File state | Meaning |
|---|---|
| absent | not held |
| `{"held": true}` | held |
| `{"held": false, ...}` | not held (resume keeps audit) |
| unreadable / malformed / wrong owner / symlink | **HELD** |
| `expires_at` set and past | still HELD; watchdog sends a reminder, never auto-resumes |

No auto-expiry: an expiring hold re-logs the Gateway in and kicks the operator, which is the bug.
Reminder Pushover at 1h, 4h, then every 12h, plus a status chip. Writes are atomic (tmp + rename + fsync).

### Enforcement points (defence in depth)
1. `radon-docker-gw compose-up`: refuse (rc 73) if held. Root-owned, lowest layer; catches any future caller.
2. `radon-ib-gateway-control start|restart|restart-preheld`: check hold **inside** the lifecycle mutex (closes TOCTOU with a concurrent release); rc 73 `HELD`; `restart-preheld` also releases the watchdog lease (same as today's deploy-lock refusal). `stop` and `status` always allowed; `status` prints `held`.
3. `radon-ib-gateway.service`: `SuccessExitStatus=73` so boot under hold is `active (exited)`, not `failed` (avoids a `units.py` P1 and the DUR-02 start-limit brake).
4. `scripts/ib_watchdog.py:_run_cycle_steps`: first step; if held, outcome `operator_hold`, no probe-driven restart, no lease acquire, `service_health[ib-watchdog]=ok` with detail `operator hold since ...`.
5. `ib_gateway_remote/serve.py`: `start|restart` -> HTTP 423 `{"code":"OPERATOR_HOLD"}`; new verbs `hold` / `unhold` (see section 4).
6. FastAPI `/ib/restart` and `/admin/services/radon-ib-gateway*/start|restart` -> 423 when mirrored hold is set (fast refusal; broker still authoritative). Relay treats 423 as terminal for the escalation window. Its `/health/lite` poll accepts only an explicit boolean `operator_hold`: malformed successful replies, failed requests and invalid JSON retain the last confirmed value; only an explicit `false` clears a known hold.
7. Watchdog IB-outage grouping (`scripts/watchdog/`): while held, IB-shaped failures collapse to one P0 "IBKR operator hold active" line, no P1s.
8. Local paths (S8, S10, H2, H3): refuse unless `ssh radon-broker radon ib status --json` returns `held:false`; unreachable -> refuse (override `RADON_ALLOW_LOCAL_IB_LOGIN=1`). Preferred: decommission H2 (`local.ibc-gateway` + `config.secure.ini` on mini) since prod is cloud.

### Trading interaction
Release also sets the existing kill-switch flag on the app host (`scripts/trading_halt.py:set_halt`, `data/trading_halt.json`, `POST /trading/halt`) best-effort with reason `ibkr-operator-hold`, so when the Gateway returns nothing auto-transmits until the operator resumes trading explicitly. Release must **not** call `/trading/kill` (mass-cancel); resting orders at IBKR stay and the operator is about to manage them manually. The kill switch cannot itself be the hold: it lives on the app host and only gates order placement, not Gateway lifecycle.

---

## 3. Procedures

### Release (`radon ib release [--reason TEXT] [--full]`, runs on broker as root)
Every step is bounded; the command never blocks on deploy lock, 2FA lease or lifecycle mutex.
1. **Write hold** (atomic, no locks). Append audit. From here every start path refuses.
2. `systemctl stop radon-ib-watchdog.timer` (belt and braces; the hold already neuters it; timer stays enabled so reboot + hold is still safe).
3. **Stop Gateway**: `radon-ib-gateway-control stop` (graceful compose down, releases 2FA lease). If it returns 74 (deploy/mutex busy) or times out at 30s: `radon-docker-gw compose-down`; if container still running after 15s: new shim verb `kill` (`docker kill ib-gateway`). Loop until `inspect-running` = false or 90s budget.
4. **Verify broker**: container absent; `pgrep -f 'ibcalpha|IbcGateway|java'` empty on host; no conntrack/NAT entries to IBKR (`conntrack -L -p tcp --dport 4000` and `--dport 4001`, or `/proc/net/nf_conntrack`; add `conntrack` package).
5. **App host (best-effort, 10s budget, skipped if unreachable)** via mTLS-free path: `ssh radon@10.0.0.2 radon ib hold-notify` which calls `POST /trading/halt` over loopback. Default leaves api/relay/monitor running (they idle on refused connects, UI keeps showing HOLD). `--full` additionally runs `radon stop` there (stops persistent units + timers, persists topology), for runaway / compromise scenarios.
6. **Latent hosts**: print a checklist line for mini/laptop (`launchctl list | grep ibc`, `pgrep -f ibcalpha`). `--full` runs `ssh joes-mac-mini 'launchctl bootout gui/$(id -u)/local.ibc-gateway; pkill -f ibcalpha'` if reachable.
7. **Report + notify**: one-line result `RELEASED hold=<id> gateway=stopped ibkr_sockets=0 app=halted`, Pushover P0 with the same.
8. Operator waits ~30s (IBKR server session teardown), then logs in to web / TWS / mobile.

Break-glass if the broker shell is unreachable: Hetzner Cloud console or API `POST /servers/{id}/actions/poweroff` for the broker. Poweroff kills the socket; if the hold file was written, reboot is safe; if not, keep it powered off until done.

### Resume (`radon ib resume [--with-trading]`)
1. Operator logs out of web / TWS / mobile first (with `primary`, the Gateway login will otherwise end their session; that is now intended).
2. Refuse if a 2FA lease is active (`ib_2fa_lock.py status`) unless `--force-lease`.
3. Clear hold (`held:false`, audit), `systemctl start radon-ib-watchdog.timer`.
4. `radon-ib-gateway-control start` (acquires lease, one push). Print "Approve IBKR Mobile push now".
5. Poll broker-local probe (`managedAccounts` via the same probe the watchdog uses) up to 180s. On success: done. On timeout: leave hold cleared, do NOT retry (watchdog's stuck-2FA path owns retries under the lease and backoff ladder).
6. App side: pool self-heal reconnects within 15-60s (`recover_stuck_pool`); `--full` release is undone with `radon start` on the app host. Trading stays halted unless `--with-trading` (calls `POST /trading/resume`) or the operator resumes from the admin page.
7. Verify: `/health` `auth_state=authenticated`, pool clients connected, relay ticks, Pushover "RESUMED".

---

## 4. Trigger surfaces (work with the app host down)

| Surface | Works if app down | Auth | Notes |
|---|---|---|---|
| **SSH one-liner** `ssh radon-broker radon ib release` | yes | root key over Tailscale | Primary. Add `ib release|resume|status` subcommands to `cloud/scripts/operator-radon.sh` |
| **Phone: iOS Shortcut "IBKR Release"** (Shortcuts "Run script over SSH") | yes | dedicated ed25519 key in broker `authorized_keys` with `from="100.64.0.0/10",command="/usr/local/sbin/radon-ib-hold-ssh",no-pty,no-port-forwarding,no-agent-forwarding,no-X11-forwarding,restrict` | Forced command allowlists `release`, `resume`, `status` from `SSH_ORIGINAL_COMMAND`; nothing else. Requires Tailscale on phone. Separate "IBKR Resume" shortcut asks for typed confirm |
| **Pushover action link** | yes | link opens `shortcuts://run-shortcut?name=IBKR%20Release` on the phone; auth is still the SSH key | No public endpoint. Attach to every Pushover sent by S2/S5 and to a new "Existing session detected" alert (watchdog greps `radon-docker-gw logs` for the IBC line; P1) so today's incident would have been one tap |
| **Admin page button** (Release / Resume) | no | Clerk operator (`ALLOWED_USER_IDS`) + type-to-confirm; FastAPI -> mTLS `hold` verb on broker daemon | Convenience when app is up. `hold` bypasses `VERB_COOLDOWN_S` and the stop/start sequencing (`serve.py:56`); `unhold` does not bypass |
| **Hetzner poweroff** (console / `hcloud` token on phone) | yes | Hetzner account 2FA / scoped API token | Break-glass only |

Never: a public unauthenticated URL, Tailscale Funnel, signed GET links, or the Clerk loopback bypass for these verbs (already disabled for Gateway POSTs, `server.py:2158`).

Auth caveat found: broker `sshd -T` shows `listenaddress 0.0.0.0:22`, `passwordauthentication yes` (root is `without-password`). The phone key must carry `from=` tailnet; separately consider `ListenAddress` tailnet-only or confirm the Hetzner firewall blocks 22 publicly.

**Audit trail** (every set/clear/refusal): append-only JSONL `/var/log/radon/ib-operator-hold.jsonl` on broker (actor = key comment / `SSH_CLIENT` IP / `app:clerk:<user_id>`), journald tag `radon-ib-hold`, Pushover P0, and best-effort Turso `service_health['ib-operator-hold']` row (never blocking; broker may lack Turso). Refused starts log `HELD` with caller.

---

## 5. Separate host? Recommendation

**No new host.** The broker is the only production IBKR session holder, has its own systemd, is reachable over Tailscale when the app host is dead, and already hosts both lifecycle chokepoints. A control host adds a third failure domain, credentials and SSH trust for no capability the broker lacks. The only case it covers (broker itself wedged) is covered better by Hetzner poweroff.

| Option | Solves "operator can log in" | Solves "kill everything" | Cost / risk |
|---|---|---|---|
| A. Operator hold + release on broker (this doc) | yes, on demand | yes | ~4-5 days; Radon offline while held |
| B. **Dedicated IBKR secondary username for the Gateway** | **yes, permanently, zero downtime** | no | IBKR user add via Portal (Settings > Users & Access Rights), days of approval; market-data subscriptions are per user (second set of fees); separate 2FA (IB Key app supports multiple users; verify); operator manual trades run concurrently with bots (keep trading halt / order-risk chokepoint in mind) |
| C. `ExistingSessionDetectedAction=primaryoverride` | yes (operator login wins; IBC does not fight) | no | Gateway then sits logged out; watchdog stuck-2FA restart (~3 min in data-plane window) logs back in with a new push and kicks the operator again unless hold exists. Pair with auto-hold on "session taken over" detection. Verify behaviour against the pinned `gnzsnz/ib-gateway` IBC version before relying on it |
| D. `secondary` | yes | no | Gateway refuses to log in whenever any other session exists; restarts during operator sessions loop 2FA pushes every ~3 min. Not recommended |
| E. `manual` | n/a | no | Unattended dialog hangs the Gateway. Not viable |
| F. Separate control host | same as A | same as A | extra host, keys, drift; no added capability |

**Top recommendation:** do **B** (root-cause: stop sharing one username between a bot and a human) and **A** (kill switch for everything else). Until B lands, A with the Pushover "existing session detected" one-tap release is the fix for a repeat of today. Keep `primary` for the Gateway user once B is in place (the Gateway user should always win for itself).

---

## 6. Tests (red first) and phased plan

### Tests
| Area | Red test | File |
|---|---|---|
| Hold state | absent=not held; malformed / unreadable / symlink / wrong owner = HELD; atomic write; audit append; expiry never clears | `scripts/tests/test_ib_operator_hold.py` |
| Root shim | `compose-up` under hold exits 73 and never invokes docker stub; `compose-down` and new `kill` allowed | `cloud/tests/test_docker_gw_shim.py` |
| Helper | `start|restart|restart-preheld` under hold rc 73, docker stub never called; hold checked inside mutex (hold written while start waits -> refused); `restart-preheld` releases watchdog lease; `stop|status` allowed; `status` prints `held` | `cloud/tests/test_ib_gateway_control.py` |
| Unit | `radon-ib-gateway.service` has `SuccessExitStatus=73` | `cloud/tests/test_install_units.py` |
| Watchdog | held -> no `trigger_restart`, no lease, outcome `operator_hold`, health row ok; held + gateway DEAD for 10 cycles -> still no restart | `scripts/tests/test_ib_watchdog*.py` |
| Remote daemon | `start|restart` -> 423 under hold; `hold` bypasses cooldown immediately after a `restart`; `unhold` respects cooldown; peer allowlist unchanged | `scripts/tests/test_ib_gateway_remote*.py` |
| FastAPI | `/ib/restart` under mirrored hold -> 423 and **no** mTLS request made (assert the wire, per repo gated-action rule); `/health` exposes `operator_hold` | `scripts/api/tests/` |
| Relay | 423 from `/ib/restart` suppresses re-escalation for the window | `web/tests` / relay unit tests |
| Admin UI | armed Release button POSTs exact `/api/admin/ib/hold` with `{confirm:true, reason}`; nothing fires while unconfirmed | `web/tests/admin-ib-hold-request.test.tsx` |
| SSH forced command | allowlist `release|resume|status`; everything else rejected + audited; actor captured | `cloud/tests/test_ib_hold_ssh.py` |
| Release orchestration | hold written before stop even with deploy lock held; falls back to shim `compose-down` then `kill`; converges within budget; app unreachable does not fail release | `cloud/tests/test_ib_release.py` |
| Local paths | `docker_ib_gateway.sh` / local start refuse when broker hold is held or unknown | `scripts/tests/test_local_ib_hold_guard.py` |
| Live drill (manual, off-hours) | release -> container gone + no IBKR conntrack -> operator web login stays up 10 min -> resume -> one push -> authenticated -> pool reconnects | runbook checklist |

### Phases
| Phase | Scope | Effort |
|---|---|---|
| 0 | Operator: request IBKR secondary user for the Gateway (Option B); decommission mini `local.ibc-gateway` + `~/ibc/config.secure.ini`; confirm broker SSH exposure | 0.5 day hands-on + IBKR lead time |
| 1 | Hold module + shim/helper/unit enforcement + watchdog short-circuit + `radon ib release|resume|status` on broker + audit + Pushover | 2 days |
| 2 | Phone path: forced-command SSH key, iOS Shortcuts, Pushover link, IBC "existing session" log detector | 1 day |
| 3 | App mirror: remote daemon `hold`/`unhold` + 423s, `/health` field, relay 423 handling, watchdog grouping, admin Release/Resume buttons, trading-halt coupling | 1.5 days |
| 4 | Local-mode guards (laptop/mini) + live drill + docs (`docs/ib-gateway-recovery.md`, `scripts/api/CLAUDE.md`) | 0.5-1 day |
| 5 | After B is live: point Gateway at the new user in `/etc/radon/env` (root, manual broker change), keep `primary`, re-drill | 0.5 day |

Broker changes are manual root SSH (CI deploys only the app host); each phase that touches the broker needs an explicit install step and a post-install `radon ib status` check.
