# Grok page responder + live-deploy page

Operator loop for iPhone P1 service pages. Full playbook:
[`incident-runbook.md`](incident-runbook.md). Brand voice: no hype.

Primary host is Hetzner: `radon-grok-page-responder.timer` against
`/home/radon/radon-page-responder`. Laptop launchd is off. Do not run
the fixer against `/home/radon/radon`. Cloud options that were rejected
(Cursor Cloud Agents, weekend clone) are in
[`show-me-grok-cloud.html`](archive/show-me/show-me-grok-cloud.html).

## Isolated responder account migration

The optional responder domain uses `radon-responder`, its own clone, runtime
lock and cache, and the CI-managed clients under `/opt/radon-model-clients`.
It joins only the narrow `radon-model-connect` inference group, with no
production application or credential-vault group membership. Provider grants
and renewal belong to the model gateway. Both optional units retain
`ProtectProc=invisible`, `ProtectHome=yes` and a strict filesystem write set.
Mutable native upgrade timers are retired.

These units live under `cloud/services/responder-domain/`. A source deploy
does not install or enable them and does not interrupt the existing responder.
It does refresh the root-owned code tree below when that tree's installer is
in the trusted main tip. The explicit preparation below installs the units
disabled and records their root-owned inventory at
`/etc/radon/grok-responder/installed-units.sha256`.
The daily drift auditor then compares them, including drop-ins, against their
canonical nested sources. Rollback retains that inventory and those disabled
units, so their configuration remains observable.

### Prepare the optional account

After the tested maintenance commit has deployed, run as root from the same
trusted provision-store staging pattern used by the legacy installer:

```bash
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
STORE=/opt/radon-provision/radon.git
SHA="$(git ls-remote https://github.com/joemccann/radon.git refs/heads/main | cut -f1)"
git --git-dir="$STORE" cat-file -e "${SHA}^{commit}"
STAGE="$(mktemp -d /opt/radon-provision/responder-account.XXXXXX)"
git --git-dir="$STORE" -c tar.umask=022 archive "$SHA" cloud scripts | tar -x -C "$STAGE"
bash "$STAGE/cloud/scripts/setup-grok-responder-account.sh"
rm -rf "$STAGE"
```

Preparation verifies the immutable client manifest as the dedicated account;
no provider credential file is copied. It preserves the legacy clone and its branches. It derives a private
root-owned environment at `/etc/radon/grok-responder/env`, using the existing
scoped responder Turso token and Pushover inputs. It preserves bounded operator
controls, always forces `GROK_PAGE_AUTOPUSH=0`, and carries no GitHub token.
Set `GROK_PAGE_RESPONDER=1` there explicitly if the inherited switch was off.
Model readiness is checked through the private gateway socket.

Preparation requires the standard root-installed Python, Node and Bun
prerequisites and an already promoted model-client release. Dependency
installation runs only as the dedicated account. Preparation never downloads,
updates or logs into a provider CLI. The legacy upgrade service name remains
a read-only gateway status check; its timer stays disabled.

### Root-owned responder code (DS-2026-10-17-16)

The isolated unit executes
`/usr/bin/python3.13 -E -S /usr/local/lib/radon/grok-responder/scripts/grok_page_responder.py`.
That tree is root-owned and listed read-only. `/var/lib/radon-responder/repo`
stays writable because it is the clone the cycle fetches, edits, and pushes.
`RADON_GROK_REPO` points the cycle at that clone. The import closure is
stdlib-only, so the unit does not use the clone's `.venv`.

`setup-vps.sh` creates the tree from the provision store. Every
`radon-deploy-root install-units` refreshes it from the same store. A fresh
`setup-grok-responder-account.sh` publishes the unit that points at the tree.
An already-finalized host (the `ready` marker exists) takes the refresh verb
of that same script: it replaces the code and the installed unit files, and
it does not rebuild the clone, the venv, or the timers. Retry when
it reports the workload is active.

Until that refresh has been run on a host whose unit still executes
`/var/lib/radon-responder/repo`, the live service is unchanged. Do not treat
a green deploy as the cutover.

