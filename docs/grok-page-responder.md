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
  git push -u origin fix/<slug>
  python3.13 scripts/ir_ensure_pr.py   # open PR against main; never merge
  Joe / Mac Mini / loops merge after CI green
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
/usr/local/sbin/radon-deploy-root sync-control-plane   # installs the unit
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

## Open-PR path: the Mac mini picks the branch up

**The VPS holds no GitHub credential.** Pushing a branch and merging a pull
request need the same GitHub permission, so any token on the host that runs
`grok --always-approve` over untrusted page text could merge to `main` and
deploy — the prompt rule and `ir_ensure_pr`'s merge refusal are guards the
agent's own shell can walk around with `curl`. The responder therefore runs
with `GROK_PAGE_AUTOPUSH=0`: it edits, tests and commits to `fix/<slug>` in
its clone, and stops.

`scripts/grok_fix_pickup.py` on the Mac mini (launchd
`com.radon.grok-fix-pickup`, every 15 min) first refreshes its clone to
`origin/main`, then fetches those branches over the
existing ssh access, pushes them to GitHub and calls `ir_ensure_pr`. It never
merges; Joe merges after CI is green. Local `fix/*` branches in that clone
survive the reset. Branch content is still untrusted, so
pickup refuses:

- refs outside `fix/<slug>` (no refspec, option or path tricks);
- a diff touching `.github/` — a PR-triggered workflow runs from the PR head,
  which would execute attacker-authored CI in this repository;
- a branch that does not descend from `origin/main`, or exceeds the commit cap
  (default 20).

A pushed branch remains pending until pickup confirms its PR URL. Later
runs reconcile the PR without pushing the branch again, reuse an open PR,
and retain a closed or merged PR's terminal disposition. If the source and
origin heads differ, pickup refuses the branch instead of overwriting it.
These checks retain the `.github/` refusal on repeated pickup runs.

Fetching from a hostile repository is a supported git operation, and nothing
in pickup executes code out of the fetched tree. Regressions:
`scripts/tests/test_grok_fix_pickup.py`.

```bash
# Mac mini, once: dedicated clone + job.
# Each fire fetches origin and hard-resets main to origin/main before
# pickup, so the job cannot drift. Local fix/* branches survive.
git clone git@github.com:joemccann/radon.git ~/radon-weekend/radon-grok-pickup
sed -e "s|__PICKUP_REPO__|$HOME/radon-weekend/radon-grok-pickup|g" \
    -e "s|__HOME__|$HOME|g" \
  config/com.radon.grok-fix-pickup.plist \
  > ~/Library/LaunchAgents/com.radon.grok-fix-pickup.plist
launchctl load ~/Library/LaunchAgents/com.radon.grok-fix-pickup.plist
```

## Kill switches

**Unset means OFF** (REL-030 / R-055). Each switch is an explicit opt-in.
A missing or renamed `EnvironmentFile` is indistinguishable here from a
deliberate stand-down, and an agent that runs `grok --always-approve` and can
push a `fix/*` branch must read that ambiguity as "stop". Before this the
three flags all defaulted on, so a broken env file yielded maximum autonomy.

| Env | When `1` | When unset or `0` |
|---|---|---|
| `GROK_PAGE_RESPONDER` | Claim and launch | Do not claim or launch |
| `GROK_PAGE_AUTOSHIP` | Edit, test, commit | Diagnose only. No edits or commits |
| `GROK_PAGE_AUTOPUSH` | Push `fix/*` and ensure an open PR | Commit locally. Do not push |

`GROK_BIN` overrides the `grok` executable.

## IR PR description

Pickup no longer opens a PR whose issue and fix lines are
`grok incident fix on fix/<slug>`. Grok's commit body is the source. It
must contain these sections, each with real content:

- What broke (symptom, failing job or alert, page id, first-seen time,
  run or log links, error excerpt)
