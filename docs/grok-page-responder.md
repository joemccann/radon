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

As root, from a root-only stage of the provision store (the script refuses,
exit 77, to run from any tree radon can write, such as the deploy checkout):

```bash
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
STORE=/opt/radon-provision/radon.git   # root-only; deploys keep it current
SHA="$(git ls-remote https://github.com/joemccann/radon.git refs/heads/main | cut -f1)"
git --git-dir="$STORE" cat-file -e "${SHA}^{commit}"   # fails until that deploy ran
STAGE="$(mktemp -d /opt/radon-provision/grok-setup.XXXXXX)"
git --git-dir="$STORE" -c tar.umask=022 archive "$SHA" cloud | tar -x -C "$STAGE"
bash "$STAGE/cloud/scripts/setup-grok-page-responder.sh"
rm -rf "$STAGE"
sudo -u radon -H /home/radon/.local/bin/grok login --device-auth
# approve the code on your phone
/usr/local/sbin/radon-deploy-root sync-control-plane   # installs the unit
systemctl enable --now radon-grok-page-responder.timer
```

Stripped env: `/home/radon/radon-page-responder.env` (Turso + Pushover
only). Auth: `/home/radon/.grok/auth.json` via device-code.

Least-privilege Turso: the responder touches only `watchdog_pages` and its
`service_health` heartbeat (whose triggers append to `service_health_events`;
without that grant the heartbeat logs `not authorized`). Mint a scoped token
and put it in the production env as `GROK_RESPONDER_TURSO_AUTH_TOKEN`; setup
writes it as the responder's `TURSO_AUTH_TOKEN`. It is required: when unset,
setup exits and leaves the current file alone. The full-access production
token is never used. Turso's insert action is `data_add`; the CLI accepts
`data_insert` but the server rejects that token.

```bash
turso db tokens create radon \
  -p watchdog_pages:data_read,data_update \
  -p service_health:data_read,data_add,data_update \
  -p service_health_events:data_add
```

Rerunning setup rebuilds that file (`cloud/scripts/grok_responder_env.py`,
mode 600, owner radon). Secrets come only from the production env; an old
value in the file is never kept. The operator flags
`GROK_PAGE_RESPONDER`, `GROK_PAGE_AUTOSHIP`, `GROK_PAGE_AUTOPUSH` and
`GROK_PAGE_MAX_ACTIONS_PER_DAY` are carried over from the current file, so a
rerun no longer turns the responder off (`"skipped": "disabled"`).
`GROK_PAGE_NO_DOTENV`, `GROK_PAGE_SYNC_REMOTE` and `GROK_BIN` are always
reset. Contract: `cloud/tests/test_grok_responder_env.py`.

Setup drops a `.radon-page-responder` marker in the clone. It is gitignored on
purpose: `sync_remote_clone` fast-forwards only a clean tree, so an untracked
marker reads as dirty work and pins the clone to whatever grok last committed.
A responder running code older than `main` is the failure this prevents.
For the same reason the sync checks out `main` before fast-forwarding: a run
leaves the clone on its `fix/*` branch (kept for the pickup), and a
fast-forward of that branch fails every cycle (`"sync": "ff-failed"`).

Claude analyze-only remains laptop-only: `com.radon.incident-responder`.
It never pushes.

## Own health (`grok-page-responder`)

The poller heartbeats itself, because a stalled auto-fixer is silent by
nature and its silence used to look exactly like health.

| Cycle | Row |
|---|---|
| Completed (including `pending: 0`) | `ok` |
| Kill switch off | `paused` |
| Runtime refused (`GROK_BIN` missing or failing, no trusted last-known-good) | `error` with the reason (exit 0, `skipped: grok_runtime`, no fallback pushover; the watchdog error bucket pages once) |
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
its clone, and stops. The host's own GitHub SSH key (`~/.ssh`) and every
other home credential store (`~/.claude`, `~/.codex`, `~/.gnupg`,
`~/.config/gh`, `~/.git-credentials`) are `InaccessiblePaths` in the
responder and upgrade units, and the clone syncs `main` over public HTTPS,
so the agent's sandbox holds no GitHub credential.

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
  (default 20);
- anything at all unless `GROK_PAGE_AUTOPUSH` is truthy in the pickup job's
  own environment (the plist ships `0`). Off, a fire returns
  `{"action": "disabled"}` before touching either remote;
- a branch whose commit messages, added lines or paths in any commit
  (including merge resolutions), or PR title/body carry a private identifier
  (`scripts/ir_push_gate.py`): IB account ids (`U`/`DU`/`F` + 6-8 digits), numeric Flex exec ids (10+
  digits), dotted-hex IB exec ids, or a specific credential shape from
  `credential_redaction`. The branch stays local, the reason (kind and
  location, never the value) is logged and sent to Pushover, and nothing is
  redacted in place. Removing an identifier in a later commit does not
  make the earlier history safe to publish;
