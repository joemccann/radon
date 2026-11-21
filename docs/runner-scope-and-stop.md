# Runner scope and stop rules

This page records what the Mac mini runner is already allowed to touch, which live tests the prompts allow, when a run stops, which outside stop commands exist in the tree, and which end-of-run checks those files already require. A sentence here is a citation of a prompt or script. Anything those files do not enforce is under [Open gaps](#open-gaps). `docs/runner.md` stays the owner of `scripts/runner/**` and `.claude/runner-prompts/**` (`docs/owners.json`, rule `nightly-runner`).

The shape follows the operator's Daybreak Red reference (OpenAI, "Deploying Daybreak Red", 9 November 2026): scope, pause, stop, an outside stop, and an end checklist. Controls in that paper that no file in this repo enforces are gaps, not rules.

## Loops

`scripts/runner/run_loop.sh <loop>` reads `scripts/runner/loops/<loop>.env`, clones into `$HOME/radon-runner/work/<loop>`, and runs `.claude/runner-prompts/<loop>.md` (`scripts/runner/run_loop.sh`). `scripts/runner/install.sh` `print_plist` installs LaunchDaemon `com.radon.runner.<loop>` as `_radonbot` with `ExitTimeOut` 60. Seven env files exist.

| Loop | Prompt | Branch prefix | Clock (`SCHEDULE_*`) | Cap |
|---|---|---|---|---|
| `security` | `.claude/runner-prompts/security.md` | `security` | hour 0 minute 40, weekdays `0,2,4,6` | `PHASES` audit 28800s, remediate 21600s, deliver 10800s |
| `security-deepsec` | `.claude/runner-prompts/security-deepsec.md` | `security-deepsec` | hour 0 minute 50, weekdays `1,3,5` | same three phase caps |
| `reliability` | `.claude/runner-prompts/reliability.md` | `reliability` | hour 0 minute 0, every day | `TIMEOUT_SECS` 10800 |
| `testing` | `.claude/runner-prompts/testing.md` | `testing` | hour 0 minute 10, every day | `TIMEOUT_SECS` 14400 |
| `ci-performance` | `.claude/runner-prompts/ci-performance.md` | `ci-performance` | hour 0 minute 20, every day | `TIMEOUT_SECS` 10800 |
| `performance` | `.claude/runner-prompts/performance.md` | `performance` | hour 22 minute 0, every day | `PHASES` `perf:14400` |
| `documentation` | `.claude/runner-prompts/documentation.md` | `documentation` | hour 3 minute 0, every day | `TIMEOUT_SECS` 10800 |

Clock, prefix, cap, and prompt path are the assignments in each `scripts/runner/loops/<loop>.env`. `security` and `security-deepsec` set `ALLOWED_AGENTS=claude`, `GH_GUARD=1`, `PRE_RUN=hooks/security_pre.sh`, `POST_RUN=hooks/security_post.sh`, and `SUBSCRIPTION_LOCK=claude`. The other five env files do not set `GH_GUARD`.

`scripts/codemap_nightly.sh` is a separate nightly job, not a `run_loop.sh` loop. Its header says it regenerates `tools/codemap/codemap.json`, `codemap.data.js`, and `architecture.json` from `origin/main` in its own clone and opens a PR for the operator to merge. The script's Operator Next Steps say automation never merges. It closes older open `codemap/` PRs after a replacement exists. The script states no live-system or credential rails.

## What each loop may touch

### Launcher, all seven loops

`scripts/runner/run_loop.sh` `launch_agent` is the execution boundary the script itself names ("the existing clone, approval flags, stdin and process group"). It unsets `PUSHOVER_USER` and `PUSHOVER_TOKEN` in the agent child. `apply_agent_env` unsets every `AGENT_UNSET` name before the resolver, the hooks, and the agent, and logs `IGNORING: <NAME>` without the value.

The same function launches:

- `claude` with `--dangerously-skip-permissions` and `--disallowedTools ScheduleWakeup Monitor CronCreate`
- `codex`, `agy`, and `fx` with `--dangerously-bypass-approvals-and-sandbox`
- `grok` with `--always-approve`

`security` and `security-deepsec` set `ALLOWED_AGENTS=claude`, so a non-claude rung is refused before clone (`scripts/runner/run_loop.sh` `refuse_disallowed_agents`, `scripts/runner/loops/security.env`, `scripts/runner/loops/security-deepsec.env`). The other five env files list `grok codex agy fx:nvidia fx:cerebras`.

`GH_GUARD=1` puts the root-owned `gh` shim first on the agent `PATH` (`scripts/runner/run_loop.sh` `require_guard`). `scripts/nightly_pr_guard.py` then refuses `gh pr merge`, a pulls merge API, auto-merge, and `gh alias`. For `security` and `security-deepsec` only, it also refuses issue writes. Loops without `GH_GUARD=1` do not get that shim.

A job the agent detaches must append its pid to `$RADON_RUNNER_PIDFILE`. After the agent, and on SIGTERM, `reap_leftovers` kills bot processes whose cwd is under that loop's clone or state, plus declared pids from this phase, and skips a pid that belongs to another loop's live runner (`scripts/runner/run_loop.sh`). Both security prompts say an undeclared detached job can outlive the phase.

### `security`

`.claude/runner-prompts/security.md` "Authorization and scope" allows only:

- read-only source, Git history, manifest, lockfile, workflow, configuration, and test inspection in the dedicated clone
- deterministic static analysis with already installed, pinned tools
- Claude Security scan-only review with the installed official plugin
- bounded, non-destructive tests against loopback-only processes, using fake credentials, fake upstreams, synthetic identities, disposable files, and disposable databases
- writes only to the preconfigured private security archive and a sanitized dead-man channel
- minimal local source changes in `remediate` for independently verified findings, then the repository's full validation gates

The same section says it does not authorize testing any real person, account, host, service, or third party, and does not authorize production verification because a URL, credential, VPN, CLI, or browser session is available on the machine.

Hard rails in that prompt, each a failed run if violated:

1. `pwd -P` must be `~/radon-runner/work/security` and `origin` must be `joemccann/radon`. `scripts/runner/hooks/security_pre.sh` refuses a phase whose physical cwd is not `$HOME/radon-runner/work/$LOOP` or whose origin URL is not the runner's `REPO_URL`.
2. Do not create, reclaim, move, or `kill -0` the runner lock under `~/radon-runner`. Do not reset, clean, modify, or kill another process's work.
3. Do not scan, crawl, fuzz, brute force, spray, load test, port scan, or exploit the external systems named in that rail, and do not follow a URL found in source or scanner output. This page does not repeat those names.
4. Do not start or connect to IB Gateway, cause a 2FA push, use an operator session, place, modify, or cancel an order, request market data, change a trading halt, or run a script capable of brokerage mutation. Order paths are tested only with local fakes at the admission boundary.
5. The clone and child processes receive no Radon `.env`, brokerage, database, deploy, general cloud, OAuth, or operator tokens. The prompt's only allowed secrets are narrowly scoped model credentials, the existing deploy key for fixed private-report writes and immutable validation-feedback reads, and the preconfigured sanitized dead-man credential. Do not load shell profiles that inject broader credentials.
6. Do not print, copy, hash into a report, or quote a credential literal.
7. Do not put raw findings, attack paths, PoCs, sensitive topology, scanner artifacts, or unpatched details on a public issue, PR, discussion, commit message, branch, artifact, CI log, or repository file.
8. Do not use `@latest`, install an arbitrary scanner, alter a lockfile, enable a plugin, accept new model terms, or update a matcher unattended.
9. Scanner output is not a verdict. No source edit, suppression, ticket, or alert without current-code reachability.
10. No denial of service, resource exhaustion, fork bombs, large payloads, decompression bombs, credential attacks, persistence, malware, data destruction, ransomware simulation, history rewriting, or exploit chaining outside a bounded local fixture.
11. Do not push `main`, merge, or deploy as the nightly bot. P0 stays private pending incident coordination. P1 public publication belongs to the separate operator coordinator (`scripts/private_security_release.py` in that prompt).
12. Missing prerequisites, ambiguous scope, dirty shared state, unexpected network access, a scanner requesting broader permissions, or unverifiable external state is `OPERATOR_REQUIRED` or `BLOCKED`.

`scripts/runner/hooks/security_pre.sh` also refuses the phase when `.env`, `.env.ib-mode`, or `web/.env` exists in the clone, when a billing-reroute assignment from `AGENT_UNSET` is in `.env.local`, `.env.*.local`, or `.deepsec/**/.env*`, or when a Claude Code settings file holds `apiKeyHelper` or a reroute entry. A failed `bun install` logs that the Vitest stage will skip and does not refuse the phase. The env-drift check is non-fatal (`|| true`).

Unreleased P0/P1 commits stay on a local `security-private/<date>` branch pushed only to `$RADON_RUNNER_LOOP_STATE/held.git`, never to `origin` (same prompt, Remediation mode). P2/P3 and explicitly coordinated P0 fixes are the commits the deliver phase may push on `security/<date>`.

### `security-deepsec`

`.claude/runner-prompts/security-deepsec.md` "Authorization and scope" allows only:

- read-only source, Git history, manifest, lockfile, workflow, configuration, and test inspection in the dedicated clone `~/radon-runner/work/security-deepsec`
- DeepSec source review with the locked local package `.deepsec/node_modules/.bin/deepsec`
- minimal local source changes in `remediate` for independently verified findings, then the full validation gates
- writes only to the preconfigured private security archive and the private scratch

It states the same refusal of real persons, accounts, hosts, services, third parties, and production verification. Rails 3, 4, 5, 6, 7, 10, 11, and 12 match the security loop's live-system, trading, credential, disclosure, destructive-test, no-deploy, and fail-closed rules, with this loop's own wording. Rail 8 allows `scripts/nightly_toolchain_update.py ensure-deepsec` to stage, smoke, and recover only this loop's private `.deepsec` tree, and otherwise forbids `@latest`, `npx deepsec`, install, upgrade, lockfile edits, matcher regeneration, and new model terms during the night. The anti-pattern section says this loop does not run gitleaks history sweeps, Claude Security, or active local tests. Those belong to `.claude/runner-prompts/security.md`.

`scripts/runner/loops/security-deepsec.env` sets `KEEP_PATHS=".deepsec data/radon"`. `scripts/runner/run_loop.sh` moves those paths aside before the re-clone and restores them. The prompt forbids committing or publishing either directory. Unreleased P0/P1 branches are `security-deepsec-private/<date>` in `$RADON_RUNNER_LOOP_STATE/held.git` only.

`scripts/runner/hooks/security_pre.sh` is the pre-run hook for this loop too, including the `.deepsec/**/.env*` walk.

### `testing`

`.claude/runner-prompts/testing.md` rails: fakes and mocks only. No IB Gateway, no live connection, no order, no Turso, no Unusual Whales, no ssh, no VPS, no service restart. No production credentials. The clone has no `.env`. Do not look for credentials, create one, or read or print a secret value. The prompt says the suites need none. Never push `main`, force-push, or merge. Never weaken a test. The header branch is the only branch to push. Local verification is focused pytest and Vitest, then serial full pytest, `npx vitest run`, and `cloud/tests`. Do not detach work into a new session.

### `reliability`

`.claude/runner-prompts/reliability.md` rails: no IB Gateway, no 2FA, no order placed, modified, or cancelled, never set or clear the production trading halt, no Turso, no Unusual Whales, no ssh, no VPS, no service restart. Fault injection is fakes and mocks only. No production credentials, no `.env`, do not look for, create, read, or print a secret. Never push `main`, force-push, or merge. Never weaken a test or gate. Focused suites and the named drills run locally. Full pytest, `cloud/tests`, and Vitest stay on PR CI. Do not detach work.

### `ci-performance`

`.claude/runner-prompts/ci-performance.md` rails: GitHub Actions is the only source of truth. No production host access. Never run `gh workflow run`, re-run `main`, or add a dummy commit to sample a deploy. No ssh, no VPS, no IB Gateway, no 2FA, no orders, no Turso, no service restarts, no DNS. No production credentials. Do not look for them or read or print a secret. Never weaken a gate. Never push `main`, force-push, or merge. The header branch is the only branch to push.

### `performance`

`.claude/runner-prompts/performance.md` rails: production access is the probe file `<State>/prod-probe.json` only. Never ssh, never use ssh keys, never contact the VPS, Turso, Unusual Whales, IB Gateway, or any production URL. No load test except against a process this loop started locally. No production credentials. The clone has no `.env`. Do not look for, create, read, or print a secret. Do not modify `cloud/services/*`, `cloud/config/*`, `cloud/scripts/*` other than `perf_probe.py`, `scripts/db/migrations/*`, `.github/workflows/*`, Docker files, or systemd timers. Those are operator-only findings. `perf_probe.py` stays read-only, stdlib-only, and loopback-or-handshake-only. Trading-path code is ranked last and touched only for a P0 with a fault-injection test that shows behaviour unchanged. Never push `main`, force-push, or merge.

`scripts/runner/loops/performance.env` sets `PERF_SSH_TARGET` for the installer-copied helper. This page does not copy that value. The prompt forbids the agent from using SSH.

### `documentation`

`.claude/runner-prompts/documentation.md` scope is durable human contracts in the tree (API, auth, dependencies, configuration names, deploy topology, migrations, schedules, CLI procedures, recovery). Rails: no IB Gateway, no 2FA pushes, no orders, no Turso writes, no deploys, no service restarts, no DNS or firewall changes, no external consoles. Never run a production, broker, destructive, or credential command to verify prose. Never read or print secret values. Never change runtime behavior to make a doc true. Never push `main`, force-push, or merge. The header branch is the only branch to push. Documented commands are checked against parsers or `--help`, and the prompt says never run them live.

## Live-test limits

No runner prompt names a non-production target, a dedicated test account, or a lab. The limits below are the ones the prompts already state. There is no separate lab to point them at.

`.claude/runner-prompts/security.md` Stage 5 is the only prompt that authorizes active tests:

- Start Radon only on loopback, with synthetic configuration, fake identities, fake upstreams, disposable storage, and explicit process cleanup.
- Prefer framework test clients and existing Playwright fixtures over a network server.
- Exercise the surfaces that prompt lists (authorization, browser controls, parser and path bounds, WebSocket tickets, order admission, AI tool boundaries, deploy config) only as the delta implicates them.
- Order admission uses a fake broker that cannot reach IB.
- Bound the number of requests, payload size, concurrency, runtime, and retries. The prompt sets no numbers.
- Do not run ZAP, nuclei, sqlmap, masscan, nmap, generic exploit packs, or a browser crawler unless a human separately approves a pinned configuration and the target remains the disposable loopback fixture.
- `RADON_AUTHLESS_TEST=1` may support UI fixtures and cannot prove authentication.
- Production smoke verification is outside this unattended prompt.

`scripts/runner/loops/security.env` and `scripts/runner/loops/security-deepsec.env` set `RADON_WEEKEND_BROWSER_HOST=unavailable:disabled`.

`.claude/runner-prompts/security-deepsec.md` does not authorize those tests. Its anti-pattern section forbids running them in this loop. `deepsec process` in that prompt is source review (`--diff`, `--concurrency 2`). The prompt says `process` has no per-command cost or duration cap, and the outer deadline is the bound. A full refresh uses repeated `process --reinvestigate <wave-marker> --limit N` and must not be an uncontrolled whole-repository pass.

`.claude/runner-prompts/testing.md`, `.claude/runner-prompts/reliability.md`, `.claude/runner-prompts/ci-performance.md`, `.claude/runner-prompts/performance.md`, and `.claude/runner-prompts/documentation.md` authorize local fakes, mocks, local processes, Actions logs, or doc checks, and forbid broker, gateway, order, and production actions as cited above. None of them authorizes a production broker action.

## When a run must pause or stop

### Pause

None of the seven `.claude/runner-prompts/*.md` files contains a pause instruction. They open by saying nobody is available to answer during the run. The Daybreak pause (different host, real user data, or a request for broader permissions, then wait for the analyst) is not a rule in this repo. See [Open gaps](#open-gaps).

### Stop, from the prompts

Violating a hard rail is a failed run (`.claude/runner-prompts/security.md`, `.claude/runner-prompts/security-deepsec.md`, `.claude/runner-prompts/reliability.md`).

Fail closed, do not improvise, when a prerequisite is missing, scope is ambiguous, shared state is dirty, network access is unexpected, a scanner asks for broader permissions, or external state is unverifiable (both security prompts, rail 12). Record `OPERATOR_REQUIRED` or `BLOCKED`, do not advance the audited SHA, and for a clean `OPERATOR_REQUIRED` the prompts say to exit the phase cleanly and print the completion marker.

`.claude/runner-prompts/security.md` Stage 1: if a possible live secret or a real account, portfolio, transaction, or other sensitive financial record is found, stop model-backed scanners, do not reproduce the value, and do not open a public issue or PR.

`.claude/runner-prompts/security.md` severity table, `P0 Critical`: stop normal work, archive privately, send a sanitized urgent alert, require operator coordination, never push or disclose. `.claude/runner-prompts/security-deepsec.md` `P0` says archive privately, send a sanitized urgent alert, require operator coordination, and never push or disclose. It does not say "stop normal work".

Both security prompts: `nightly_publish.py` status `error` (exit 1) must stop publication. After three evidence-backed failed remediation approaches, record `BLOCKED` and stop modifying that finding.

A phase is `INCOMPLETE`, must not advance the audited SHA, and must not print the completion marker when any of these happened: provider budget or spend stop, wall-clock cap or outer timeout, SIGTERM or kill, work left to a background task, a test suite still running, a scan or `deepsec process` cut off, or a missing stage marker (both security prompts, "Completion marker, INCOMPLETE, and resume"). `ScheduleWakeup`, `Monitor`, `CronCreate`, and waiting on a completion notification end the process and score the phase `INCOMPLETE`. The launcher removes those three tools (`scripts/runner/run_loop.sh` `launch_agent`).

`.claude/runner-prompts/security-deepsec.md` Stage 1: if `deepsec.config.ts` is not the local model route, or a `.deepsec/.env*` file carries a key line, record `OPERATOR_REQUIRED` and stop before DeepSec runs. If `ensure-deepsec` fails, record `INCOMPLETE`, do not advance the SHA, and do not start DeepSec.

Do not print a deliver-ready verdict while a check is still red or pending (both security prompts, deliver step 6 and the anti-patterns).

The non-security prompts say a `nightly_publish.py check` exit 1 stops publication (`.claude/runner-prompts/testing.md`, `.claude/runner-prompts/reliability.md`, `.claude/runner-prompts/documentation.md`, `.claude/runner-prompts/performance.md`, `.claude/runner-prompts/ci-performance.md`).

### Stop, from the scripts

`scripts/runner/hooks/security_pre.sh` exits 2, and `scripts/runner/run_loop.sh` then skips the agent (`PHASE_RC=2`, refused), when:

- `LOOP` is not `security` or `security-deepsec`
- cwd is not this loop's runner clone, or origin is not `REPO_URL`
- `.git` is not a plain directory, or the hook cannot rebuild `.git/config`
- a credential file or billing-reroute file matches the checks above
- `git fetch origin` fails, or `git checkout` of the pinned base fails

If the green-base helper does not return a 40-hex SHA that exists, the hook detaches `origin/main` instead of refusing (`scripts/runner/hooks/security_pre.sh`).

`scripts/runner/run_loop.sh` `build_prompt` tells the agent: "Time budget: N minutes, then this session is stopped." `run_agent` runs `timeout -k 60` for `TIMEOUT_SECS` (`KILL_AFTER_SECS=60`). `run_agents_in_order` retries a claude rung once unless the exit is 124, and a 124 returns immediately without another rung.

`scripts/runner/lifecycle_gate.py` forwards SIGTERM and SIGINT to the `run_loop.sh` child. `on_signal` kills the agent and hook process groups, reaps leftovers, pages `KILLED`, gives `POST_RUN` ten seconds, posts the phase line, and exits 143. `docs/operations.md` names the SIGTERM sources it documents: a bootout, launchd `ExitTimeOut` of 60, or a reboot. `scripts/runner/install.sh` `print_plist` sets that `ExitTimeOut` to 60.

A live per-loop lock, or a live `subscription-claude.lock` when `SUBSCRIPTION_LOCK` is set, makes the new fire exit 0 without cloning. It does not stop the holder (`scripts/runner/run_loop.sh`, `docs/runner.md` loop config).

`scripts/runner/install.sh` `refuse_active_install` exits 75 and does not install while `launchctl print` for a runner job, the perf-probe job, or the native-update job is missing the line `state = not running`.

## Commands that stop a run from outside the agent

These are the stop-related commands the tree actually contains. A command that unloads one live runner job and leaves it unloaded is not among them. That is an [open gap](#open-gaps).

`scripts/runner/install.sh` `install_loop` sets `label="com.radon.runner.$1"` and runs:

```bash
launchctl bootout "system/$label" 2>/dev/null || true
launchctl bootstrap system "$plist"
```

The bootout is the unload half of a reinstall. The next line loads the job again. The same pair reinstalls `com.radon.model-gateway-local` and `com.radon.runner-perf-probe` in that script. It is not a stop that leaves the job unloaded.

`docs/runner.md` unloads retired user LaunchAgents, not `com.radon.runner.*`:

```bash
for l in com.radon.security-daily com.radon.security-deepsec com.radon.claude-cli-env-drift com.radon.claude-stable-sync; do launchctl bootout gui/$(id -u)/$l 2>/dev/null; rm -f ~/Library/LaunchAgents/$l.plist; done
```

`docs/runner.md` Operate starts a loop with `sudo launchctl kickstart system/com.radon.runner.documentation`. That command is a start. The same page's smoke section says not to stop a foreground run to read the log, and later says to keep a bad installation's job stopped. It does not give the stop command.

Once a SIGTERM or SIGINT reaches the process, the enforced path is `scripts/runner/lifecycle_gate.py` forwarding the signal and `scripts/runner/run_loop.sh` `on_signal` (kill the process group, reap, page `KILLED`, exit 143). The in-process budget stop is `timeout -k 60` inside `run_agent`, which is not an outside command.

## End-of-run checklist

Checks below are the ones a prompt or script already requires. Items the operator asked to confirm that no file requires are gaps.

### Report and the log

Security and DeepSec, every phase, before the completion marker: write `$RADON_RUNNER_LOOP_STATE/scratch/latest-report-<phase>.md` mode 0600 (`.claude/runner-prompts/security.md`, `.claude/runner-prompts/security-deepsec.md`, "Private operator report").

`scripts/runner/hooks/security_post.sh` publishes that file only when it is a regular file newer than the phase start. A symlink is left in place. When no such file exists, the hook writes a harness report whose body is the exit, the clock, and a short tail of that phase's log, scrubs it with `scripts/credential_redaction.py`, and publishes the scrubbed file to the private reports repository. The hook does not compare the text of an agent-written report with the log.

The phase is OK only with a column-0 completion line, `SECURITY-NIGHTLY PHASE COMPLETE:` or `SECURITY-DEEPSEC PHASE COMPLETE:` (`scripts/runner/hooks/security_post.sh`, both prompts). The prompts then require a `RESULT:` line: `RESULT: <phase> <status> - <PR URL or no PR>` with status `COMPLETE`, `INCOMPLETE`, or `OPERATOR_REQUIRED`, and no finding detail.

`docs/runner.md` smoke, written for the documentation loop and used as the completion check: a `RESULT:` line in the log, the Pushover page, and, when a change was published, a draft PR whose checks on that exact head are green. A runner `done` page does not prove CI passed. If that evidence is missing, preserve the log and do not reinstall while the run is active.

### Branches and PRs

Security and DeepSec deliver: the verdict line lists ready PR URLs or `prs=0` (both prompts). `scripts/runner/hooks/security_post.sh` re-reads each claimed PR before a ready-to-merge page: open, same repository, the loop's branch prefix, head SHA still the recorded SHA, and a non-empty check rollup that is wholly successful. The record is `$RADON_RUNNER_LOOP_STATE/.<loop>-deliver/record.json` plus `branch:` and `pr:` in the private `run-record.md` (both prompts). Zero PRs is accepted only when the open-PR listing ran, returned fewer than `PR_LIST_PROOF_LIMIT` (100) rows, and contained no same-repo PR on the prefix (`scripts/runner/hooks/security_post.sh`).

Private P0/P1 branches are listed in the run-record and in `held.git`. They are not pushed to `origin` (both security prompts).

The other five prompts require the last output line `RESULT: <PR URL> - <one-clause summary>` or `RESULT: no PR - <why>`, and the rolling-issue comment must carry tonight's PR URL or why there is none (`.claude/runner-prompts/testing.md`, `.claude/runner-prompts/reliability.md`, `.claude/runner-prompts/documentation.md`, `.claude/runner-prompts/performance.md`, `.claude/runner-prompts/ci-performance.md`). `docs/runner.md` smoke lists published PRs with `gh pr list --author radon-runner-bot --state all` and checks the draft on `<prefix>/<date>`.

`scripts/codemap_nightly.sh` prints the branch it opened, or `0 PR(s)` when the publish gate is a no-op.

### Credentials

Before the agent starts, `scripts/runner/hooks/security_pre.sh` refuses credential files and billing-reroute files in the two security clones. `scripts/runner/run_loop.sh` `apply_agent_env` unsets `AGENT_UNSET`, and `launch_agent` unsets `PUSHOVER_USER` and `PUSHOVER_TOKEN` in the child. Every loop prompt cited above forbids reading, printing, or loading production credentials, and the security prompts forbid quoting a credential literal in the report. `scripts/runner/hooks/security_post.sh` scrubs the report it publishes.

No prompt or script re-checks at the end of the run that credentials were untouched. That missing check is an [open gap](#open-gaps).

## Open gaps

These are absent from the prompts and scripts. They are not rules.

1. No non-production target, dedicated test account, investigation window, or separate lab is defined in any `.claude/runner-prompts/*.md` file or under `scripts/runner/`. Daybreak's scope block (target, permitted actions, window, stopping point, dedicated account) is not implemented. The prompts forbid real hosts instead.
2. No file pauses a run for a person when the agent reaches a different host, sees real user data, or asks for broader permissions. The security prompts classify those as `OPERATOR_REQUIRED` or `BLOCKED` and say nobody can answer during the run. Daybreak's pause is not implemented.
3. No checked-in command stops one live `com.radon.runner.<loop>` job from outside the agent and leaves it unloaded. `scripts/runner/install.sh` pairs every runner `launchctl bootout` with `launchctl bootstrap`. `docs/runner.md` Operate has `kickstart` and `print`, and its "keep the affected job stopped" sentence has no command. Nothing in the tree blocks a running job's access to a target from outside the agent, which Daybreak requires before the first run and again if monitoring is lost.
4. `.claude/runner-prompts/security.md` Stage 5 says to bound request count, payload size, concurrency, runtime, and retries, and it sets no numbers. No script enforces a request rate. `.claude/runner-prompts/security-deepsec.md` says `deepsec process` has no per-command cost or duration cap. The Daybreak walkthrough's example limits (one request per second, 12 attempts, five minutes) are not adopted by any prompt.
5. `scripts/runner/run_loop.sh` `launch_agent` starts Claude with `--dangerously-skip-permissions` and Codex with `--dangerously-bypass-approvals-and-sandbox`. Daybreak's sandbox, permission profile, `requirements.toml`, and network allowlist are not in `scripts/runner/`. The hard rails against production scanning, live trading, and destructive tests are prompt text. `scripts/runner/hooks/security_pre.sh` enforces clone identity, origin, and credential-file refusal. It does not intercept outbound connections or broker calls.
6. `GH_GUARD=1` is set only in `scripts/runner/loops/security.env` and `scripts/runner/loops/security-deepsec.env`. The other five loops' "never merge" line is the prompt. `scripts/nightly_pr_guard.py` is not on their `PATH` (`scripts/runner/run_loop.sh` `require_guard`).
7. No end-of-run step compares an agent-written private report with the runner log. `scripts/runner/hooks/security_post.sh` substitutes a log-tail harness report only when the agent file is missing, older than the phase start, or not a regular file. No step lists background jobs after the run and confirms they stopped, beyond `reap_leftovers`, which kills bot processes whose cwd is in that loop's clone or state. The security prompts warn that an undeclared detached job can outlive the phase.
8. No end-of-run check proves credentials were untouched. The pre-run refusal, the `AGENT_UNSET` unset, and the Pushover unset are start-of-process controls. Daybreak's "revoke temporary credentials" has no runner step. The security prompts name allowed secrets (model session, private-archive write credential, dead-man credential) and do not revoke them at the end.
9. `scripts/codemap_nightly.sh` is outside `run_loop.sh` and states no live-system, broker, or credential rails. Its only stated limits are the generated codemap artifacts, the publish gate, and "automation never merges".
10. `docs/owners.json` does not list this page as an owner of the prompts or `scripts/runner/**`. A prompt change does not fail CI when this page is stale.
