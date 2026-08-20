# Grok page responder + live-deploy page

Operator loop for iPhone P1 service pages. Full playbook:
[`incident-runbook.md`](incident-runbook.md). Brand voice: no hype.

Primary host is Hetzner: `radon-grok-page-responder.timer` against
`/home/radon/radon-page-responder`. Laptop launchd is off. Do not run
the fixer against `/home/radon/radon`. Cloud options that were rejected
(Cursor Cloud Agents, weekend clone) are in
[`show-me-grok-cloud.html`](archive/show-me/show-me-grok-cloud.html).

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
VPS timer (30s after last cycle)
  ff-only origin/main if the clone is clean
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

## Install (VPS)

As root:

```bash
bash cloud/scripts/setup-grok-page-responder.sh
sudo -u radon -H /home/radon/.local/bin/grok login --device-auth
# approve the code on your phone
bash cloud/scripts/bootstrap-control-plane.sh   # installs the unit
systemctl enable --now radon-grok-page-responder.timer
```

Stripped env: `/home/radon/radon-page-responder.env` (Turso + Pushover
only). Auth: `/home/radon/.grok/auth.json` via device-code.

Setup drops a `.radon-page-responder` marker in the clone. It is gitignored on
purpose: `sync_remote_clone` fast-forwards only a clean tree, so an untracked
marker reads as dirty work and pins the clone to whatever grok last committed.
A responder running code older than `main` is the failure this prevents.

Claude analyze-only remains laptop-only: `com.radon.incident-responder`.
It never pushes.

## Own health (`grok-page-responder`)

The poller heartbeats itself, because a stalled auto-fixer is silent by
nature and its silence used to look exactly like health.

| Cycle | Row |
|---|---|
| Completed (including `pending: 0`) | `ok` |
| Kill switch off | `paused` |
| Skipped on a live lock | nothing written |
| Raised on Turso / git | nothing written |

Only a completed cycle heartbeats, so a wedged poller goes stale instead of
keeping its own row fresh forever. That is why the window is 90m in
`web/lib/serviceHealthWindows.ts` and `scripts/watchdog/services.py`: it has
to absorb one full grok run (`GROK_TIMEOUT_SECS`, 1h) of legitimate skipping.
The row is this writer's health, never the ticket verdict — a stand_down is a
healthy cycle.

## Verify

```bash
systemctl is-active radon-grok-page-responder.timer
journalctl -u radon-grok-page-responder -n 20 --no-pager
```

## Kill switches

**Unset means OFF** (REL-030 / R-055). Each switch is an explicit opt-in.
A missing or renamed `EnvironmentFile` is indistinguishable here from a
deliberate stand-down, and an agent that runs `grok --always-approve` and can
`git push origin main` into the production auto-deploy must read that
ambiguity as "stop". Before this the three flags all defaulted on, so a broken
env file yielded maximum autonomy.

| Env | When `1` | When unset or `0` |
|---|---|---|
| `GROK_PAGE_RESPONDER` | Claim and launch | Do not claim or launch |
| `GROK_PAGE_AUTOSHIP` | Edit, test, commit | Diagnose only. No edits or commits |
| `GROK_PAGE_AUTOPUSH` | Push after a green suite | Commit locally. Do not push |

`GROK_BIN` overrides the `grok` executable.

## Global daily action cap

The per-ticket bounds (3 attempts; one ticket per service/severity/kind/hour)
do not bound a weekend of hourly P1s — that is ~24 independent
autoship-and-push runs, each of which can deploy.
`GROK_PAGE_MAX_ACTIONS_PER_DAY` (default 6) caps grok invocations per UTC day,
counted as tickets claimed since 00:00Z (`watchdog.pages.actions_since`). At
the cap the cycle heartbeats `paused` and exits 0. An unreadable ledger counts
as over-cap, so a Turso miss stands the responder down rather than freeing it.

## Filesystem sandbox

The unit's stripped `EnvironmentFile` is pointless if the agent can read the
real secrets off disk, so `/home/radon/radon-cloud` (which holds the 0600
`.env` with IB Flex, Clerk, UW and archive credentials) is in
`InaccessiblePaths`, `/home/radon` is `ReadOnlyPaths`, and only the dedicated
clone is writable. `ProtectHome=tmpfs` is deliberately NOT used: it would also
hide the clone and the venv the unit executes from.

Page text is untrusted third-party/exception content. It reaches the model
only inside `<untrusted-excerpt>` delimiters, and `build_prompt` re-sanitizes
a row that does not already carry them rather than trusting the writer.

## Ticket states (`watchdog_pages`, migration 0048)

`pending` → `claimed` → `done`. Three failed Grok runs → `skipped`.
Stale claims older than 2h are reclaimable. Overlapping cycles skip on
`.responder.lock`.

The lock holds only while its PID is alive. A cycle killed before its
`finally` leaves the file behind, and the next poll steals it once
`os.kill(pid, 0)` reports the holder gone. Content that will not parse as a
PID is never assumed dead: those fall back to the TTL, `GROK_TIMEOUT_SECS`
plus a 5-minute teardown margin, since no cycle may outlive the grok timeout.
A wider TTL blackholes every queued page for its whole span — on 2026-08-14 a
cycle died at 20:14 UTC and the old 3h TTL stranded a `radon-skew2d.service`
P1 in `pending` with the poller printing `previous grok page cycle still
running` every 30 seconds.

## Code

| Path | Role |
|---|---|
| `scripts/watchdog/pages.py` | Sanitize, enqueue, claim, complete |
| `scripts/watchdog/notify.py` | After P1 2xx |
| `scripts/watchdog/grouping.py` | After grouped IB P1 2xx (creds required) |
| `scripts/grok_page_responder.py` | Poller |
| `cloud/services/radon-grok-page-responder.*` | VPS timer |
| `cloud/scripts/setup-grok-page-responder.sh` | Clone + stripped env + grok CLI |
| `scripts/deploy_notify.py` | Live-gate Pushover |
| `cloud/scripts/deploy.sh` | `notify_release_live` after green marker |

A quiet healthy cycle prints `{"pending": 0}`.