- running at all from a clone that is behind `origin/main` (stale pickup
  code).

REL-296 / R-715: the gate scans each unpublished commit, including merge
parents, so adding and then deleting an identifier still refuses publication.
Empty and binary files, filenames and branch names are included; refusal
messages replace private filenames with a redacted location.

2026-09-30: a pickup install from before the refresh step (#759) ran a clone
frozen before #773. It ignored `GROK_PAGE_AUTOPUSH`, opened
`IR: grok incident fix on <branch>` placeholder PRs, and published one
branch whose diff and messages carried private account and exec ids.
After changing the plist, reinstall it (the `sed` below) and
`launchctl unload`/`load` it.

A pushed branch remains pending until pickup confirms its PR URL. Later
runs reconcile the PR without pushing the branch again, reuse an open PR,
and retain a closed or merged PR's terminal disposition. If the source and
origin heads differ, pickup refuses the branch instead of overwriting it.
These checks retain the `.github/` refusal on repeated pickup runs.

Pickup reads `PUSHOVER_USER`, `PUSHOVER_TOKEN`, `TURSO_DB_URL` and
`TURSO_AUTH_TOKEN`, and nothing else, from `~/radon-weekend/.env` (the file
beside the pickup clone, also used by the plist's launch-failure page;
override with `--env-file`). The process environment wins. The file must be
a regular file owned by the operator with no group or other access, or it
is ignored. Without it, refusal alerts and the `watchdog_pages` lookup are
skipped silently.

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
| `GROK_PAGE_AUTOPUSH` | Push `fix/*` and ensure an open PR | Commit locally. Nothing pushes: the responder, `ir_ensure_pr` (module and CLI) and the Mac mini pickup all refuse |

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

`ir_ensure_pr.ensure_pr` refuses to create or edit a PR whose body lacks
these sections, and its CLI builds the body from the head commit (there is
no placeholder default). A missing, empty, TODO, branch-name, or
`grok incident fix on` section is refused: pickup logs, sends a normal-priority Pushover, exits non-zero for
that branch, and leaves it for the next cycle. The responder's AUTOPUSH
path uses the same validator. Pickup also folds in the `watchdog_pages`
row (page id, severity, first-seen, result) when Turso is reachable, and
adds CI run URLs on a later cycle once `gh run list` has them.

## Grok model and CLI (track latest)

Default is the newest stable CLI and the live default model. The responder
resolves the model id at run time from `grok models` (or the promoted
last-known-good) and always passes it with `-m`. Every log line,
`watchdog_pages.result`, commit body, and PR `How it was verified` records
`Ran <model> on CLI <version>`. `--no-auto-update` stays on at incident
time so the CLI never upgrades itself mid-run.

Last-known-good is a machine-written state file owned by the daily
upgrader: `/var/lib/radon/grok_lkg.json` (`cli_version`, `binary_path`,
`model`, `promoted_at`, `smoke_result`). There is no checked-in pin.

If the current model or CLI errors at incident time (non-zero exit,
model-unavailable, unparseable output with no `RESULT:` line), the
responder retries once on last-known-good and alerts. A valid
`RESULT: failed` line is not a retry.

A shared flock (`/var/lib/radon/grok-runtime/grok-runtime.lock`) serializes the
responder against a live promotion.

### Daily upgrade timer (enabled)

`radon-grok-upgrade.{service,timer}` is installed and enabled. Daily
07:40 UTC it runs `scripts/grok_upgrade.py`: copy the resolved CLI into
`<candidate>/downloads/grok-linux-x86_64` under the configured `--scratch`
path (`/var/lib/radon/grok-upgrade` in the unit; `data/cache/grok_upgrade`
when omitted) and point `<candidate>/bin/grok` at it with the installer
symlink `../downloads/grok-linux-x86_64`. `grok update` on CLI 1.0.44 and
newer readlinks that path to capture rollback; a regular file there exits
22 before smoke. Run that private symlink's updater with isolated `HOME`
and `GROK_HOME`. Probe its version,
resolve the newest default model, then smoke a canned dry-run that must return `RESULT:` plus a Part 1
validator-passing body. The smoke runs with `--output-format json`, so it
validates the reply in the JSON `text` field (the same extraction
`parse_grok_result` uses), from its first `## What broke` on: grok joins the
messages of each turn with no separator, which can glue a pre-tool preamble
to the first heading. The live executable is never the updater target
(REL-292 / R-711).

- Pass: prepare replacement links, atomically switch the canonical live symlink (`--live-bin`) and the explicit `--alias-bin` (`~/.grok/bin/grok` in the unit), and write LKG with the immutable candidate path. The upgrader never derives a path from HOME and refuses a candidate outside `--scratch`. Keep the promoted directory; a later attempt gets a new directory. No PR.
- Fail: preserve the live/LKG executable and alert via Pushover / watchdog; remove an unpromoted candidate. Health diagnostics use the writer's structured `error.message` field (REL-293 / R-712).
- Incident lock held: skip the promote and retry next fire.

```bash
systemctl status radon-grok-upgrade.timer
journalctl -u radon-grok-upgrade.service -n 50
cat /var/lib/radon/grok_lkg.json
```

`cloud/scripts/setup-grok-page-responder.sh` installs the latest stable
CLI and seeds LKG from the live default.

## Binary recovery

**Symptom and prerequisites:** use this path when the configured Grok entry
is missing or resolves to a discarded test candidate. Recovery belongs to the
operator on the affected host, with the reviewed runtime/upgrader code and
access to the existing LKG record. Repair affects both CLI entry links and
incident response; it does not require restarting the trading stack.

- **Safe diagnosis:** inspect the entry links and the LKG `binary_path` without
  executing an unknown target. Validate the candidate as the unit user with
  `grok_runtime.lkg_binary_problem`, including the responder checkout in
  `extra_untrusted`. A JSON record alone is not proof of a usable executable.
- **Stop conditions and repair:** stop if the target fails that check or its
  promotion provenance cannot be established. Do not relink while an incident
  or upgrade owns the shared runtime lock. Any operator relink must hold
  `grok_runtime.exclusive_lock` on the configured lock and use the explicit
  live/alias paths from the installed upgrade unit. Preserve the prior links
  and candidate directories. The upgrader needs a working source binary
  before it can check or install a candidate; restarting a dangling entry
  cannot repair it.
- **Verify:** inspect the resulting link targets, then verify the trusted
  executable's version and the responder journal/health result. For an
  upgrade, require its smoke and promotion result; a scheduled fire or exit
  zero alone can mean `current` or `locked`. Read the LKG record after a
  successful promotion, rather than assuming a particular version or file state.
- **Rollback and escalation:** restore only a previously verified executable
  under the same lock, retaining its candidate directory. If none is available,
  leave automatic response disabled and escalate CLI installation/authentication
  to the operator using [Install (VPS)](#install-vps). Do not substitute a temp
  binary or delete a candidate still referenced by either entry link or LKG.

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
  `radon-demo-mirror.service` is excluded from both the responder and polkit
  grants because its `ExecStartPre` applies database migrations (REL-108 /
  R-302). Its normal timer is unchanged; manual recovery uses the operator's
  reviewed service-control path.
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
`InaccessiblePaths`, as are `/var/lib/radon/flex-secrets` and
`/var/lib/radon/rh-mcp`. `/home/radon` and `/var/lib/radon` are
`ReadOnlyPaths`. The unit writes only the dedicated clone, `~/.grok` (session
state) and `/var/lib/radon/grok-runtime` (the shared flock). `~/.grok/bin`,
`~/.grok/hooks` and `~/.grok/downloads` stay read-only inside `~/.grok`,
because `radon-subscription-tokens` runs `grok` and `agy` from `~/.grok/bin`
and `~/.local/bin` with the production env. The upgrader installs candidates
under `/var/lib/radon/grok-upgrade`, which the responder cannot write, so the
promoted live symlink never points into a responder-writable path.
`ProtectHome=tmpfs` is deliberately NOT used: it would also hide the clone and
the venv the unit executes from.

The stripped env file itself is also in `InaccessiblePaths` (systemd reads it
before building the namespace), and every grok child process runs with an
allowlisted environment (`grok_runtime.grok_child_env`: PATH, HOME, locale,
proxy and CA vars). Turso and Pushover credentials stay with the Python
parent, which needs them for the ledger, heartbeats and alerts.

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
| `scripts/grok_runtime.py` | Model resolve, LKG IO, lock, fallback |
| `scripts/grok_upgrade.py` | Daily smoke + auto-promote |
| `/var/lib/radon/grok_lkg.json` | Machine-written last-known-good |
| `cloud/services/radon-grok-page-responder.*` | VPS timer |
| `cloud/services/radon-grok-upgrade.*` | Daily track-latest, installed enabled |
| `cloud/scripts/setup-grok-page-responder.sh` | Clone + stripped env + latest grok CLI |
| `scripts/deploy_notify.py` | Live-gate Pushover |
| `cloud/scripts/deploy.sh` | `notify_release_live` after green marker |

A quiet healthy cycle prints `{"pending": 0}`.