- Root cause
- What changed (each file)
- How it was verified
- Risk and rollback
- Still open

A missing, empty, TODO, branch-name, or `grok incident fix on` section is
refused: pickup logs, sends a normal-priority Pushover, exits non-zero for
that branch, and leaves it for the next cycle. The responder's AUTOPUSH
path uses the same validator. Pickup also folds in the `watchdog_pages`
row (page id, severity, first-seen, result) when Turso is reachable, and
adds CI run URLs on a later cycle once `gh run list` has them.

## Grok model and CLI pin

`config/grok_pin.json` is the only allowed model/CLI pair. The responder
passes `-m` and `--reasoning-effort` from that file and checks
`grok --version` plus `grok models` before each run. A CLI mismatch or a
missing pinned model falls back to `last_known_good` and sends a warning
Pushover. It never runs on the CLI's unpinned default. `--no-auto-update`
stays on.

`cloud/scripts/setup-grok-page-responder.sh` installs
`grok update --version <pin.cli_version> --no-auto-update`.

### Hand bump

1. Edit `config/grok_pin.json`: set `model` / `cli_version` / `reasoning_effort`.
   Move today's values into `last_known_good`.
2. On the VPS: `sudo -u radon -H grok update --version <new> --no-auto-update`.
3. Confirm `grok --version` and `grok models` match the pin.
4. Open a PR with the six IR sections. Joe merges.

### Rollback to last-known-good

1. Copy `last_known_good` over the live `model` / `cli_version` / `reasoning_effort`
   fields in `config/grok_pin.json`.
2. On the VPS: `sudo -u radon -H grok update --version <lkg> --no-auto-update`.
3. Do not enable auto-update.

### Weekly bump timer (disabled)

`radon-grok-pin-bump.{service,timer}` is inventoried and stays disabled.
When enabled it runs `scripts/grok_pin_bump.py`: `grok update --check --json`
and `grok models`, side-installs a candidate under a scratch `GROK_HOME`,
smokes a canned dry-run that must return `RESULT:` plus a validator-passing
body, then commits `fix/grok-pin-*` in the responder clone for pickup.
A failed smoke alerts and leaves the pin alone. Enable only after review:

```bash
systemctl enable --now radon-grok-pin-bump.timer
```

## Push guard

The prompt tells grok never to push `main` or merge, but prompt text is not
a control: page excerpts are untrusted input. Every push-capable cycle
(`GROK_PAGE_AUTOPUSH=1`) reinstalls a `pre-push` hook in the clone that
refuses any ref outside `refs/heads/fix/*`, including branch deletes and
tags, before grok is launched. If the hook cannot be installed the cycle
stands down (exit 1) instead of running push-capable. `ir_ensure_pr.py`
refuses any merge-shaped `gh` invocation by token scan, and the page-derived
summary is flattened to one control-free line before it enters PR metadata.
Regressions: `scripts/tests/test_grok_push_guard.py`.

## Global daily action cap

The per-ticket bounds (3 attempts; one ticket per service/severity/kind/hour)
do not bound a weekend of hourly P1s — that is ~24 independent
autoship-and-push runs, each of which can deploy.
`GROK_PAGE_MAX_ACTIONS_PER_DAY` (default 6) caps grok invocations per UTC day,
counted as tickets claimed since 00:00Z (`watchdog.pages.actions_since`). At
the cap the cycle heartbeats `paused` and exits 0. An unreadable ledger counts
as over-cap, so a Turso miss stands the responder down rather than freeing it.

## Oneshot re-run (signal-killed, or exit-code with a fix deployed since)

A deploy stop-clean or `radon restart` teardown can SIGTERM a timer-driven
EOD oneshot mid-run. "Recovers on next timer" is the wrong verdict when that
slot is most of a day away: radon-vol-cone was killed at 20:4x UTC on
2026-08-20, the next slot was ~22h out, and the day's EOD data stayed missing
until a human ran reset-failed + start. Before launching grok on a
`kind=unit` page, the responder pre-triages deterministically
(`attempt_oneshot_rerun`) and re-runs the unit itself when ALL of these hold:

- the unit is in `RERUNNABLE_ONESHOT_UNITS` (the timer-owned data-fetch
  oneshots: the daily and multi-daily scans such as vol-cone, skew, bpi,
  leap, garch, cor, credit-spread, the equibles fetches; intraday timers are
  deliberately absent because their next slot is minutes away). The
  responder has no general right to start `radon-*` units — polkit grants
  the radon user exactly this list, verbs `reset-failed` and `start` only
  (`cloud/config/polkit/50-radon-services.rules`; the two lists are pinned
  to each other by `test_allowlist_matches_the_polkit_grant`, and every
  member must be a `Type=oneshot` with a `.timer` partner per
  `test_allowlist_covers_the_daily_scans_and_only_timer_owned_oneshots`).
- `GROK_PAGE_AUTOSHIP=1` — the restart is an auto-fix action, so it takes the
  same explicit opt-in as shipping a code fix. Unset stands down.
- no deploy transition journal (`/home/radon/.radon-deploy-transition.json`):
  never restart units mid-deploy.
- systemd itself reports `ActiveState=failed` (the page excerpt is
  untrusted text and never triggers the action on its own) with either
  `Result=signal`, or `Result=exit-code`/`Result=timeout` AND the green
  deploy marker (`/home/radon/.radon-last-green-deploy`) is newer than the
  unit's `InactiveEnterTimestamp` — a fix has shipped since the failure.
  radon-leap 2026-08-20: exit 1 at 14:02Z, fix deployed 15:05Z, next slot
  the following day; radon-divyield 2026-08-24: `Result=timeout` at 23:57Z,
  next slot ~22h. Both sat `failed` re-paging hourly until a human ran
  reset-failed + start. Without a newer deploy nothing has changed, a rerun
  would only fail again, so it falls through to grok.
- the unit's timer's next elapse is more than 12h away
  (`RERUN_TIMER_HORIZON_SECS`). Closer than that, waiting for the timer
  remains correct and grok triages the page as before.
- the unit has not already spent its re-run for the UTC day
  (`MAX_RERUNS_PER_UNIT_PER_DAY`, counted by `watchdog.pages.reruns_since`
  over completed `restarted_unit:` tickets for that service). R-115: the
  green-deploy marker is written by EVERY green deploy with no relation to
  the failed unit, so a unit failing for an environmental reason (UW quota
  exhausted, provider 5xx, gateway down) otherwise satisfies "a fix has
  deployed since" once per unrelated merge to main, indefinitely — four of
  the allowlisted units are UW consumers, so each attempt spends quota it
  does not have. Fails CLOSED: a count that cannot be read stands the
  re-run down.

Action: `systemctl reset-failed <unit>` + `systemctl start --no-block
<unit>`, ticket completed as `restarted_unit`, normal follow-up push. Any
failed precondition or failed systemctl call falls through to the ordinary
grok path. The claim still counts against the daily action cap.

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
| `scripts/ir_pr_description.py` | IR PR section validator |
| `scripts/grok_pin.py` | Pin parse + fallback |
| `scripts/grok_pin_bump.py` | Weekly candidate smoke (disabled) |
| `config/grok_pin.json` | Model / CLI pin |
| `cloud/services/radon-grok-page-responder.*` | VPS timer |
| `cloud/services/radon-grok-pin-bump.*` | Weekly pin check, installed disabled |
| `cloud/scripts/setup-grok-page-responder.sh` | Clone + stripped env + pinned grok CLI |
| `scripts/deploy_notify.py` | Live-gate Pushover |
| `cloud/scripts/deploy.sh` | `notify_release_live` after green marker |

A quiet healthy cycle prints `{"pending": 0}`.
