# Grok page responder + live-deploy page

Operator loop for iPhone P1 service pages. Full playbook:
[`incident-runbook.md`](incident-runbook.md). Brand voice: no hype.

The Mac must be awake. This is not a Hetzner systemd unit. Cloud options
(dedicated VPS worktree vs Cursor Cloud Agents) are in
[`show-me-grok-cloud.html`](show-me-grok-cloud.html). Do not run the
fixer against `/home/radon/radon`.

## What you get on the phone

| Title | When | Priority | Starts Grok? |
|---|---|---|---|
| `radon watchdog: <service>` | Watchdog delivered a P1 | Emergency (2) | Yes |
| `radon grok: <service>` | Grok finished the ticket | Normal (0) | No |
| `radon deploy live` | Release passed the live gate | Normal (0) | No |

Never send the last two as P1. That would enqueue another Grok ticket.

## Path

```
watchdog P1 2xx
  ├─ Pushover emergency          → iPhone
  └─ INSERT watchdog_pages       → Turso (one row per service/kind/UTC hour)
laptop launchd 30s
  claim pending
  grok --prompt-file --always-approve
    stand_down | ops_only | code_fix
  normal follow-up               → iPhone
code_fix + AUTOPUSH
  git push origin main
  CI test gate
  VPS deploy.sh live gate
  radon deploy live              → iPhone
```

Stand down (no ship): `ib-gateway-grouped`, IB 2FA, IB unreachable, Turso
platform outage, CI deploy already in flight, expected off-hours lag,
anonymous 401/403, unknown probes, ops-only (secret/host/restart).

## Install (laptop)

```bash
bash scripts/setup_grok_page_responder.sh
```

Job: `com.radon.grok-page-responder`. Logs:
`/tmp/radon-grok-page-responder.log` and `.err`.

Claude analyze-only remains separate: `com.radon.incident-responder`
(`scripts/incident_responder.py`, 10 min). It never pushes.

## Kill switches

Unset means on.

| Env | When `0` |
|---|---|
| `GROK_PAGE_RESPONDER` | Do not claim or launch |
| `GROK_PAGE_AUTOSHIP` | Diagnose only. No edits or commits |
| `GROK_PAGE_AUTOPUSH` | Commit locally. Do not push |

`GROK_BIN` overrides the `grok` executable.

## Ticket states (`watchdog_pages`, migration 0048)

`pending` → `claimed` → `done`. Three failed Grok runs → `skipped`.
Stale claims older than 2h are reclaimable. Overlapping cycles skip on
`.responder.lock`.

## Code

| Path | Role |
|---|---|
| `scripts/watchdog/pages.py` | Sanitize, enqueue, claim, complete |
| `scripts/watchdog/notify.py` | After P1 2xx |
| `scripts/watchdog/grouping.py` | After grouped IB P1 2xx (creds required) |
| `scripts/grok_page_responder.py` | Laptop poller |
| `scripts/deploy_notify.py` | Live-gate Pushover |
| `cloud/scripts/deploy.sh` | `notify_release_live` after green marker |
| `config/com.radon.grok-page-responder.plist` | launchd |

## Verify

```bash
launchctl print "gui/$(id -u)/com.radon.grok-page-responder" | grep -E 'state|runs|last exit'
tail -n 5 /tmp/radon-grok-page-responder.log
```

A quiet healthy cycle prints `{"pending": 0}`. `state = not running`
between 30s ticks is idle, not down.
