# Weekend loops: host-side browser for sandboxed agents + stale runner-lock reclaim

> **STATUS (2026-09-20): PLAN ONLY.** Design and acceptance criteria for a
> separate implement PR. No product code changes here. Nothing in this file is
> a claim that the fix exists.

Two operator-verified defects on the Mac mini runner, one root cause each,
one implement PR. **Both are hard Definition of Done** (Joe, 2026-09-20
05:11 PT via Chief of Staff): the Chromium host gate AND the five lock-hygiene
items in §2 must all be met before the implement PR is called complete. Lock
hygiene is not a follow-up and may not be split off.

---

## 1. Chromium cannot launch inside the agent sandbox (testing nightly, 2 nights)

### Root cause

Chromium on macOS is multi-process. At startup the browser process calls
`bootstrap_check_in` to register the launchd Mach service
`org.chromium.Chromium.MachPortRendezvousServer.<pid>` so its child processes
(renderer, GPU, utility) can rendezvous. That is a Seatbelt `mach-register`
operation. Every agent CLI the testing loop launches wraps tool shells in a
Seatbelt profile that allows `mach-lookup` for a fixed service set and grants
**no** `mach-register` for `org.chromium.*`:

- codex `--sandbox workspace-write` (`codex-rs/sandboxing/src/seatbelt_base_policy.sbpl`;
  upstream openai/codex#21292, #31560: "no Chromium flag works around this;
  codex does not expose a user rule mechanism").
- Claude Code sandbox / `srt` (anthropic-experimental/sandbox-runtime#210, #212:
  same denial, same `--single-process` non-answer).

So `kr != KERN_SUCCESS` trips the `CHECK` at
`base/apple/mach_port_rendezvous_mac.cc:155`, Chromium traps (`SIGTRAP`,
`exitCode=null`), and Playwright reports `browserType.launch: Target page,
context or browser has been closed`. The host shell has no Seatbelt profile,
so the same binary launches and dumps the DOM (operator repro, exit 0).

Not the cause, verified by the operator repro: browser install, Playwright
version (1.58.2 in `web/`), the mini display, `--no-sandbox`, headless shell
vs full Chromium.

Evidence: `~/radon-weekend/.testing-deliver/remediate-2026-09-{19,20}/browser-preflight.log`
on the mini; `TEST_LOG.md` T-496 row ("Real Chromium launch exits SIGTRAP,
`bootstrap_check_in: Permission denied (1100)`"); `tasks/todo.md`
"Reliability remediate 2026-09-18" (REL-260 visual verification BLOCKED after
three browser launches, same class, sibling loop).

Consequence for the wrapper: the testing ladder is
`codex grok nvidia cerebras` (`scripts/testing_weekend.sh`
`PROVIDER_LADDER`), codex first, and the codex rung deliberately refuses the
bypass sandbox ("never the bypass flag: parity with the claude rung's bounded
grant"). There is no in-sandbox configuration that admits Chromium. The
browser must run in a process the sandbox does not wrap.

### Chosen design: wrapper-owned host browser server, runner connects over loopback

`@playwright/test` honours `PW_TEST_CONNECT_WS_ENDPOINT`: when set, the
default `browser` / `context` / `page` fixtures call `browserType.connect()`
against that endpoint and **never call `browserType.launch()`**
(`web/node_modules/playwright/lib/index.js:513-520`; playwright.dev/docs/docker
"Remote Connection"). `npx playwright run-server --host --port --path` is the
matching server: it launches a browser per connecting client using the
client's own launch options.

Mechanism verified on this branch's VM (Linux, so it proves the wiring, not
the Mach path): `run-server` on `127.0.0.1:47123/<token>`, then a spec run
with `PLAYWRIGHT_BROWSERS_PATH=/nonexistent` so the runner has no browser to
launch. Without the endpoint: `browserType.launch: Executable doesn't exist`.
With the endpoint: `1 passed`, `browser.version()` = 145.0.7632.6 served by the
host process. The runner never touched a browser binary.

Split of responsibilities on the mini:

| Process | Sandbox | Does |
|---|---|---|
| `testing_weekend.sh` (launchd child, host bash) | none | starts `playwright run-server` on `127.0.0.1:<port>/<unguessable path>` before the agent round, smoke-connects once, exports `PW_TEST_CONNECT_WS_ENDPOINT` + `RADON_WEEKEND_BROWSER_HOST=ready` into the agent environment, kills the server at phase end / on signal |
| agent CLI (codex / claude / grok) | Seatbelt | runs `npx playwright test <spec>` exactly as the skill already says; the sandboxed runner starts `next dev` (node, no Mach), then connects to the host browser instead of launching one |
| Chromium | none (host, via run-server) | renders `http://localhost:3000` served by the sandboxed Next process; same machine, so loopback resolves without `exposeNetwork` |

Why this is the smallest correct design:

- **No spec, config or assertion changes.** `web/playwright.config.ts`,
  every `web/e2e/*.spec.ts`, `page.route` mocks, `routeWebSocket`,
  screenshots and traces run unmodified; the env var is read by the runner
  itself. Money-path assertions are untouched by construction. CI is
  unaffected (the env var is only set by the wrapper on the mini).
- **The agent's code stays sandboxed.** Only the browser process escapes.
  Rejected alternative B (a host "job broker" that executes
  `npx playwright test` for the agent) would run agent-authored TypeScript
  and the agent-editable `webServer.command` unsandboxed, which is the bypass
  the codex rung refuses.
- **No plist change.** The wrapper owns the lifecycle; launchd already
  starts the wrapper unsandboxed.
- **Fails closed and legibly.** If the host browser cannot start, the agent
  is told so before it runs and the skill forbids the sandboxed launch that
  would burn three attempts and end in "BLOCKED".

Rejected alternatives, briefly:

- `--single-process` / `--no-sandbox` / `--disable-features=MachPortRendezvous`:
  upstream confirms none avoid the check-in; also changes the browser under
  test.
- codex `--sandbox danger-full-access` or Claude `sandbox.excludedCommands`:
  widens the grant for the whole round, contradicts the wrapper's stated
  parity rule, and codex `exec` has no per-command escalation.
- A launchd `WatchPaths` job that drains a queue of spec runs: plist + setup
  step + the same "runs agent code unsandboxed" problem as B.
- Hard-fail `OPERATOR_REQUIRED` at pre-flight: the operator's stated
  non-goal; gates must actually run.

### Trust boundary (state it, do not hide it)

`run-server` gives any process that knows the endpoint control of an
unsandboxed Chromium as the runner user. Bounds: loopback only
(`--host 127.0.0.1`), unguessable `--path` token generated per phase, the
endpoint is passed only in the agent's environment (name contains no
`KEY` / `SECRET` / `TOKEN`, which codex's default `shell_environment_policy`
would strip), server lifetime is one phase, killed with the round. Codex
workspace-write already reads the whole disk, so `file://` navigation adds no
read exposure; downloads land server-side in a temp dir.

The server binary must **not** come from the clone's `web/node_modules`
(agent-writable, excluded from `git clean`, so a phase-1 agent could plant a
`playwright/lib/cli.js` that phase-2 runs on the host). Same class as the
wrapper's "never exec disk python after the agent" rule. It comes from a
wrapper-owned install under `~/.radon/agent-cli/browser-host/` (the existing
`AGENT_CLI_ROOT` precedent), provisioned by `setup_testing_weekend.sh` at the
`@playwright/test` version pinned in `web/package.json` plus
`playwright install chromium`. Client/server must match on major.minor
(`1.58.x`); the preflight compares versions and fails closed on mismatch.

### Wrapper changes (`scripts/testing_weekend.sh`)

All inside `main()` (nothing above the `--lock-lib-only` line may fork; see
the file header). New helpers, `start_browser_host` / `stop_browser_host`:

1. In `run_phase`, after `ground_truth` and `refuse_billing_reroute_files`,
   before the round loop:
   `BROWSER_HOST_BIN="$AGENT_CLI_ROOT/browser-host/node_modules/.bin/playwright"`.
   Absent or version-mismatched against `$REPO/web/node_modules/@playwright/test`
   (read for the compare only, fail closed): `RADON_WEEKEND_BROWSER_HOST=unavailable:<reason>`, no endpoint, continue.
2. Otherwise `"$TIMEOUT_BIN" "$CAP_SECS" "$BROWSER_HOST_BIN" run-server --host 127.0.0.1 --port 0 --path "/$(openssl rand -hex 16)" >"$LOG_DIR/browser-host-$STAMP.log" 2>&1 &`,
   record `BROWSER_HOST_PID`, wait bounded (60 s) for `Listening on ws://…`.
   (`--port 0` behaviour must be confirmed on 1.58.2 during implement; fall
   back to a free-port probe if it is not honoured.)
3. Smoke, host side, bounded 60 s: a `node -e` one-liner from the browser-host
   install: `chromium.connect(endpoint)` -> `newPage` -> `setContent` -> `close`.
   This exercises the exact Mach path that fails in the sandbox. Success:
   export `PW_TEST_CONNECT_WS_ENDPOINT` and `RADON_WEEKEND_BROWSER_HOST=ready`.
   Failure: `unavailable:smoke-failed`, kill the server, no endpoint.
4. Phase-start line gains `browser-host=<ready|unavailable:reason>` next to
   the existing `ignored=` field, so the dead-man log names it per phase.
5. `stop_browser_host` in: end of `run_phase`, `on_signal` (before the
   Pushover, like `release_runner_lock`), and the EXIT trap. Not in
   `kill_round_group` (the server lives outside the round's process group by
   design so a rung advance does not restart it).
6. Reliability wrapper (`scripts/reliability_weekend.sh`): same block,
   byte-identical, since REL-260 is the same failure. Optional in the first
   implement PR; if deferred, say so in its body.

### Setup changes (`scripts/setup_testing_weekend.sh`)

- New step: provision `~/.radon/agent-cli/browser-host/` with
  `npm install --prefix … @playwright/test@<version from web/package.json>`
  and `npx --prefix … playwright install chromium`; re-run idempotently so a
  repo Playwright bump propagates (`check "browser host (playwright X.Y)"`).
- Toolchain check: `check "playwright run-server (host)"` runs the same smoke
  as the wrapper and prints the Mach error verbatim if it fails.
- Keep `web/.env` provisioning as is (the sandboxed Next server needs it).

### Skill / prompt changes (`.claude/skills/testing-weekend/SKILL.md`, then `python3 scripts/render_loop_prompt.py --write`)

- Remediate step 2(d): "run the relevant `web/e2e` spec" gains: the wrapper
  exports `PW_TEST_CONNECT_WS_ENDPOINT`; a plain `npx playwright test <spec>`
  connects to the host browser, so no browser launch happens in the sandbox.
- New rail: when `RADON_WEEKEND_BROWSER_HOST` is not `ready`, do **not**
  attempt a local Chromium launch (it dies on
  `bootstrap_check_in … Permission denied (1100)` and three attempts prove
  nothing new). Record the UI verification as operator-only with the exact
  action `bash scripts/setup_testing_weekend.sh` and quote the wrapper's
  `browser-host=` reason. This replaces the current "BLOCKED after three
  attempts" outcome for this class.
- Lesson bullet (dated) recording the root cause in one paragraph.
- `test_portable_prompt_sync.py` enforces the re-render of
  `.claude/portable-prompts/testing-weekend.*.md` and `.codex/skills/testing-weekend/`.

### Regression proof (red first, then green)

New `scripts/tests/test_weekend_browser_host.py`, same harness shape as
`test_rel180_loop_launchers.py` / `test_weekend_loop_deadman.py` (staged clone,
stub `gh`, stub agent binary that dumps its environment, stub `playwright`
on a stub `AGENT_CLI_ROOT`):

1. RED at HEAD: the stub agent's env has no `PW_TEST_CONNECT_WS_ENDPOINT`
   and the phase-start line has no `browser-host=`. GREEN after: stub
   `run-server` prints `Listening on ws://127.0.0.1:4711/tok`; agent env
   carries exactly `ws://127.0.0.1:4711/tok`; line says `browser-host=ready`.
2. Stub `run-server` exits 133 (SIGTRAP shape) or never prints: agent env
   has `RADON_WEEKEND_BROWSER_HOST=unavailable:…` and **no** endpoint; the
   phase still runs and reports; no orphan server pid after the phase
   (R-386 pattern: assert the pid is gone).
3. Host install absent, or version file says `1.57.0` against the clone's
   `1.58.2`: `unavailable:version-mismatch` / `unavailable:not-installed`.
4. SIGTERM to the wrapper mid-round: server pid is gone (extends
   `test_rel137_weekend_wrapper_survivability.py`).
5. Source contract: the wrapper never executes
   `web/node_modules/.bin/playwright` or `web/node_modules/playwright` from
   the clone (regex over the wrapper body, same style as the
   `test_ops_plane_bounds.py` contracts).
6. `python3 scripts/render_loop_prompt.py --check` green; a contract grep that
   the remediate section names `PW_TEST_CONNECT_WS_ENDPOINT` and the
   `Permission denied (1100)` refusal.

Live proof on the mini (implement PR body must carry it): one manual
`RADON_WEEKEND_REPO=~/radon-weekend/radon-testing bash scripts/testing_weekend.sh remediate`
whose run log shows `browser-host=ready`, and the next
`com.radon.testing-daily` cycle's remediate log showing a `web/e2e` spec pass
with a screenshot path, not a `bootstrap_check_in` line.

---

## 2. Dead `.weekend-runner.lock` hygiene

### What is on disk and why it blocks

- Wrappers take `$REPO/.weekend-runner.lock/` as a **directory** with a `pid`
  file (`mkdir` is the only atomic primitive on macOS; `acquire_runner_lock`,
  duplicated byte-identically in all six wrappers).
- `~/radon-weekend/.weekend-runner.lock` is a **plain file** at the weekend
  root containing `21108`, mtime 2026-09-04 00:00:43 PDT. No script in the
  repo writes that path (grep: only `$REPO/.weekend-runner.lock` and
  `weekend_prune.py` `RUNNER_LOCK` relative to a clone). `CI_PERFORMANCE_LOG.md`
  2026-09-04 onward shows the ci-performance agent "reclaiming" and re-taking
  runner locks itself ("Stale `.weekend-runner.lock` PID … was not live …
  before exclusive lock reclaim"), following its skill's rail 2 "Take an
  exclusive loop lock". The file is an agent artifact from that rail.
- Every reader treats it as held forever: `acquire_runner_lock` does
  `cat "$dir/pid"`, which fails on a plain file, yielding empty `held` ->
  "pid not yet published" refusal (R-411). A sandboxed agent that does
  `kill -0 21108` gets `Operation not permitted` (Seatbelt denies signalling
  outside the sandbox) and, per R-411, must read that as alive ->
  `lock-owner-unverified` / INCOMPLETE. Nothing on the host ever re-examines
  the file, so a pid dead for 16 days blocks ci-perf / docs pre-flight every
  night.
- The agents' "not live" verdict on the wrapper's own live pid is the same
  EPERM misread in the other direction, and it means an agent rewrote the
  wrapper's `pid` file mid-cycle; `release_runner_lock` then correctly
  refuses to unlock (`held != $$`) and the next fire reclaims it. Hygiene,
  not a collision, but it is why the skills must stop touching the lock.

### Hard DoD (Joe, 2026-09-20): five lock-hygiene requirements

| # | Requirement | Where it is met |
|---|---|---|
| L1 | No orphan shared-parent lock. `~/radon-weekend/.weekend-runner.lock` as a plain FILE is never valid; only per-clone DIRECTORY locks `$REPO/.weekend-runner.lock/pid` are. Setup and wrappers reject/remove it. | `sweep_shared_parent_lock` in every wrapper prologue; `setup_*` scripts; skill rails |
| L2 | Stale reclaim works outside the sandbox: launchd wrapper / setup reclaim when the pid is verifiably dead (ESRCH), never left to sandboxed agents that see EPERM and refuse forever. | `pid_alive` (host bash) in `acquire_runner_lock`, the sweep, and setup |
| L3 | Never block sibling loops: a dead or unverifiable lock must not strand ci-performance / documentation across nights. Reclaim-if-dead in bash before phase start; if live, stand down with a clear `REFUSED` + page, never a silent rc=75 forever. | wrapper prologue order + `report "REFUSED (lock held)"` detail names pid, owner, start time, command |
| L4 | EXIT / crash safety: owner-only release stays (R-411); `kill -9`, `timeout`, launchd stop cannot leave a 16-day corpse. | pid + start-time fingerprint in the lock; launchd-owned reclaim on the next fire of any loop bounds a corpse to one day |
| L5 | Regression tests or scripted proof for: file-shaped lock at the shared parent; directory lock with dead pid; directory lock with live pid (must not steal). | §Regression proof cases 1, 2, 4, 5, 6, 8, 9 below |

### Design: reclaim only from a context that can prove death (host bash), never weaken R-411

**Lock shape (L1, L4).** A valid lock is `$REPO/.weekend-runner.lock/` holding
`pid` (integer, unchanged for every existing reader: plist pre-reset guard,
setup scripts, `weekend_prune.py`, tests) plus a new `start` file: the
owner's process start time as `/bin/ps -p $$ -o lstart=` prints it. Written
`start` first, then `pid`, each via `tmp` + `mv -f`, so a reader that sees
`pid` also sees `start`. A lock with `pid` but no `start` (written by a
pre-fix wrapper) is read pid-only, so the upgrade needs no manual step.

**`pid_alive pid [start]`** (host bash; `/bin/ps` absolute for the same
reason the wrapper already hard-codes `/usr/bin/curl` and `/usr/bin/sed`):

```
kill -0 pid          succeeds                      -> alive          (fast path)
kill -0 pid          fails, /bin/ps -p pid empty   -> DEAD (ESRCH)   (reclaimable)
kill -0 pid          fails, /bin/ps -p pid lists it -> alive          (EPERM class: another user or a
                                                                       sandbox; absent evidence is not staleness)
alive AND start given AND ps -o lstart= differs    -> DEAD (pid reused by an unrelated process; L4)
```

The reuse check is what removes the wrapper's own documented failure ("a
recorded pid reused by ANY live unrelated process makes every subsequent daily
fire exit 3", `testing_weekend.sh` comment at the `REFUSED (lock held)` site).

**`acquire_runner_lock "$dir"`** (all six wrappers, byte-identical; no shared
sourced helper, because sourcing a clone file before `ground_truth` executes
agent-writable code on the host, which is the reason the wrappers are
single-file today):

```
mkdir succeeds -> write start, then pid; done
mkdir fails ->
  dir  with pid file : held=$(cat "$dir/pid") start=$(cat "$dir/start")  shape=dir   (unchanged read)
  plain file         : held=$(cat "$dir")                                shape=file  (new)
  held empty / non-numeric ->
      dir : refuse "held (pid not yet published)"                                    (R-411 unchanged)
      file: RECLAIM. A wrapper never writes this shape and there is no mkdir->pid
            window to protect, so an empty plain file is a foreign artifact, not a
            racing winner. Logged "reclaiming foreign plain-file lock (no pid)".
  pid_alive "$held" "$start" -> refuse "held by pid $held"                            (unchanged)
  else -> "[weekend] reclaiming stale runner lock (pid $held, $shape)"; mv aside; mkdir; write start, pid
```

"mv aside" = `mv -f -- "$dir" "$WEEKEND_ROOT/.stale-locks/$(basename "$REPO").weekend-runner.lock.<held|nopid>.<STAMP>"`
(preserved, never deleted; forensics for the operator). Live -> the existing
`report "REFUSED (lock held)"` path (exit 3, dead-man comment, Pushover)
with the detail extended to `pid <held>, started <start>, owner <ps -o user=>,
cmd <ps -o command=>` so an operator can tell a live cycle from a reused pid
at a glance (L3).

**Shared-parent sweep (L1, L3).** Before `acquire_runner_lock`, every wrapper
calls `sweep_shared_parent_lock "$WEEKEND_ROOT/.weekend-runner.lock"` (that
exact path only, never a glob; any shape, file or directory):

```
absent                                    -> nothing
no parseable pid (empty, garbage)         -> move aside (never a valid lock; L1)      log foreign-lock=removed:nopid
pid dead (pid_alive says DEAD)            -> move aside                                log foreign-lock=removed:<pid>
pid alive                                 -> leave it; log foreign-lock=live:<pid>; include in this
                                             phase's dead-man detail so it PAGES; proceed (the
                                             per-clone lock, not this file, gates the run)
```

A live foreign pid is a process that wrote a lock no wrapper honours; the
wrapper does not kill it and does not stand down for it (L3: nothing may
strand a sibling loop), it makes it visible. The first loop to fire after
merge moves the 2026-09-04 file to
`~/radon-weekend/.stale-locks/shared.weekend-runner.lock.21108.<STAMP>`.

**Setup scripts (L1, L2).** `setup_testing_weekend.sh` (and the five sibling
`setup_*`): the in-flight check `kill -0 "$(cat "$WEEKEND_REPO/.weekend-runner.lock/pid")"`
is replaced by sourcing the wrapper from the operator's own checkout
(`source "$SRC_REPO/scripts/<wrapper>" --lock-lib-only`, trusted, not the
runner clone) and calling `pid_alive` on the clone lock and each sibling
clone lock (dead -> move aside and continue; live -> `exit 1` naming pid,
owner, start), then `sweep_shared_parent_lock` (dead/no-pid -> moved aside;
live -> `exit 1` naming the pid, because setup is about to unload and reload
the job and must not race an unknown live process). Also a new
`check "no shared-parent lock"` line in the toolchain report.

**Crash safety (L4).** Unchanged: `on_signal` releases on SIGTERM/HUP/INT
(launchd stop sends SIGTERM first), the EXIT trap releases owner-only. New:
`kill -9`, `timeout -k`, a reboot mid-cycle leave `pid` + `start`; the next
fire of the same loop (launchd, host bash, at most 24h later) reclaims on
ESRCH, and a reused pid is caught by the `start` mismatch. The shared parent
is swept by whichever loop fires first (six fires per day). Worst case for
any corpse is therefore one day, not sixteen. `release_runner_lock` also logs
`[weekend] runner lock pid was rewritten during the cycle (now <held>)` when
`held != $$`, so an agent that overwrote the lock is visible.

**Plist pre-reset guard** (`kill -0 "$(cat "$C/.weekend-runner.lock/pid")"`):
unchanged. It is a conservative "skip the reset if possibly live" check;
false-live only delays a reset the wrapper's own `ground_truth` performs
anyway.

`scripts/weekend_prune.py::locking_pid` needs no change (already
dir-or-file, EPERM -> alive). Its refusal of a held clone still stands; a
dead per-clone lock is reclaimed by that loop's own next fire, not by prune.

Skills: `.claude/skills/ci-performance/SKILL.md` rail 2 ("Take an exclusive
loop lock") becomes the documentation-nightly wording: the wrapper's
`$REPO/.weekend-runner.lock` **is** the lock; never create, reclaim, move,
`kill -0` or otherwise verify it, never create or read
`~/radon-weekend/.weekend-runner.lock`; a wrapper is running, so the pid in
the clone lock is the wrapper's. A sandboxed `kill -0` returning
`Operation not permitted` is not evidence of anything and must not become a
`lock-owner-unverified` INCOMPLETE. Testing and documentation skills get the
same sentence where it is missing. Re-render portable prompts.

### Regression proof (red first; L5)

Extend `test_rel137_weekend_wrapper_survivability.py::TestRunnerLockIsRaceFree`
and `test_ops_plane_bounds.py::TestWeekendRunnerMutualExclusion`
(parametrised over all six wrappers, `source … --lock-lib-only`), plus a
setup-script case in `test_weekend_runner_env_provisioning.py`:

1. **L5 file-shaped lock (dead pid).** RED at HEAD: plain file at the lock
   path containing `999999` -> `acquire_runner_lock` returns 1 with "pid not
   yet published". GREEN: returns 0, the path is now a directory holding this
   process's `pid` and `start`, the old file sits under `.stale-locks/` with
   `999999` intact.
2. **L5 file-shaped lock (live pid).** Plain file containing `os.getpid()`
   -> refuse (must not steal).
3. Empty plain file -> reclaimed, log line names "foreign plain-file lock
   (no pid)"; the file is preserved under `.stale-locks/`.
4. **L5 directory lock, dead pid.** Directory with `pid=999999` (no `start`)
   -> reclaimed (already green today; pinned so the pid-only upgrade path
   stays honest). With `pid=999999` and any `start` -> reclaimed.
5. **L5 directory lock, live pid.** Directory with `pid=os.getpid()` and a
   matching `start` -> refuse; with `pid=os.getpid()` and no `start` ->
   refuse (must not steal, both shapes).
6. **L4 pid reuse.** Directory with `pid=os.getpid()` and
   `start=Thu Jan  1 00:00:00 1970` -> reclaimed (live pid, wrong fingerprint
   = a different process owns that pid now).
7. Directory with empty `pid` -> refuse "pid not yet published" (R-411 pin,
   unchanged).
8. **L2 EPERM is not death.** `kill` overridden as a function returning 1
   for a live pid: `pid_alive` still says alive because `/bin/ps -p` lists it.
   `pid_alive 999999` (no such process) says DEAD.
9. **L1/L3 shared-parent sweep.** `WEEKEND_ROOT/.weekend-runner.lock` as a
   plain file with `999999` -> moved to
   `.stale-locks/shared.weekend-runner.lock.999999.<STAMP>`, contents intact,
   phase-start line carries `foreign-lock=removed:999999`, wrapper proceeds to
   take its own clone lock. Same file with `os.getpid()` -> left in place,
   `foreign-lock=live:<pid>`, the phase still runs and the dead-man detail
   posted to the stub `gh` names the pid. Empty file -> moved aside,
   `foreign-lock=removed:nopid`. Directory shape with a dead `pid` -> moved
   aside. A second run with nothing there logs nothing.
10. **L3 live sibling never silent.** Clone lock held by `os.getpid()` ->
    exit 3 AND the stub `gh` received a `REFUSED (lock held)` comment whose
    body names the pid, and the stub `curl` received the Pushover
    (`test_prologue_page_wire.py` already asserts the page; extend the body
    assertion).
11. Race pin unchanged: eight simultaneous acquires, exactly one `WON`.
12. Setup: `setup_testing_weekend.sh` against a staged `WEEKEND_ROOT` with a
    dead-pid shared-parent file -> moves it aside and continues; with a
    live-pid file -> `exit 1` naming the pid; with a dead-pid clone lock ->
    reclaims and continues (extends the existing in-flight check).
13. Contract: the six `pid_alive` / `acquire_runner_lock` /
    `release_runner_lock` / `sweep_shared_parent_lock` bodies are
    byte-identical across wrappers (drift guard without extraction), and
    `sweep_shared_parent_lock` is called before `acquire_runner_lock`
    (ordering pin, same style as `test_ops_plane_bounds.py`'s "lock before
    reset").

Scripted proof on the mini for the implement PR body: the first post-merge
fire's phase-start line (`foreign-lock=removed:21108`),
`ls -l ~/radon-weekend/.stale-locks/`, `cat` of the preserved file showing
`21108`, and `ls -d ~/radon-weekend/radon-*/.weekend-runner.lock` showing
only directories with `pid` + `start`.

---

## Files the implement PR is expected to touch

| Path | Change |
|---|---|
| `scripts/testing_weekend.sh` | browser host start/stop/smoke, phase-start field, lock helpers |
| `scripts/reliability_weekend.sh`, `scripts/ci_performance_nightly.sh`, `scripts/documentation_nightly.sh`, `scripts/security_nightly.sh`, `scripts/security_deepsec_nightly.sh` | lock helpers byte-identical; reliability also gets the browser host (or a stated deferral) |
| `scripts/setup_testing_weekend.sh` | browser-host provisioning + check; lock checks via `pid_alive` + shared-parent sweep |
| `scripts/setup_reliability_weekend.sh`, `scripts/setup_ci_performance.sh`, `scripts/setup_documentation_nightly.sh`, `scripts/setup_security_nightly.sh` | lock checks via `pid_alive` + shared-parent sweep (L1, L2); reliability also gets the browser host if included |
| `.claude/skills/testing-weekend/SKILL.md`, `.claude/skills/ci-performance/SKILL.md`, `.claude/skills/documentation-nightly/SKILL.md` | browser rail, lock rail; then `.claude/portable-prompts/*` and `.codex/skills/*` via `render_loop_prompt.py --write` |
| `scripts/tests/test_weekend_browser_host.py` (new), `test_rel137_weekend_wrapper_survivability.py`, `test_ops_plane_bounds.py`, `test_weekend_loop_deadman.py`, `test_prologue_page_wire.py`, `test_weekend_runner_env_provisioning.py` | red/green above (browser cases 1-6, lock cases 1-13) |
| `docs/operations.md` | owner for `nightly-loops` (`docs/owners.json`): the `browser-host=` / `foreign-lock=` fields, the `~/.radon/agent-cli/browser-host` install, the lock shapes and who may reclaim |
| `docs/owners.json` | only if a new helper file is added outside the `scripts/*_weekend.sh` / `scripts/*_nightly.sh` globs |
| `.gitignore` | nothing new expected (`.weekend-runner.lock/` already ignored) |

Not touched: `web/playwright.config.ts`, any `web/e2e/*.spec.ts`,
`.github/workflows/ci.yml`, `config/com.radon.*.plist`,
`scripts/weekend_prune.py`.

---

## Done-when (checkable; every item is hard DoD for the implement PR)

Browser host gate (operator DoD 1-4):

1. **Root cause in the implement PR body**: Seatbelt `mach-register` denial for
   `org.chromium.Chromium.MachPortRendezvousServer.<pid>` under the agent CLI
   sandbox vs allowed on host; cites the two log paths and T-496.
2. **Browser acceptance runs on the mini without a manual Chromium host**: the
   first post-merge `com.radon.testing-daily` remediate log contains
   `browser-host=ready`, at least one `web/e2e` spec executed by the agent
   with a screenshot artifact, and zero `bootstrap_check_in` lines. Money-path
   e2e list in `ci.yml` unchanged; no spec, config or assertion diff.
3. **Regression**: `test_weekend_browser_host.py` cases 1-6 green and case 1
   shown red at the pre-fix SHA; the skill forbids the sandboxed launch when
   `RADON_WEEKEND_BROWSER_HOST != ready` and names the operator action instead
   of claiming BLOCKED.
4. **Implement PR opened separately**, number reported, with an explicit
   "what Joe runs after merge" section (below).

Lock hygiene (Joe's hard DoD L1-L5; same implement PR, not split):

5. **L1 no orphan shared-parent lock.** After the first post-merge fire of
   any loop, `~/radon-weekend/.weekend-runner.lock` does not exist and
   `~/radon-weekend/.stale-locks/shared.weekend-runner.lock.21108.<stamp>`
   holds the preserved file; that fire's phase-start line reads
   `foreign-lock=removed:21108`. `setup_*` refuses (`exit 1`, pid named) on a
   live shared-parent lock and removes a dead one. Every lock left on the mini
   is `radon-*/.weekend-runner.lock/` with `pid` + `start`.
6. **L2 reclaim outside the sandbox.** Lock regression cases 1, 4, 8 green
   (dead pid reclaimed by host bash via ESRCH; EPERM stays alive), case 1 red
   at the pre-fix SHA. No skill or prompt tells an agent to `kill -0`,
   reclaim or create a runner lock (grep over `.claude/skills/*/SKILL.md`,
   `.claude/portable-prompts/*`, `.codex/skills/*`).
7. **L3 never block sibling loops.** Lock case 9 (sweep proceeds past a live
   foreign file and pages it) and case 10 (a live clone lock exits 3 with a
   `REFUSED (lock held)` comment naming pid, owner, start, command, plus the
   Pushover) green. On the mini: ci-performance and documentation post
   audit-phase dead-man comments on the first night after merge with no
   `lock-owner-unverified` / `runner-lock-held` INCOMPLETE.
8. **L4 crash safety.** Lock case 6 green (live reused pid with a stale
   `start` is reclaimed); `test_rel137_weekend_wrapper_survivability.py`
   SIGTERM cases still release; a `kill -9` of a smoke run on the mini leaves
   `pid` + `start` and the next manual fire logs
   `reclaiming stale runner lock (pid <n>, dir)` and runs.
9. **L5 proof set.** Cases 1-2 (file-shaped, dead and live), 4 (directory,
   dead), 5 (directory, live: must not steal) exist as tests parametrised
   over all six wrappers and are green; the mini scripted-proof block (ls,
   cat, phase-start line) is pasted in the implement PR body.

Full gates before the implement PR's commits: `python3.13 -m pytest`
(focused first: `python3.13 scripts/run_pytest_affected.py --files scripts/testing_weekend.sh … -- -q`),
`bash -n` on all six wrappers, `python3 scripts/render_loop_prompt.py --check`.

---

## What Joe runs after the implement PR merges (no plist reload expected)

1. On the mini, with no cycle in flight
   (`ls -d ~/radon-weekend/radon-testing/.weekend-runner.lock` absent, or
   `kill -0 $(cat …/pid)` fails):
   `git -C ~/radon-weekend/radon-testing fetch origin && git -C ~/radon-weekend/radon-testing checkout -f main && git -C ~/radon-weekend/radon-testing reset --hard origin/main`
   (git only; never `cp` the wrapper, per its header).
2. `bash scripts/setup_testing_weekend.sh` from the operator checkout: sweeps
   the dead shared-parent lock to `~/radon-weekend/.stale-locks/` (prints the
   pid it moved), installs `~/.radon/agent-cli/browser-host/` (Playwright
   1.58.x + chromium) and prints `ok  playwright run-server (host)` and
   `ok  no shared-parent lock`. The plist is re-installed by the same script
   unchanged; no `launchctl` action beyond what setup already does.
3. Optional smoke, still host shell:
   `RADON_WEEKEND_REPO=~/radon-weekend/radon-testing bash ~/radon-weekend/radon-testing/scripts/testing_weekend.sh remediate`
   and confirm `browser-host=ready` and `foreign-lock=` on the phase-start
   line.
4. If step 2 is skipped, the next fire of any loop performs the same sweep.
   Delete `~/radon-weekend/.stale-locks/` whenever convenient.
5. If reliability is included: repeat steps 1-2 for `radon-reliability` via
   `setup_reliability_weekend.sh`.

## Open verifications for the implementer (not assumptions)

- `run-server --port 0` on 1.58.2 (else probe a free port).
- Codex workspace-write with `network_access=true` permits an outbound
  loopback WebSocket from the runner, and the sandboxed `next dev` can bind
  `:3000` (T-496 reached the launch step, which implies the web server
  readied, but confirm on the mini).
- The Claude rung's sandbox, if ever used by this loop, allows loopback
  egress; if not, the same `unavailable:` path applies and the rail holds.
- `PW_TEST_CONNECT_WS_ENDPOINT` reaches worktree subagents (env inheritance;
  expected yes).
- `/bin/ps -p <pid> -o lstart=` prints a stable, second-resolution start time
  on macOS 15 for a process owned by another user (needed for the fingerprint
  compare; fall back to `-o etime=`-free pid-only if it is empty).
- bash 3.2 on the mini: no `mapfile`, no `${var,,}`; the helpers must stay
  POSIX-ish like the rest of the wrapper (the cloud/tests baseline already
  documents that trap).