```bash
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
STORE=/opt/radon-provision/radon.git
SHA="$(git ls-remote https://github.com/joemccann/radon.git refs/heads/main | cut -f1)"
git --git-dir="$STORE" cat-file -e "${SHA}^{commit}"
STAGE="$(mktemp -d /opt/radon-provision/responder-code.XXXXXX)"
git --git-dir="$STORE" -c tar.umask=022 archive "$SHA" cloud scripts | tar -x -C "$STAGE"
bash "$STAGE/cloud/scripts/setup-grok-responder-account.sh"
rm -rf "$STAGE"
systemctl show radon-grok-responder.service -p ExecStart -p ReadWritePaths --no-pager
```

`ExecStart` must be the `/usr/local/lib/radon/grok-responder` interpreter line.
`ReadWritePaths` must still include the repo and must not include that code
tree. Contract: `cloud/tests/test_grok_responder_code.py`.

### Configure read-only branch pickup

The existing pickup default still addresses the legacy clone. Before finalizing,
process any outstanding legacy `fix/*` branches and change only the installed
Mac pickup job's source. Set its `EnvironmentVariables` entry to
`RADON_GROK_FIX_SOURCE=ssh://radon@radon-app/var/lib/radon-responder/repo`.
For an already installed launch agent:

```bash
pickup_plist="$HOME/Library/LaunchAgents/com.radon.grok-fix-pickup.plist"
/usr/libexec/PlistBuddy -c 'Set :EnvironmentVariables:RADON_GROK_FIX_SOURCE ssh://radon@radon-app/var/lib/radon-responder/repo' "$pickup_plist" || \
  /usr/libexec/PlistBuddy -c 'Add :EnvironmentVariables:RADON_GROK_FIX_SOURCE string ssh://radon@radon-app/var/lib/radon-responder/repo' "$pickup_plist"
launchctl bootout "gui/$(id -u)/com.radon.grok-fix-pickup"
launchctl bootstrap "gui/$(id -u)" "$pickup_plist"
GIT_SSH_COMMAND='ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ControlMaster=no -o ControlPath=none' \
  git ls-remote ssh://radon@radon-app/var/lib/radon-responder/repo HEAD
```

Preparation grants `radon` read access to the clone through a dedicated pickup
group, with group writes removed, and registers only the repository root and its
exact `.git` path as Git safe directories for the pickup account. SSH upload-pack
checks the `.git` path on servers that enforce ownership there; trust remains
limited to these two exact paths. The responder is not added to `radon` or any
privileged group. Model credentials remain outside the account; runtime state stays private.
A fresh SSH connection is needed to pick up the new group membership. After a
setup change deploys, rerun preparation from a trusted stage of that deployed
commit before the ordinary `ls-remote` check above. Existing pickup push-policy
and description gates remain unchanged.

### Finalize and verify

As root, explicitly acknowledge that Mac pickup is configured:

```bash
/usr/bin/python3.13 -I /usr/local/lib/radon/grok_responder_account.py finalize --pickup-configured
systemctl is-active radon-grok-responder.timer
systemctl is-enabled radon-grok-responder-upgrade.timer   # disabled
systemctl show radon-grok-responder.service -p User -p ProtectProc -p ProtectHome
systemctl is-enabled radon-grok-page-responder.timer
```

Finalization verifies the installed unit hashes and deployed action policy,
the immutable native-client receipt and gateway readiness, then checks pickup
readability and refuses unmerged legacy work. Only then does it disable the
legacy responder timer, publish the root-owned readiness marker and enable
`radon-grok-responder.timer`. An active legacy responder stops handover;
retry after that run finishes. An enable failure restores the previous timer.
The marker is outside the account's writable directories.

The responder keeps the existing `grok-page-responder` heartbeat. The optional
`grok-responder-upgrade` health row is retired. Manifest and gateway status
checks do not perform inference; a successful status check proves configured
routing, not a successful provider response.

### Rollback the account migration

```bash
/usr/bin/python3.13 -I /usr/local/lib/radon/grok_responder_account.py rollback
```

Rollback waits for inactive optional workloads, disables their timers,
withdraws readiness and enables the legacy responder timer. It never copies
authentication files or rewrites either clone. A failed legacy enable restores
the new domain's readiness and timers. The optional upgrade health row is
retired by one exact root-owned database operation; if telemetry retirement
fails after legacy restoration, rerun rollback to finish that operation.

Restore the Mac pickup source to
`ssh://radon@radon-app/home/radon/radon-page-responder` and reload its launch
agent using the same commands above. The dedicated account and disabled unit
inventory remain available for a later retry. Preparation refuses an active
readiness marker; use rollback before preparing a newer optional runtime.
Provider grants and CI-managed client promotion remain gateway-owned.

Contracts: `cloud/tests/test_responder_service_account.py`.

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
git --git-dir="$STORE" -c tar.umask=022 archive "$SHA" cloud scripts | tar -x -C "$STAGE"
bash "$STAGE/cloud/scripts/setup-grok-page-responder.sh"
rm -rf "$STAGE"
/usr/local/sbin/radon-deploy-root sync-control-plane   # installs the unit
systemctl enable --now radon-grok-page-responder.timer
```

Stripped env: `/home/radon/radon-page-responder.env` (Turso + Pushover
only, plus fixed managed-client and inference-socket settings). Provider
credentials remain in the gateway; local device-code login is not required.
The installed unit overrides the client path and socket at `ExecStart` so a
legacy environment file cannot redirect inference.

Least-privilege Turso: the responder touches only `watchdog_pages`, `watchdog_page_attempts` and its
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
  -p watchdog_page_attempts:data_read,data_add \
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

A recovered ledger transport blip heartbeats `ok` with
`last_error.class=ledger_transport` (the error bucket pages only on
`state=error`, so this does not self-page). Unrecovered blips skip the
ok heartbeat and print `{"event":"grok_page_ledger_transport",...}` plus
`grok page ledger non-fatal` so `journalctl -u radon-grok-page-responder`
can see them.

A zero-exit Grok run with a valid assistant `RESULT:` is complete even when
its diagnosis, JSON metadata or stderr mentions an unavailable model. A
runtime fallback must change the model, reasoning effort or executable;
symlinks, hard links and PATH aliases of the same binary do not qualify.
Before a genuine failure can launch a fallback, `grok_runtime_attempt`
records its page, model, CLI version, exit/timeout/invalid-result reason,
output length and a credential-redacted stderr tail. Agent stdout stays
out of the journal. The normal per-launch action budget still applies.
Generated `node_modules/` directories must stay gitignored: clone sync
refuses a dirty tree, including untracked dependency caches.

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

The subscription vault's runtime directory (`/run/radon-subscription-vault`)
is in both deny-lists too. The vault broker answers its socket with no
client authorization -- no peer-credential or keeper-identity check -- so the
socket's mode and the owner of its directory are the whole access control,
and that owner is the same application account these units run as. Without
the deny entry an agent session could read the sealed third-party
subscription credentials straight off the socket, which is exactly what the
rest of the deny-list exists to prevent. `radon-subscription-tokens.service`
is the vault's only intended client and is a separate unit, so it keeps its
access; `cloud/tests/test_grok_sandbox_contract.py` pins both halves of that
split.

`scripts/grok_fix_pickup.py` on the Mac mini (launchd
`com.radon.grok-fix-pickup`, every 15 min) first refreshes its clone to
`origin/main`, then fetches those branches over the
existing ssh access, pushes them to GitHub and calls `ir_ensure_pr`. It never
merges; Joe merges after CI is green. Local `fix/*` branches in that clone
survive the reset. Branch content is still untrusted, so
pickup refuses:

- refs outside `fix/<slug>` (no refspec, option or path tricks);
- a diff touching `.github/` — a PR-triggered workflow runs from the PR head,
  which would execute attacker-authored CI in this repository. The changed
  paths are read with `git diff -z --name-only` and split on NUL: without
  `-z`, git C-quotes a path carrying a non-ASCII byte, a quote or a newline,
  and `".github/workflows/é.yml"` does not start with `.github/`. It also
  passes `--no-renames`: a detected rename emits only the destination, so
  moving a workflow or CODEOWNERS out of `.github/` showed the permitted
  destination alone;
- a branch that does not descend from `origin/main`, or exceeds the commit cap
  (default 20);
- anything at all unless `GROK_PAGE_AUTOPUSH` is truthy in the pickup job's
  own environment (the plist ships `0`). Off, a fire returns
  `{"action": "disabled"}` before touching either remote;
- a branch whose commit messages, added lines or paths in any commit
  (including merge resolutions), or PR title/body carry a private identifier
  detected by [`ir_push_gate.py`](../scripts/ir_push_gate.py): account and
  execution identifiers, credential shapes or an opaque literal assigned to
  a credential-named key, and a verbatim credential value from the pickup process
  environment. Exact detectors live in the gate and
  [`credential_redaction.py`](../scripts/credential_redaction.py), not this runbook.
  The branch stays local, the reason (kind and
  location, never the value) is logged and sent to Pushover, and nothing is
  redacted in place. Removing an identifier in a later commit does not
  make the earlier history safe to publish;
- running at all from a clone that is behind `origin/main` (stale pickup
  code).

The run-identifier shapes the gate refuses live in
`scripts/credential_redaction.py` and are shared with the public-text
sanitizer, so the two cannot acquire rules independently. Add a new shape
there, not in the gate.

REL-296 / R-715: the gate scans each unpublished commit, including merge
parents, so adding and then deleting an identifier still refuses publication.
Empty and binary files, filenames and branch names are included; refusal
messages replace private filenames with a redacted location. The raw commit
object is read too: a header other than `tree`, `parent`, `author`,
`committer`, `encoding` or `gpgsig`/`gpgsig-sha256`, or a continuation line
after anything but a signature, refuses the branch, and
the free-text values of the allowed signature and encoding headers are scanned.

DS-2026-10-17-22: the gate and the pickup job read a branch an outside
contributor shapes, so both now run every `git` call through one bounded
runner that reads incrementally and kills the child past 32MB. Output over
that cap is a refusal naming the limit, not an exhausted process: a single
oversized blob, patch or commit message used to be buffered whole in memory.
The same bound covers the PR builder reading a complete commit message.

Pickup resolves each fetched branch to one commit sha right after the fetch.
Every check above, the PR text and the push (`<sha>:refs/heads/fix/<slug>`)
use that sha, so moving the shared `refs/remotes/grok-vps/*` ref after
validation cannot change what is published.

A refused branch stays on the VPS and is listed every cycle, but its Pushover
page (identifier or IR-body refusal) fires once per branch head. Pickup records
the paged head in `logs/grok-fix-pickup/refusal-pages.json` in its clone; a new
commit on the branch pages again, and a deleted branch is forgotten. To retire
a superseded refused fix, keep it under `refs/archive/<branch>` in
`/home/radon/radon-page-responder` and delete the branch there. The responder
prompt tells Grok to use invented ids (`demo`) instead of real broker ids.

**Symptom:** pickup refuses publication and leaves the incident fix local.
**Prerequisites:** access to the pickup clone and its refusal log; inspect no secret values.
**Blast radius:** refusal blocks the fix's push and PR; it does not deploy or alter production.
**Diagnosis:** use the logged kind and location to inspect the unpublished history and PR metadata. A clean tip does not prove clean history.
**Stop:** never enable publication to bypass a refusal or paste the matched value into an issue.
**Verification:** rerun the gate on the sanitized unpublished range and PR metadata; require no findings before pickup publishes it. Offline coverage is in [`test_ir_push_gate.py`](../scripts/tests/test_ir_push_gate.py).
**Rollback:** leave the branch local with pickup disabled while correcting it.
**Escalation:** if the identifier is already public, follow [`SECURITY.md`](../SECURITY.md); a later deletion does not remove published history.

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
`GROK_PICKUP_TURSO_AUTH_TOKEN`, and nothing else, from `~/radon-weekend/.env`
(the file beside the pickup clone, also used by the plist's launch-failure
page; override with `--env-file`). `GROK_PICKUP_TURSO_AUTH_TOKEN` is
exported to the process as `TURSO_AUTH_TOKEN`; a `TURSO_AUTH_TOKEN` line in
the file is never read. Pickup only reads `watchdog_pages`, so mint it as a
table-scoped read token and keep nothing else in that file:

```bash
# Mac mini, operator. Value goes straight into the file, never on screen.
f=~/radon-weekend/.env
t="$(turso db tokens create radon -p watchdog_pages:data_read)"
{ grep -E '^(PUSHOVER_USER|PUSHOVER_TOKEN|TURSO_DB_URL)=' "$f"; printf 'GROK_PICKUP_TURSO_AUTH_TOKEN=%s\n' "$t"; } \
  | (umask 077; cat > "$f.new") && mv "$f.new" "$f"; unset t
```

The process environment wins. The file must be a regular file owned by the
operator with no group or other access, or it is ignored. Without it,
refusal alerts and the `watchdog_pages` lookup are skipped silently.

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

- What broke. The first line is the PR title: one sentence naming the
  service and the failure. Page id, first-seen time, run or log links, and
  the error excerpt go on the following lines. `format_ir_pr_title` still
  strips a 32-hex page id, an ISO timestamp, an alert quotation, and any
  `journalctl` or "last successful line" tail, then clips on a word at 72
  characters. A short human incident slug may prefix the title (`IR <slug>:`).
  A page id never does. The id stays in the body.
- Root cause
- What changed (each file)
- How it was verified
- Risk and rollback
- Still open

The PR body renders those sections into the repository PR template
(`.github/pull_request_template.md`, enforced by the `PR body template` CI
check): Summary is What broke plus a `**Root cause:**` line, Test plan is
How it was verified, Operator Next Steps is Still open, and the template
Checklist follows. `ir_ensure_pr.ensure_pr` refuses to create or edit a PR
whose body is not that rendering (`ir_pr_description.validate_ir_pr_body`),
and its CLI builds the body from the head commit (there is no placeholder
default). A missing, empty, TODO, branch-name, or
`grok incident fix on` section is refused: pickup logs, sends a normal-priority Pushover, exits non-zero for
that branch, and leaves it for the next cycle. The responder's AUTOPUSH
path uses the same validator. Pickup also folds in the `watchdog_pages`
row (page id, severity, first-seen, result) when Turso is reachable, and
adds CI run URLs on a later cycle once `gh run list` has them.

## Grok model and CLI (CI managed)

The responder verifies `/opt/radon-model-clients/current/manifest.json`,
including protected ancestry, release selection, exact native versions and
file hashes. It selects supported Grok model metadata from the gateway's
cached inventory and runs the immutable release path through the private
native-protocol bridge. Every log line, `watchdog_pages.result`, commit body
and PR Test plan records `Ran <model> on CLI <version>`.

The bridge supplies temporary app-scoped authentication and preserves native
tool schemas. Provider credentials and renewal stay in the gateway. A model
missing from supported metadata or an untrusted client release fails closed;
a queue state or a status check is not proof of a completed model response.

### CI-managed upgrades

Native versions are pinned in `docker/app/vendor-clis/package.json` and its
lockfile. CI verifies payload integrity and actual native tool transport
without provider network access. The root deployment controller stages the
exact image's clients, verifies the manifest and promotes the release only
through the protected commit protocol. Consumer processes cannot update the
release tree. See [model-gateway.md](model-gateway.md) for promotion, rollback
and the separate inference/control sockets.

`radon-grok-upgrade.timer` and `radon-grok-responder-upgrade.timer` remain
disabled. Their service names provide a read-only gateway readiness check.
The old root-owned controller under `/usr/local/lib/radon/grok-upgrade` and
`cloud/scripts/install-grok-upgrade-controller.sh` are retained only for
rollback compatibility; setup does not invoke an updater or seed mutable LKG.
Its protected publication contract remains covered by
`cloud/tests/test_grok_upgrade_controller.py`.

## Binary recovery

Stop automatic response when the selected release is missing, writable,
hash-mismatched or lacks a trusted receipt. Do not execute or relink an unknown
binary. `grok_runtime.lkg_binary_problem` retains legacy validation; managed
releases must pass the stronger `managed_manifest` check.

Use the protected deployment controller to reinstall or rollback to a verified
immutable client release from the exact CI image. Hold the existing
`grok_runtime.exclusive_lock` when a manual legacy recovery changes an entry
referenced by an incident. Retain the prior release and receipt; never replace
a managed file in place or delete a release still selected by a running job.

Verify the protected ancestry, manifest hashes, native version receipt and
private gateway readiness, then verify an actual responder result separately.
For rollback, restore a previously verified release through the same root
commit protocol. If no trusted release is available, leave automatic response
disabled and escalate installation to the operator using
[Install (VPS)](#install-vps). No trading-stack restart is required.

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
counted from `watchdog_page_attempts` since 00:00Z
(`watchdog.pages.actions_since`). One row is reserved BEFORE every launch
(`record_action_attempt`), including the last-known-good fallback launch and
an `attempt_oneshot_rerun`, and is never cleared. The count deliberately does
NOT key on `watchdog_pages.claimed_at`: a failed attempt clears that column so
the ticket stays retryable, which silently returned the attempt's budget. At
the cap the cycle heartbeats `paused` and exits 0. An unreadable ledger counts
as over-cap, so a Turso miss stands the responder down rather than freeing it.
A reservation that cannot be written propagates and fails the cycle instead of
launching unbudgeted.

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
real secrets off disk. Live production secrets are `/etc/radon/env`.
`/home/radon/radon-cloud/.env` is retired. Both the retired checkout and
`/etc/radon/env` are in `InaccessiblePaths`, as are
`/var/lib/radon/flex-secrets` and `/var/lib/radon/rh-mcp`. `/home/radon` and `/var/lib/radon` are
`ReadOnlyPaths`. The unit writes only the dedicated clone and
`/var/lib/radon/grok-runtime` (the shared flock). `/home/radon/.grok` and the
provider/control sockets are inaccessible. Native clients remain in the
root-owned immutable release tree; the bridge uses private temporary state.
`ProtectHome=tmpfs` is deliberately NOT used: it would also hide the clone and
the venv the unit executes from.

The stripped env file itself is also in `InaccessiblePaths` (systemd reads it
before building the namespace), and every grok child process runs with an
allowlisted environment (`grok_runtime.grok_child_env`: PATH, HOME, locale,
proxy and CA vars). Turso and Pushover credentials stay with the Python
parent, which needs them for the ledger, heartbeats and alerts.

That allowlist alone is advisory, because the child runs under the same UID
as the parent and `ProtectProc=invisible` only hides OTHER users' processes.
Before each launch the controller calls `prctl(PR_SET_DUMPABLE, 0)` on
ITSELF (`grok_page_responder._harden_controller_against_child`), which
reparents its `/proc` entry to root and denies the same-UID read of the
parent environment. It is Linux-only, applied to the controller rather than
through a child `preexec_fn`, and a no-op when libc or prctl is unavailable
so a page response is never blocked by it.

Page text is untrusted third-party/exception content. It reaches the model
only inside `<untrusted-excerpt>` delimiters, and `build_prompt` re-sanitizes
a row that does not already carry them rather than trusting the writer.

## Ticket states (`watchdog_pages`, migration 0048)

`pending` → `claimed` → `done`. Three failed Grok runs → `skipped`.
Stale claims older than 2h are reclaimable. Overlapping cycles skip on
`.responder.lock`.

A Turso transport blip (timeout, reset, broken pipe, SSL EOF, URLError,
HTTP 502/503/504) on `claim_page` or `complete_page` does not fail the
oneshot. Classification is by exception type, not message text. SQL
errors still exit 1.

`claim_page` reads the row back on a fresh connection after a wire
failure. If this run's `claim_token` is present, the cycle continues.
If the row is still `pending`, it retries the UPDATE once. Otherwise it
skips (`lost_claim`). A double-fault (write and read both drop) can
still orphan the row until the 2h stale window.

`complete_page` is guarded by `claim_token` so it only applies while
this run holds the claim. After a transport error the cycle retries
once on a fresh socket, then `get_page`. If the row is already `done`,
it still sends the priority-0 follow-up. Unrecovered, it skips the
follow-up and the ok heartbeat rather than launching grok again.

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
| `scripts/grok_runtime.py` | Managed manifest verification, gateway inventory, lock, legacy LKG validation |
| `scripts/grok_upgrade.py` | Retained legacy rollback controller; no scheduled updater |
| `cloud/scripts/install-grok-upgrade-controller.sh` | Installs that copy under `/usr/local/lib/radon/grok-upgrade` |
| `/opt/radon-model-clients/current/manifest.json` | Root-promoted immutable client receipt |
| `cloud/services/radon-grok-page-responder.*` | VPS timer |
| `cloud/services/radon-grok-upgrade.*` | Read-only gateway readiness service; timer disabled |
| `cloud/scripts/setup-grok-page-responder.sh` | Clone, stripped env and managed client verification |
| `scripts/deploy_notify.py` | Live-gate Pushover |
| `cloud/scripts/deploy.sh` | `notify_release_live` after green marker |

A quiet healthy cycle prints `{"pending": 0}`.


### Account preparation and handover checks

Preparation verifies the protected managed manifest as the dedicated account.
Existing mutable native slots remain unchanged; preparation never invokes an
installer, provider login or inference probe. Authentication is not copied
from another account. Finalization separately checks private gateway readiness.

Both handover directions query `ActiveState` before and after disabling their
source timers. `active`, `activating`, `deactivating` and `reloading` workloads
refuse handover. A failed, empty or unknown state lookup also refuses; only
confirmed `inactive` or `failed` workloads permit timer ownership to change.
The second check retains the existing timer restoration path on refusal.
