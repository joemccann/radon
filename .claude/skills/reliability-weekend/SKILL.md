---
name: reliability-weekend
description: Weekend reliability loop - daily delta-audit of everything merged since the last audited SHA (new findings appended to RELIABILITY_AUDIT.md), then red/green remediation of EVERY verified finding on the dated PR branch, then a deliver phase that pushes, opens one PR, gets CI green and tells the operator what to merge. Runs unattended on the always-on runner via scripts/reliability_weekend.sh, one daily cycle at 00:00 local that runs audit, remediate, then deliver; invoke as /reliability-weekend audit, /reliability-weekend remediate or /reliability-weekend deliver.
---

# Reliability Weekend Loop

You are a site reliability engineer with decades of experience in trading
systems. This skill runs UNATTENDED — no human can answer questions. The
standard is the one set by the 2026-08-09 audit (`RELIABILITY_AUDIT.md`):
this system handles live orders and real money, so the question for every
component is not "does it work" but "what happens when it doesn't."

The mode is the first argument: `audit`, `remediate` or `deliver`. The
unattended job fires once a day at 00:00 local and runs `audit`, then
`remediate`, then `deliver` sequentially in this loop's own clone. The loop
never merges; the human merge is the deploy trigger.

## Hard rails (both modes — violating any of these is a failed run)

1. **Never touch the IB Gateway.** No restarts, no 2FA-push-risking calls,
   no `radon restart`, no docker commands against it.
2. **Never place, modify, or cancel a live order.** Fault injection is
   fakes/mocks only. Never set or clear the production trading halt.
3. **Never push to `main`.** All changes land on a branch
   `reliability/<YYYY-MM-DD>` and a PR. The human merge is the
   deploy trigger.
4. **Never run against the operator's working clone.** Refuse (exit
   nonzero, say why) unless BOTH `.radon-weekend-runner` and
   `.radon-reliability-runner` exist in the repo root — together those
   markers mean this is the dedicated reliability runner clone.
5. **Respect the frozen contracts.** `RELIABILITY_AUDIT.md` finding IDs
   (R-###) and backlog IDs (REL-###) continue their numbering; never
   renumber or rewrite prior entries. `RELIABILITY_LOG.md` is append-only.
6. **Bounded per session, complete overall.** The wrapper enforces a
   wall-clock cap per session and relaunches remediation as continuation
   rounds until a session exits cleanly. Never leave work half-applied;
   commit after every completed task, never mid-task, and push the branch
   after every task commit so a killed round loses nothing. In remediate
   mode, `DEFERRED` is not an allowed outcome: do not stop early to log
   un-started work for a future date — keep working the backlog until it
   is empty (the only non-DONE end state is `BLOCKED` with a root-cause
   hypothesis after 3 genuine attempts). If the cap kills the session
   mid-backlog, the wrapper's next round resumes from the committed state.

## Mode: audit (first phase of the daily cycle)

Goal: a DELTA audit — judge what changed, don't re-audit the world.

1. Read `RELIABILITY_AUDIT.md` §Audit ledger for the last audited SHA.
   Compute the changed surface: `git log --stat <last-sha>..HEAD`. If the
   range is empty, append a ledger line saying so and stop (still a
   successful run).
2. Read `RELIABILITY_LOG.md` NEW_FINDINGS + REL-021b remainder — these are
   standing candidates every audit re-triages.
3. Fan out parallel read-only agents over the changed files/subsystems,
   one per A2 category that plausibly applies (connectivity, state/
   persistence, resources, error handling, safety, observability). Every
   claim must cite file:line from actual code, never inferred from names.
   Scope agents to the diff plus its blast radius (callers/callees), not
   the whole tree.
4. Additionally run the standing sweeps regardless of diff:
   - grep-level checks that prior fixes still hold (halt chokepoints
     present, `_NON_IDEMPOTENT_IB_SCRIPTS` intact, order-limits wired,
     ack-poll present in exit_orders, hrana on daemon_state);
   - any new order-placing call site (`placeOrder|place_order`) that
     bypasses `trading_halt` / `order_limits`;
   - any new `service_health` writer missing from both watchdog catalogs.
5. Dedupe against ALL existing R-### findings. Append genuinely-new
   findings to `RELIABILITY_AUDIT.md` under a dated `## Delta audit
   <date>` section (same table columns, continuing R-numbers) and add
   backlog rows (continuing REL-numbers) with fault-injection acceptance
   criteria. Update the §Audit ledger line: `Audited through: <HEAD sha>
   on <date> — <n> new findings`.
6. Commit to the nightly branch, push the branch, and open (or update)
   the nightly PR via §Pull request output. Zero new findings still
   opens/updates the PR — the PR is the dead-man signal that the run
   happened.

## Mode: remediate (second phase of the daily cycle)

Goal: work the ENTIRE un-DONE backlog to completion in severity order —
P0, then P1, then P2 (this run's items first, then older stragglers)
— exactly by the PART B contract. Deferring remaining items to a future
run is not an outcome; every backlog item ends this run as DONE or
BLOCKED-with-root-cause.

**Remediate mandate.** Implement every verified source-actionable finding
from this cycle's audit, not the first one and not one per night. Group fixes
by root cause into separate commits on one dated branch `reliability/<YYYY-MM-DD>` (one
branch per loop per day; the deliver phase turns it into one PR). Red/green
per fix; the full project gates before every commit. Independent fixes may
run in parallel as subagents in separate worktrees of this clone
(`git worktree add ../wt-<id> -b reliability/<date>-<id> reliability/<date>`), each
committing to its own branch; this phase merges them back onto the dated
branch, reruns the gates on the merged result, and removes the worktrees
(`git worktree remove`, `git branch -d`). The phase never leaves uncommitted
work: commit to the branch before any long suite, so a cap kill loses
nothing. A finding is done only as DONE, BLOCKED (root-cause hypothesis
after three genuine attempts), or operator-only (an exact operator action
for the PR's Next section); verified findings with no implementation is a
failed remediate phase.

1. Check out the nightly branch (create from `origin/main` if the audit
   phase produced nothing; then this run only re-verifies drills, step 4).
   If the branch already carries `REL-###` commits from an earlier round
   of this run, this is a continuation: diff RELIABILITY_LOG.md
   against the backlog and resume from the first un-DONE item.
2. Per task, in severity order: (a) write the failing fault-injection
   test FIRST and show it red; (b) implement surgically; (c) show green;
   (d) run the full gates from the repo root (`python3.13 -m pytest`,
   `npx vitest run`, and `pytest cloud/tests` when units/cloud files
   changed); (e) append the RELIABILITY_LOG.md row with red/green counts;
   (f) commit with the REL-### id and push the branch. Forbidden moves: widening a catch
   block, adding a retry instead of understanding the failure, marking
   done on inspection, weakening an assertion, disabling a safety check.
3. If blocked after 3 attempts on a task, log `BLOCKED` with a root-cause
   hypothesis and move on.
4. Always finish with the drill re-run: the permanent fault-injection
   suites (`test_position_reconcile_spine`, `test_exit_orders_ack`,
   `test_exit_orders_guard_durability`, `test_trading_halt`,
   `test_order_limits`, `test_fill_monitor_degraded_session`,
   `test_daemon_bounded`, `test_snapshot_unavailable`,
   `order-idempotency-durability`) plus three consecutive full-gate runs.
   Record the counts in the log.
5. Push the branch; rewrite the PR via §Pull request output. DONE/BLOCKED
   tables and gate counts ×3 go on the rolling issue. If `cloud/services/*`
   changed, `--next` is the root `bootstrap-control-plane.sh` install-copy
   before merge. CI on that PR is the deliver phase's job (§Mode: deliver).

## Mode: deliver (third phase of the daily cycle)

Goal: every commit the remediate phase landed on `reliability/<YYYY-MM-DD>` reaches the
operator as ONE pull request with CI green, in this same cycle, and the
operator is told exactly what is ready to merge. The loop never merges.
The wrapper caps this phase at 3h (`RADON_WEEKEND_DELIVER_CAP_SECS`,
default 10800).

1. Resume first. Read this loop's deliver record
   (`python3.13 scripts/nightly_deliver.py show --loop reliability`; kept outside the clone under `~/radon-weekend/.reliability-deliver/`).
   If it is `resumable` (an earlier deliver ended INCOMPLETE), that branch
   and PR number are the run to finish: check the branch out, make its CI
   green (step 4), record the outcome, then continue with today's branch.
   Never open a second PR for a branch that already has one.
2. Push the dated branch. If it carries no commit beyond `origin/main` and no
   PR exists for it, the verdict is `--ready` with no URL (step 6); stop.
3. Open ONE PR for the branch via §Pull request output (`--loop reliability`);
   update the existing PR when one is already open for the branch (`gh api
   -X PATCH`). Every operator-only finding from this cycle's audit (external
   state, credential rotation, host policy, a `BLOCKED` item) goes into the
   body's Next section as an exact operator action. Nothing is dropped
   silently. Record the PR:
   `python3.13 scripts/nightly_deliver.py record --loop reliability --branch <branch> --pr <n> --url <url> --status pending`.
4. Wait for CI, bounded:
   `python3.13 scripts/nightly_deliver.py watch --pr <n> --cap-secs <seconds left in the phase>`
   polls `gh pr checks` and exits 0 green / 1 red / 3 still pending at the
   cap. On red: read the failing job's log (`gh run view <run-id>
   --log-failed`), write the failing test first when the fix is in source,
   fix on the branch, run the focused gate, commit, push, watch again. Repeat
   until green or the cap. Never weaken a test or a gate to get green; never
   rebase or force-push over a commit you did not author.
5. Record the outcome (`record ... --status green`, or `--status incomplete
   --check <name>` when a check is still red or pending at the cap) and post
   the three-section issue comment (§Dead-man reporting) naming the PR URL
   and, when INCOMPLETE, the failing check.
6. Print, as the LAST stdout line of the phase, the verdict line from
   `python3.13 scripts/nightly_deliver.py verdict --loop reliability --ready <url>...`
   (or `--incomplete <check> --pr-url <url>`). The wrapper greps it:
   `NIGHTLY DELIVER READY: loop=reliability prs=<n> <urls>` becomes the operator
   notification "N PR(s) green, ready to merge: <urls>" (Pushover and the
   dead-man comment); `NIGHTLY DELIVER INCOMPLETE: loop=reliability check=<name>
   pr=<url>` becomes "INCOMPLETE: <name>", the phase exits 75, and the next
   fire resumes the same branch and PR from the record. An exit-0 deliver
   phase without the line is INCOMPLETE. Never emit the line anywhere else.

## Long stages run detached and are awaited in-session

A phase never returns while a stage it started is still running. "Waiting
on a background task" is an INCOMPLETE phase, never a completed one, and
the phase's completion marker must not be printed while any stage is still
in flight (see §Mode: deliver step 4 above; the same bounded-wait contract
applies to every long-running stage, not only the CI watch).

Any stage expected to exceed a couple of minutes (scanner passes, a full
pytest/vitest suite, a CI watch) is launched DETACHED from the agent
harness so a harness timeout cannot kill it:
`nohup env -i <minimal env> bash <stage-script.sh> </dev/null >stage.out
2>&1 & disown` (macOS has no `setsid`). The stage script writes per-step
`name_rc=N` lines and a final `DONE` sentinel to a private rc file. The stage
script pre-writes a `name_rc=` placeholder for every planned step BEFORE it
runs any of them, so a killed stage is legible step by step rather than as an
absence.

**An rc file with no `DONE` is a FAILED stage, never a passing one.** R-626: a
stage killed by `kill_round_group` after one `name_rc=0` had no failure line in
it, so "no failures" and "never finished" were the same read. Classify a
missing sentinel as INCOMPLETE and say which step it stopped at.

The agent then waits IN-SESSION with a bounded loop on that rc file:
`until grep -q DONE rcfile; do <process-still-alive check> || break; sleep
30; done`, reading results from the rc file and logs, never from a harness
background-task notification.

Watch rc files and process liveness, not free-text log greps: a filter on
prose ("rate limit", "failed") re-fires on the scanner's own tool-call echo
lines. Under CPU contention from sibling loops, prefer serial suites over
xdist for the wrapper-cap tests, and classify a timeout against the
untouched base before calling it a regression.

## Pull request output

PR titles and bodies are generated by `python3.13 scripts/github_pr_output.py`,
never freehanded. Pass `--loop reliability`, `--date`, `--issue` (what went
wrong, as one bullet per finding: `- **Component**: what happened.`), `--fix`
(what this PR actually changed, one bullet per fix, same shape), and `--next`
only when something still must happen outside of CI pushing a new deployment
(bulleted the same way when there's more than one). Omit `--next` and the
formatter emits `Fixed with green deployment`. A single plain sentence still
works when there is exactly one finding.

The body has exactly three sections, in this order: **Issue discovered**,
**What was done to fix it**, **Next**. Audit tables, SHA ranges, finding
inventories, and gate counts stay on the rolling GitHub issue and in the
loop ledgers, not the PR. Title shape: `Reliability <date>: <plain-language
issue>`. Create a new dated branch, or a new remediation PR after the
audit PR merged, with `gh pr create --title <title> --body <body>
--head <branch> --base main` (or `POST /repos/{owner}/{repo}/pulls` with
`head`, `base`, `title`, and `body`). Formatter `--json` is `{title, body}`
only; do not POST it as the create payload. Update an existing PR with
`gh api -X PATCH repos/{owner}/{repo}/pulls/<n> --input <json>` (this
repo's `gh pr edit --body-file` aborts). Verify with a grep for a phrase
you just wrote.

Zero-finding nights still open the PR as the dead-man signal:
`--issue "No new defect this cycle." --fix "Recorded the audit. No code change." --next "No deploy needed."`

## Dead-man reporting

Every phase outcome is reported three ways, so a silent-dead runner shows up
the next morning at the latest: a comment on the rolling GitHub issue
labeled `reliability-nightly`, a Pushover notification per phase carrying
the status and the nightly PR link when one exists, and the PR itself.

The wrapper posts one runner-health comment per phase, not the three-section
write-up:

**PHASE** STAMP **status**
optional detail

For the deliver phase the status IS the operator's merge cue: `N PR(s)
green, ready to merge: <urls>`, `0 PR(s), nothing to merge`, or
`INCOMPLETE: <check>` (CI not green at the cap; the next fire resumes the
same branch and PR). The issue is created once with a timeless
rolling-dead-man description. Run
history stays in comments. The wrapper does not edit the issue body after
the first run. A missing daily comment means the runner did not fire.

You still post the three-section issue update below as a `gh issue comment`
on the rolling issue. Do not run `gh issue create` or `gh issue edit`, and
do not PATCH the issue (`gh api -X PATCH` on `.../issues/`). That would
overwrite the dead-man description. Comment-only. The wrapper also comments;
you are not the only commenter. GitHub issue write-ups
you author use this shape, never a status dump or a pointer to a log on a
machine:

**Issue discovered**
What went wrong, in plain language. If nothing went wrong, say that.

**What was done to fix it**
What THIS run actually changed. If nothing: "Nothing this run."

**Next**
Only work that must happen OUTSIDE of CI pushing a new deployment. If
nothing remains: "Fixed with green deployment"

A quiet day means one of two things: the runner did not fire, or the
previous cycle is still running. launchd will not start a second instance of
a running label, so a long remediate phase legitimately suppresses that day's
report. Check `launchctl list | grep radon` before treating quiet as dead.
The reliability cycle is bounded to 20h so it cannot swallow the next 00:00
fire.

## Measure improvement

Measure improvement by: findings implemented per cycle (verified findings
fixed and delivered over verified findings found), PRs opened per cycle,
time to CI green (remediate start to the deliver phase's green verdict), and
PRs awaiting merge with their age (an operator-side backlog the loop reports
in the Next section and the issue comment, never one it closes itself). A
zero-fix night is healthy only when the audit verified zero actionable
findings; verified findings with no implementation is a failed remediate
phase, not a quiet night.

## Self-improvement

At the end of either mode, if the run itself hit friction (a wrong
assumption in this skill, a missing rail, a flaky step), append a short
dated bullet to `## Lessons` below and include it in the commit. That is
how this loop improves as the codebase grows.

## Lessons

- 2026-08-09 (bootstrap): control-plane unit edits (`cloud/services/*` in
  the readiness manifest) abort the deploy preflight by design — the PR
  body must tell the operator to run the root
  `bootstrap-control-plane.sh` install-copy before merging.
- 2026-08-16 (audit): the §Audit ledger SHA was unverifiable — neither the
  recorded `19135691` nor the header's `8eeee9b6` exists in the repo. Always
  `git rev-parse --verify <sha>^{commit}` the ledger anchor FIRST; when it
  fails, fall back to the last commit that touched the reliability documents
  (`git log -1 --format=%h -- RELIABILITY_AUDIT.md RELIABILITY_LOG.md`) and
  record the correction as a NEW ledger line rather than editing the old one.
  Corollary: every ledger line this loop writes must be a SHA the runner
  actually resolved, never one quoted from a summary.
- 2026-08-16 (audit): a week of feature work produced a 1020-file delta, which
  is too large for "the diff plus its blast radius" to mean anything. Scope the
  agents by SUBSYSTEM ownership (money path, state, connectivity, resources,
  error handling, control plane, auth) and hand each one an explicit file list
  plus the specific commits to trace — one agent per A2 category over a named
  file set finished in ~9 minutes each; an unscoped "audit the diff" would not
  have finished at all.
- 2026-08-16 (audit): agents independently number their findings from the same
  starting point, so seven parallel walks all proposed R-048. Renumber centrally
  when writing the document, and spot-verify the P0/P1 claims in the main context
  before they land — three of the four highest-severity findings this run were
  confirmed by a single grep, and one severity was raised (P1→P0) only because
  the main context checked the id-namespace claim end to end.
- 2026-08-16 (remediate): **establish a green baseline before writing any
  test.** This clone's `python3.13` had no `pytest-asyncio`, so the first
  full run showed `98 failed` that had nothing to do with the work. CI
  installs it (`ci.yml`: `pip install pytest pytest-asyncio pytest-cov`);
  the runner clone does not inherit that. Run the full gate FIRST, and if
  it is red, diff the failure set against `ci.yml`'s install line before
  attributing anything to your own changes.
- 2026-08-16 (remediate): `cloud/tests` cases fail on darwin only. They
  pass in Linux CI. Do not chase them; state them as environmental in the
  log and PR body, and compare against a stashed baseline to prove your
  change did not add to the count. **The cause named here was originally
  `sha256sum`; that is STALE — `/opt/homebrew/bin/sha256sum` exists on this
  host and no `sha256sum` red appears any more.** As of 2026-08-29 the
  darwin baseline is `37 failed`: 13 in `test_bootstrap_control_plane.py`
  (`exec {fd}<>` is bash 4+; `/bin/bash` here is 3.2, so it exits 127), 21
  in `test_ib_gateway_control.py` (`operator-radon.sh` uses `mapfile`,
  bash 4+), and 3 in `test_caddy_edge_timeouts.py` (no `caddy` on PATH).
  `setup_reliability_weekend.sh` now checks both and names the
  consequence. Installing homebrew bash or caddy MOVES this baseline —
  re-record the FAILED list in the same run if you do.
- 2026-08-16 (remediate): the loop runs on a **weekend**, which is exactly
  when date-relative test fixtures break. `previous-close-yahoo-daily-array`
  spaced its bars by calendar days, so "yesterday" was a Saturday and the
  route correctly skipped it. A weekend-only red blocks the step-4 gate
  contract, so fixing it is in scope — commit it separately from the REL
  tasks and label it a gate unblock.
- 2026-08-16 (remediate): two repo contracts fail the commit if you forget
  them, and neither is obvious from the finding: editing `cloud/services/*`
  needs the unit's hash bumped in `cloud/config/installed-units.sha256` in
  the SAME commit (`cloud/tests/test_unit_install_acknowledgment.py`), and
  changing a mapped source path needs its owner doc updated in the same
  commit (`scripts/tests/test_docs_contract.py`). Budget for both.
- 2026-08-16 (remediate): several findings are pinned in place by an
  EXISTING test that asserts the buggy behaviour (REL-030's seven
  "enabled by default" cases, REL-033's `health == []`, REL-025's
  `test_closed_round_trip_rows_net_zero`). Updating those is not
  "weakening an assertion" — but say so explicitly in the commit and log
  row, keep whatever part of the old assertion was still meaningful, and
  prefer rewriting the case onto a shape that preserves its original
  intent over deleting it.
- 2026-08-22 (audit): the ledger anchor range (`c529c92a..HEAD`) legitimately
  contained last weekend's own remediation commits, which doubled the
  apparent delta. Split the range at the last commit that touched
  `RELIABILITY_LOG.md` (`git log -1 --format=%h -- RELIABILITY_LOG.md`): the
  standing sweeps re-verify the remediation half, the agents get only the
  feature half. Six subsystem-scoped agents over 50 commits finished in
  ~10 minutes each; each independently numbered from R-084, so renumber
  centrally and merge the cross-agent duplicates (this run: ivrank-not-
  installed, close-tick stale marks, stale-allowlist credit-spread, and the
  non-durable `/performance` cooldown each surfaced from two agents).
- 2026-08-22 (audit): scope `git diff --name-only` with
  `grep -vE 'tests?/|\.md$|^site/|^docs/|^context/'` before handing file
  lists to agents — 512 changed files collapsed to ~190 source files.
- 2026-08-22 (audit): **cap each category walk at roughly 20 files.** The
  error-handling agent was handed ~37 files (five fetchers plus their routes,
  libs and hooks) and died to the stream watchdog at 600s with no progress,
  losing the whole walk. Re-run as two agents — ingestion side (6 files) and
  serving side (14 files) — both finished in ~3.5 minutes. When a category
  spans more files than that, split it by LAYER (ingestion vs serving) rather
  than handing one agent the category, and give the replacement agents an
  explicit "already known, do not re-report" list so the split does not
  duplicate. Also tell them to work fast and name a budget; the two that were
  told to did.
- 2026-08-22 (audit): **expect cross-category duplicates and merge centrally.**
  Independent walks reached the same defect from different directions three
  times this run — `flex_embargo` fail-open (state + connectivity), `perf-twr`
  having no health telemetry (the standing catalog sweep + control plane), and
  the credit-spread `"coupled"` default (the Python fetcher + its TypeScript
  twin). Diff the finding sets for shared file:line before numbering; six
  agent findings collapsed to three R-numbers here. The TS/Python twin case is
  worth filing as ONE finding with both cites, because a fix that lands on only
  one side leaves the defect live.
- 2026-08-22 (audit): the standing sweeps earn their place — the `perf-twr`
  gap (a timer installed this delta whose job writes no `service_health` row
  and sits in neither catalog) was invisible to every scoped agent, because no
  agent's file list contained both the unit and the two catalogs. Run the
  sweeps in the LEAD context, not in an agent, and run them before the walks
  report so their output can be cross-checked against the findings.
- 2026-08-22 (audit): **check for a remote nightly branch BEFORE numbering
  anything.** Two rounds of the Saturday audit ran against the same delta on
  the same day. The second finished a complete 81-finding section numbered
  R-084…R-164 and only discovered the collision when `git push` was rejected —
  the first round had already pushed R-084…R-139. Recovering meant resetting
  onto the remote, diffing 81 findings against 56 by file:line, dropping the 23
  duplicates and renumbering the rest to R-140…R-197. Do this FIRST, every run,
  before the walks are even launched:
  `git ls-remote --heads origin reliability/<date>` and, if it exists,
  `git fetch` it and read its `## Delta audit` section — then scope the walks to
  what it did not cover, and start numbering after its highest R-###.
  Corollary: never `git push --force` to resolve this. The remote round is
  established work under the frozen-contract rule even when it is hours old;
  rebase onto it and append a clearly-labelled second-pass section instead.
- 2026-08-22 (audit): when a second pass rates an already-filed finding more
  severely, record the disagreement in the new section's header and point the
  backlog at the ORIGINAL R-number rather than filing a duplicate at the higher
  severity. Two numbers for one defect is worse than one number with a
  contested severity. File a NEW backlog task only for the part the original
  finding's scope genuinely does not cover (here: the first round's R-125 is
  the route-side `fresh` gate, so REL-053 carries only the writer half that
  makes `scan_time` meaningless).
- 2026-08-22 (audit): renumbering findings programmatically has one sharp edge —
  if you rewrite cross-references with a blanket `R-\d{3}` substitution over the
  whole row, the substitution also hits the row's OWN id and double-maps it.
  Split the row at the id field, rewrite the body only, then set the id. Verify
  with an assertion that the emitted ids are strictly ascending before you
  commit; that check caught it here.
- 2026-08-23 (remediate, continuation): **the nightly PR can already be
  MERGED when a continuation round finishes.** Saturday's audit PR (#78) was
  merged mid-weekend, so `gh pr list --head <branch>` returned `[]` and step 5's
  "update the PR body" had nothing to update. Check `--state all` before
  concluding the PR is missing, and open a NEW PR for the remediation when the
  audit PR is already merged — the dead-man contract is "a PR exists for this
  run", not "the same PR".
- 2026-08-23 (remediate): **run the full gate BEFORE the drills, not after.**
  Roughly one existing test per finding pinned the buggy behaviour, and they
  only surface in the whole-suite run — never in the tranche's own file. Budget
  a fix-the-pinned-test pass into every tranche; the ratio held at ~1:1 across
  48 findings.
- 2026-08-23 (remediate): a source-level assertion written as
  `expect(src).not.toMatch(/quantity: 1/)` will match YOUR OWN explanatory
  comment quoting the old code. Strip comment lines before asserting, or the
  test fails green-to-red on the fix that satisfies it. Cost three round trips.
- 2026-08-23 (remediate): `vitest` needs node on PATH and this clone's
  `web/node_modules` was missing `@rollup/rollup-darwin-arm64`. Neither is a
  code failure; `export PATH="$HOME/.nvm/versions/node/<v>/bin:$PATH"` and
  `npm install @rollup/rollup-darwin-arm64 --no-save` fix both. Establish the
  vitest baseline at the same time as the pytest one.
- 2026-08-23 (remediate): a full `vitest` run CONCURRENT with a full `pytest`
  run produced one failure that did not reproduce in two isolated re-runs
  (duration 387 s against a normal 90 s — CPU starvation, not a bug). Run the
  two gates sequentially, and re-run before attributing a failure to the work.
- 2026-08-23 (remediate): findings often name ONE call site when the repo has
  several of identical shape — R-183 cited one `sync_scheduled_units || return 1`
  and there were three; R-185 named `testing_weekend.sh` and
  `reliability_weekend.sh` had the same trap bug. Grep for the pattern, not the
  cited line, and fix the whole class in the same commit.
- 2026-08-23 (remediate): before claiming a fix, check whether an OPEN PR
  already addresses it from a live incident (`gh pr list`). R-183 was being
  fixed in parallel by PR #80. Say so in the PR body rather than letting the
  human discover the overlap at merge time.
- 2026-08-23 (remediate): when a fix needs a guard the repo already has, find
  the EXISTING mechanism before inventing one — R-187's Monday-morning
  false-page was already solved by `check.py`'s open-bell grace and the web's
  `RTH_ONLY_SERVICES`. But check what the existing set is actually keyed on:
  the grace hung off `BUCKETS["intraday"]`, which answers how often the
  watchdog POLLS, not whether the writer is RTH-only, so it needed a separate
  `OPEN_BELL_GRACE_SERVICES` rather than a bucket move that would have
  silently changed the check cadence too.
- 2026-08-24 (runner): the 2026-08-23 remediate fire died in `ground_truth`
  on `ssh: connect to host github.com port 22` (NordVPN blackholes 22) with
  no dead-man comment, and the new daily plist was never installed, so the
  00:00 cycle silently did not fire. `fetch_origin_with_retry` bounds the
  fetch (3 x 60 s); the runner's `~/.ssh/config` routes `github.com` via
  `ssh.github.com:443`; the plist PATH carries `~/.bun/bin`. After any
  loop change, run `setup_reliability_weekend.sh` and confirm
  `launchctl list | grep reliability-daily`.
- 2026-08-26 (audit): **markdown tables break on a raw `|` inside a finding.** Nine of 76
  rows carried one — `502|503`, `placeOrder|place_order`, a `case` pattern, an `||` fallback.
  Escape `|` as `\|` in the `where` and `text` cells at generation time, and validate by
  splitting on `(?<!\\)\|` — `line.count('|')` counts the escaped ones too and will tell you
  the fix did not work when it did. Four pre-existing rows in the frozen sections have the
  same defect; leave them alone.
- 2026-08-26 (audit): the ascending-id assertion from last week is necessary but not
  sufficient — write the validation regex as `R-\d{3}`, not `R-2\d\d`. The narrower pattern
  silently skipped R-198 and R-199 (the two highest-severity rows in the section) and still
  reported "ascending: True".
- 2026-08-26 (audit): nine subsystem walks capped at ~21 files each all finished in 5-8
  minutes with none lost to the stream watchdog, against last week's death at ~37 files. The
  cap is the load-bearing part, not the category split. Giving each walk a pre-filtered list
  of the already-filed R-### findings touching ITS files (grep the findings index by basename)
  cost one script and produced near-zero re-reports across 76 findings.
- 2026-08-26 (audit): **run the standing sweeps in the lead context and then distrust their
  scope.** Sweeps 1-5 and 7 held and sweep 6 found a real gap (`ib_execute.py` has the halt
  but no `check_order_limits`) — but sweep 7 as written compares only the DELTA's jobs against
  the two watchdog catalogs, so it never looked at `breadth-scan`, a five-minute RTH timer with
  no `SCHEDULED_SERVICES` entry at all. An agent found it. Enumerate every service name
  reachable from a `cloud/services/*.timer`, not just the ones the diff touched.
- 2026-08-26 (audit): when an agent rates something P0 on a mechanism that depends on an
  unpinned third-party default (here: whether Caddy replays a POST without `retry_match`),
  do not take the rating and do not silently drop the finding. File it one severity down with
  the contingency written into the row, and point the acceptance criteria at pinning the
  behaviour explicitly. The defect that survives verification is "a money-path invariant is
  resting on a default nobody pinned or tested", which is real regardless of how the upstream
  actually behaves.

- 2026-08-26 (remediate): **a comment that quotes the code it explains will
  satisfy or break your own source-level assertion.** This bit four times in
  one run: a `SuccessExitStatus=75` grep matched the comment saying it was
  removed; a slice keyed on `stop_services_for_transition` ended inside the
  branch comment naming that function; a `python3.13 -m venv` slice ended in
  the guard comment quoting it; a `write_text` assertion matched the comment
  naming the old call. Strip comment lines before ANY structural assertion
  over a source file — it is cheaper than rediscovering it per finding.
- 2026-08-26 (remediate): **a finding's proposed remedy can be wrong even when
  the defect is real.** R-232 asked for `--cgroup-parent=<unit>`; Docker's
  systemd driver takes a slice, not a unit path, and `test_app_runtime.py`
  already asserted that with the reason inline. R-264's "dead" bash `case`
  pattern matches (bash tokenizes alternatives). R-214's second claim named
  the wrong variable. R-251's "still resolving" window is unreachable because
  the component returns a coverage skeleton first. Test the REMEDY against the
  repo's existing assertions before writing it — the pinned test that
  contradicts you is usually right and usually says why.
- 2026-08-26 (remediate): **fix the whole class, not the cited site.** R-252
  named two refresh sites; `grep` found four. R-237/R-239/R-267 were filed
  against `reliability_weekend.sh` and applied identically to
  `testing_weekend.sh`. R-270 named the render path and the sort extractor
  repeated the expression verbatim. Budget one grep per finding.
- 2026-08-26 (remediate): **run `cloud/tests` after every task, not just at the
  end.** REL-077's 2FA change broke a cloud test that only surfaced two tasks
  later, and the cause was structural — `ib-gateway-control.sh` is a ONE-SHOT
  process, so a confirmation streak carried across calls could never confirm
  for it. Cross-suite fallout from a scripts/ change is normal here; the
  stashed clean-tree baseline diff is the only way to see it quickly.
- 2026-08-26 (remediate): a sibling-module import (`from test_caddyfile import
  ...`, `from test_run_flow_refresh_wrapper import ...`) works from the test
  directory and fails collection from the repo root, where pytest actually
  runs. `sys.path.insert(0, str(Path(__file__).resolve().parent))` at the top
  of the new file; two tasks lost a full-gate run to this.
- 2026-08-26 (remediate): 24 backlog tasks over ~17h at roughly one full gate
  per task (pytest ~5min, vitest ~1.5min) is ~2.5h of gate time alone. Run the
  two gates SEQUENTIALLY (concurrent runs starve CPU and produce phantom
  failures), and run the cheap targeted suite first — it catches most
  regressions in seconds.- 2026-08-27 (audit): **review the previous weekend's own remediation as a walk, not just via the
  sweeps.** The 2026-08-26 merge squashed 24 backlog items across 134 files, written unattended and
  reviewed only by CI. A seventh walk pointed at its five P0 fixes found that two did not hold —
  REL-070's `stage-release` still races two coverage jobs deploy blocks on, and REL-071's new
  completeness guard is blind to contract-identity corruption, which is the same fabricated-P&L
  outcome the P0 named. Four fixes were confirmed holding, which is itself worth recording. The
  standing sweeps cannot find this class: they check that a mechanism is PRESENT, not that it
  covers what the finding claimed.
- 2026-08-27 (audit): **a finding's own fix can be the next finding.** R-274, R-299 and R-319 are
  all defects in last week's remediation, and R-277 re-opens NF-8 — the REL-088 test written to
  close the catalog-parity sweep parses ExecStart with a `.py|.sh` regex that matches neither
  `python -m package.module` nor `.js`, so eight units are asserted on by nothing while the test
  reports green. When a fix ships as "a test now enforces this", audit the test's SCOPE next week,
  not its presence. Enumerate the real population in the lead context and diff it against what the
  test actually iterates.
- 2026-08-27 (audit): the auto-escape belongs at emission, not in the source strings. Hand-writing
  `\|` inside the finding text worked for the findings table but the backlog table failed on a
  `(totalQty || 1)` I forgot. Run every cell through `re.sub(r'(?<!\\)\|', r'\|', cell)` at
  generation time — the negative lookbehind makes it idempotent, so manually-escaped and
  forgotten pipes both come out right, and the 4-cell assertion then passes first try.
- 2026-08-27 (audit): **assert backlog coverage programmatically.** A set-difference between the
  emitted R-numbers and the R-numbers referenced across all backlog rows caught nothing this run,
  but it is the check that makes "46 findings, 15 tasks" trustworthy without re-reading both
  tables. Pair it with the ascending-id assertion; both are three lines.
- 2026-08-27 (audit): seven walks capped at ~15 files each all finished in 4-6 minutes with none
  lost to the stream watchdog. Three cross-walk duplicates appeared exactly where last week's
  lesson predicted — one defect reached from two directions, and one TS/Python twin that must be
  ONE finding because a fix on either side alone leaves the defect live. Also expect walks to
  DISAGREE: one filed `oldestQuoteTimestamp`'s fail-closed aggregation as a defect and another
  listed the same code as clean. The lead resolved it by reading the docstring, which states the
  intent verbatim; file the half that survives and record the rejected half in the row.
- 2026-08-28 (audit): **reproduce a P0 regression claim by EXECUTING it, not by reading it.** The
  regression walk claimed REL-094's P0 fix did not hold. Importing the fix's own test seed and
  running `realized_pnl_by_exec_id` in the lead context took one tool call and turned a plausible
  agent claim into a verified P0 with exact numbers (`strike=0` -> `{'c1': 4000.0}` against a true
  `3000.0`, no warning logged). The same move settled the catalog-parity scope claim: importing the
  test module and calling its own `_health_names_written_by` over `cloud/services/*.timer` returned
  25-of-54 exactly, matching the agent. Two ad-hoc reimplementations of that resolver first gave 54
  and then 32 — when a finding is about a test's scope, call the TEST's functions, never your own
  approximation of them.
- 2026-08-28 (audit): the delta was 24 commits / 262 files, small enough that seven walks capped at
  ~12 files each finished in 3-7 minutes with none lost to the stream watchdog. At this size the
  binding constraint is not agent capacity but DEDUPLICATION: three of the ten highest-severity
  findings were reached by two walks each (`_run_script_retrying_capacity` from connectivity and
  error-handling, the vixts route from the indicator and the standing catalog sweep, the flex
  embargo from state and connectivity). Merge before numbering, as the standing lesson says, and
  record which walks converged — a defect two independent walks reach is worth more confidence than
  one walk's P0.
- 2026-08-28 (audit): **the standing sweeps found a P1 that eight scoped walks structurally could
  not.** `exit_order_service.py` places a live GTC combo with neither `is_trading_halted()` nor
  `check_order_limits()` — the only order-placing module in the repo with neither. No walk's file
  list contained it because it is not in the delta; it is old code the sweep reached by grepping
  `place_order(` across the tree. Keep running sweep 6 over the WHOLE repo, not the diff. The
  corollary from 2026-08-26 also held again: verify reachability before rating. This one is
  launchd-installable and holds a reserved client id but appears in no `cloud/` unit or `*.sh`
  entry point, so it was filed P1-with-contingency rather than P0.
- 2026-08-28 (audit): a finding whose severity the lead RAISES needs the reasoning in the row, not
  just the number. The TWR coverage bound was filed P1 by the walk; reading `perf_twr_builder.py`
  around it showed the comment at `:1640-1643` justifies the `info` payload severity on the grounds
  that "the mirror's age is policed by the coverage bound below" — i.e. the one mechanism the
  severity defers to is the one that fails open. That sentence is what makes it a P0, and it came
  from reading 25 lines of context the walk had already cited.
- 2026-08-28 (remediate): **a second weekend loop was running full suites in a sibling clone
  the whole time** (`radon-weekend/radon-testing`, the testing-weekend cycle). Load average hit
  58; a `pytest -q` that baselines at 7m36s took over 20 minutes and had to be killed. Check
  `ps ax | grep -E "vitest run|pytest"` for OTHER clones before planning the gate cadence, and
  when one is present run the cheap targeted suite per task and batch the full gate at tranche
  boundaries — three gates for 22 tasks, not 22. Also: run pytest and vitest SEQUENTIALLY even
  across clones; one vitest file (`stale-option-quote-guard`) failed only under that contention
  and passed on isolated re-run.
- 2026-08-28 (remediate): **a finding's acceptance criteria can name a remedy the repo forbids.**
  R-341 asked for an `ExecStart` flock like `radon-db-backup.service`, but the deploy lock lives
  at `/home/radon/.radon-deploy.lock` and `cloud/tests/test_root_execution_paths.py` rejects ANY
  `/home/radon` path in a `User=root` ExecStart — the first implementation went red on exactly
  that test. Taking the lock in-process with `O_NOFOLLOW` satisfies both and closes a hole the
  ExecStart form would have left open. Same shape as last week's lesson: test the remedy against
  the repo's existing assertions first, and when a guard blocks you, read WHY before routing
  around it.
- 2026-08-28 (remediate): **the pinned test that goes red can be telling you the fix is wrong in
  DIRECTION, not just in detail.** REL-127 unified two coverage tests behind "net session qty ==
  live size", which made `test_close_then_reopen_uses_new_fill_price` fail. It was right: selling
  the overnight 25 and rebuying 25 means the 25 held now ARE today's fills, so the honest
  resolution is that BOTH mechanisms cover, not neither. A FIFO walk gives that. When a pinned
  test contradicts a conservative fix, check whether the conservative answer is actually the
  correct one before rewriting the test.
- 2026-08-28 (remediate): **a test that round-trips through `sqlite3` cannot prove a libsql
  constraint.** R-362 is "ON CONFLICT DO UPDATE command does not affect row a second time"; this
  runner's SQLite is 3.53.4, which RELAXED that restriction, so the duplicate silently succeeded
  and the test passed against the UNFIXED writer. Pin what the fix guarantees and what is
  engine-independent instead — here, the parameters of the emitted statement — and always verify
  red by stashing the source, never by reasoning that it must be red.
- 2026-08-28 (remediate): four separate test-authoring bugs cost round trips and all four are
  mechanical: `_warning(**context)` nests extras under `context` (not top level); importing
  `lib.twr_math` when the module under test imports `scripts.lib.twr_math` loads a SECOND enum
  class so every `is` check fails; `monkeypatch.setattr(server.asyncio, "sleep", lambda: asyncio
  .sleep(0))` recurses because `server.asyncio` IS the global module; and `asyncio.run` consumes
  a `time.monotonic()` call during loop setup, so a call-counting clock stub hands the wrong
  value to the code's own `started`. Advance a fake clock from inside the fake work, not by call
  ordinal.
- 2026-08-28 (remediate): **say which half of a finding you did not close, in the row and in the
  PR.** R-320's `strike=600` / `expiry='20260819'` cases are information-theoretically
  unreachable from journal rows alone, and REL-128's per-ticker in-flight dedupe was deliberately
  left out as a new shared-mutable-state surface. Both are recorded with the reason rather than
  quietly dropped, which is the difference between a BLOCKED sub-part and an unnoticed gap.
- 2026-08-29 (audit): **a walk can be right about the defect and backwards about the mechanism.** The
  control-plane walk filed the new container drop-ins as invisible to `drift_audit`. Reading
  `_live_unit_counter` in the lead context showed the opposite and worse: the live side merges
  `<unit>.d/*.conf` and the repo side does not, so the auditor goes permanently RED on all five app
  units, and the allowlist (verified: two entries, both `not-installed:radon-llm-index.*`) does not
  acknowledge them. File the verified reading and record the rejected half IN the row — "drift is
  invisible" and "drift is permanently red" have opposite fixes, and an allowlist entry would have
  been the wrong one.
- 2026-08-29 (audit): **when a fix's anti-recurrence mechanism is free text, audit the text against
  the code.** REL-114 closed NF-8 by adding `EXEMPT_UNITS` with a `parser:` / `gap:` reason per
  entry. The count genuinely improved (25-of-54 to 16-of-55) and the new assertion is legitimately
  green — but `test_every_exempt_unit_states_a_reason` checks only the PREFIX, and eight of ten
  `gap: writes no service_health row` labels are false. Two lines of Python (resolve each exempt
  unit's ExecStart, grep the target for `write_service_health`) turned a green suite into a P1. Run
  that check on every exemption list a remediation introduces, the week after it ships.
- 2026-08-29 (audit): the delta's dominant defect class was **suppression added to stop a false
  page**, five mechanisms across `probes.py`, `external_probe.py` and `data_refresh.py`, four of them
  unbounded. Two questions catch all four and neither needs deep reading: does the suppression have a
  DWELL bound (how long may this state persist before it pages anyway), and what is its ORDERING
  against the checks it precedes. `aggregate_state` has no timestamp input at all — a one-line grep
  for any clock in the module proved it. Filed as standing class NF-10.
- 2026-08-29 (audit): nine walks capped at ~12 files each finished in 4-9 minutes with none lost to
  the stream watchdog. Two walks were told to EXECUTE rather than read (the journal_realized P0 and
  the remediation regression) and both returned literal command output that settled claims a reading
  walk would have left plausible — the `strike`/`right` halves of REL-109 verified holding, the
  `expiry`-lifetime half verified NOT implemented, and REL-110's two `None` causes verified
  separated. Budget one executing walk per P0 fix under review; it is the difference between "the
  mechanism is present" and "the mechanism covers the claim".
- 2026-08-29 (audit): the backlog-coverage set difference earned its place this run — it caught that
  every `R-###` reference in the 18 backlog rows was written against the pre-numbering draft order,
  so twelve of eighteen tasks pointed at the wrong findings. Two further mechanics matter: apply the
  per-task remap SIMULTANEOUSLY through one `re.sub` callback (the corrections chained, e.g.
  R-385 to R-386 while R-384 to R-385), and keep the remap OFF the task's own id field. Also, a
  finding body containing `payload["date"]` breaks a double-quoted Python literal in the generator —
  write repo code samples with single quotes inside the table strings.

- 2026-08-29 (remediate): **a finding's remedy can be a REGRESSION the pinned tests catch, and they
  were right every time.** Four this run. R-428's "check the limits on the modify path" classified a
  `secType == "BAG"` order as `type: "combo"`, and `check_order_limits` fails CLOSED on a combo whose
  `legs` it cannot read — a `comboLeg` carries a conId, not a strike — so that shape refused EVERY
  combo modify and placement; `check_quantity_limit` is the bound actually derivable at a funnel.
  R-421's "divide the reserve by `len(indices)`" made a SINGLE-index bpi run demand a reserve sized
  for three; dividing by `len(INDEX_NAMES)` is what the finding meant. R-386 asked for
  `timeout --foreground`, which stops timeout creating its own process group and therefore defeats
  the orphan reaping the SAME finding asks for. R-402 asked to register `signals-refresh`, whose
  wrapper POSTs two scanners that each write their own already-catalogued row — a key nothing writes
  ages to stale and pages forever. Test the remedy against the repo's existing assertions first.
- 2026-08-29 (remediate): **widening a scope-limited test surfaces real gaps immediately, and they
  are in scope.** Teaching `_names_in` the bounded-stdlib writer shape (REL-141) dropped the
  unresolved set from 16/55 to 7/55 AND surfaced `flow-refresh` — an hourly RTH job that had always
  written its own health row and was in NEITHER catalog, which no scoped walk had found. Merging
  drop-ins into `_unit_texts` (REL-133) surfaced five `User=root` units with no pinned PATH. Adding
  the `place_order` tripwire (REL-145) surfaced `clients/ib_client.py`, the transport every caller
  goes through. Budget for one extra fix per widened guard; the guard finding the gap on its first
  run is the guard working.
- 2026-08-29 (remediate): the comment-quotes-its-own-code trap bit twice more, once in a test I
  wrote (`assert "infinity" not in _dropin(unit)` matched the comment explaining the removal) and
  once in the parity resolver itself — `run_flow_refresh.sh` mentions `scripts/api/server.py` in a
  COMMENT about a shed marker, so every health name that file writes was attributed to the
  flow-refresh timer. Strip comments before ANY structural scan, in test AND in source-walking code.
- 2026-08-29 (remediate): **editing the running wrapper is safe only via rename.** REL-137 rewrites
  `reliability_weekend.sh` while this very loop is executing it. Bash reads a script lazily by byte
  offset, so `Path.write_text` (truncate + rewrite of the SAME inode) can strand the live run at a
  stale offset. Every edit went through `tempfile.mkstemp` in the same directory plus `os.replace`,
  which hands the running shell an untouched old inode. The file header already says this for `cp`;
  it applies to any in-place writer, including Python's.
- 2026-08-29 (remediate): a `-k` filter is not a gate. `pytest -k "scan or gate or api or catalog"`
  matched 7645 of 8686 tests and read like a full run at a glance. When reporting a targeted result,
  report the DESELECTED count too, or the number means nothing.
- 2026-08-29 (remediate): three findings this run were closed only PARTIALLY and each says so in its
  own row — R-424's `service_health` row (no error-only catalog category exists, and a scheduled key
  for a no-cadence signal ages to stale and pages forever), R-408's browser screenshot (this runner
  clone had no `web/.env`, so the app could not boot; `setup_reliability_weekend.sh` now provisions
  it into the clone, so this residual is closed for later runs), and R-402's `signals-refresh` registration
  (deliberately refused, above). Writing the reason into the row is the difference between a known
  residual and a silent gap.
- 2026-08-30 (audit): **the operator can consume R-numbers outside this loop.** REL-149/REL-150 were
  written by the operator on 2026-08-29 citing R-429…R-431 in commit messages, `cloud/CLAUDE.md` and
  the log, with no findings row. `grep -o "R-[0-9]{3}" RELIABILITY_AUDIT.md | sort -u | tail -1` says
  R-428 and would have collided. Take the max over BOTH documents plus `git log --grep 'R-[0-9]'` since
  the anchor, skip the consumed ids, and say so in the section header and the ledger line.
- 2026-08-30 (audit): when the range holds both weekend loops' remediation merges AND the operator's
  own fixes, splitting "at the last commit that touched RELIABILITY_LOG.md" picks the operator's
  commit and hides the reliability remediation inside the feature half. Split by branch ancestry
  instead: `git log <anchor>..HEAD ^origin/reliability/<prev> ^origin/testing/<prev>` is the feature
  set, and hand the executing regression walk the operator's REL rows too.
- 2026-08-30 (audit): **an ad-hoc call of a test's resolver is not the test's iteration.** The lead
  called `_health_names_written_by(<service path>)` over `cloud/services/*.timer` and got six
  exempt-but-resolving units; the walk that ran the test's own `_timer_backed_services` got zero,
  and `test_every_exempt_unit_still_lacks_a_resolvable_name` already rejects a resolving exemption.
  The 2026-08-28 lesson says call the TEST's functions — it also has to be the test's INPUTS. Run
  the test file, then reuse only what it exports at module scope with the same arguments it uses.
- 2026-08-30 (audit): the executing regression walk found all three PARTIALs (REL-132, REL-150 and
  the REL-149 socket mode) with SCRATCH cases the shipped tests did not cover — "release also
  fails", "one previous unit is down", "who can write the socket". The pattern is that an incident
  fix's test pins the branch the incident exercised. Give the regression walk one explicit
  instruction per fix: name the case the shipped test does not cover and run it.
- 2026-08-30 (audit): a `git diff --name-only` of 232 source files collapsed to ~110 once the 130
  `route.ts` files touched only by a two-line export were set aside (`git diff --numstat` per file,
  keep > 10 lines). Check for a mechanical sweep commit before sizing the walks; the capability
  export itself was audited in the lead with one grep over the sensitive routes.
- 2026-08-31 (audit): **when the merged nightly branches have been deleted remotely, split by the merge
  commits' second parents, not by branch name.** `^origin/reliability/<prev> ^origin/testing/<prev>` from the
  2026-08-30 lesson silently excludes nothing once those refs are gone; `git log <anchor>..HEAD ^<merge>^2
  ^<merge>^2` (find the merges with `git log --merges --first-parent`) gives the same feature set and does
  not depend on branch retention. Also: another loop's remediation can land as a SQUASH (7c627f30, #198), so
  it is in the feature set by ancestry — hand it to the executing regression walk, not a feature walk.
- 2026-08-31 (audit): a `.md` finding-count check must anchor on `\n## Delta audit <date>\n`, not the bare
  heading string — the ledger line quotes the heading inside backticks, so a plain `index()` finds the
  ledger first and the ascending-id assertion runs over the whole document (it failed on R-048 here).
  Write the doc, then re-validate with the anchored slice.
- 2026-08-31 (audit): the permanent drill suites live in TWO directories — `scripts/tests/` and
  `scripts/tests/test_monitor_daemon/` (`test_exit_orders_ack.py`, `test_exit_orders_guard_durability.py`,
  `test_fill_monitor_degraded_session.py`) plus `scripts/tests/test_watchdog/test_snapshot_unavailable.py`.
  An executing walk reported the ack drill as "does not exist" after `ls scripts/tests | grep`; use
  `grep -rl` over the tree before accepting a "missing test" claim, and verify it in the lead.
- 2026-08-31 (audit): a week that lands a host split produces P1s that are all one shape — a mechanism that
  worked on one host relied on something only that host had (its own lock file, its own env file, its own
  systemctl). Give the walk covering a topology change an explicit question per shared-state file the
  pre-split code read (`/health` lock state, `RADON_HOST_ROLE` source, `installed-units` role strip) and
  ask "which host reads this now, and from where"; five of this week's P1s fall out of that question.
- 2026-08-31 (audit): nine walks capped at 10-14 files finished in 4-8 minutes with none lost to the stream
  watchdog, while five loops ran full suites on the same host. Two walks told to EXECUTE returned literal
  outputs (`select_gates(...)` results, libsql claim races, `place_order` call counts) that settled four
  fixes as HOLDS and produced four P2s from the uncovered cases — the "name one case the shipped test does
  not cover and run it" instruction paid for itself again.
- 2026-09-02 (audit): **when the previous backlog has missing REL rows, read the rolling issue's comments
  for that date BEFORE theorizing.** The 09-01 cycle's 8 remediate rounds all died in ~40 seconds on
  "You're out of usage credits" (subscription exhaustion) — one `gh issue view` explained both the missing
  09-01 ledger line and ten un-started tasks (REL-175…178, REL-181…186). Un-started tasks roll into the
  NEXT remediate; do not re-file them as findings. Corollary: rounds have no quota-aware backoff, so a
  quota outage burns the whole round budget in seconds — the wrapper defect is filed, but the triage move
  (issue forensics first) stands regardless. Also this run: a one-tool-call CPython repro in the lead
  (`ThreadPoolExecutor` atexit join, exit 124 under `timeout`) turned a walk's strongest P1 claim into
  CONFIRMED — executing the cheap repro beats rating a plausible mechanism.
- 2026-09-03 (audit): `github_pr_output.py` uses `--issue` verbatim as the title and truncates at ~250
  chars mid-word. Keep `--issue` to one or two short clauses (it is also the PR title); the detail
  belongs in the rolling-issue comment, not the flag.
- 2026-09-03 (remediate): **a file-level autouse fixture can stub the exact method your new test
  targets, and the test passes vacuously.** `test_fill_monitor.py` autouse-stubs
  `_mirror_ib_orders_snapshot` to a no-op; the REL-212 guard test "passed" pre-fix and its control
  cases failed instead. When adding tests to an existing file, read its fixtures FIRST and restore
  the real method in a class-scoped fixture. Same run: patching `scripts.api.routes.streaks` while
  the app runs `api.routes.streaks` is the dual-module import trap in a second wardrobe — patch the
  exact module path the app imports.
- 2026-09-03 (remediate): the widened-guard lesson held twice more — the REL-186 per-function
  placement tripwire found an unguarded `modify_order` on its first run, and the REL-114
  catalog-parity test went red the moment REL-178's refactor hid the `"ib-watchdog"` literal from
  its resolver (fixed by keeping the literal at the transport call). Budget the extra fix; the
  guard going red on your own change is the guard working.
- 2026-09-03 (remediate): REL-210's process-lifetime error latch leaked across unrelated pytest
  tests sharing the interpreter and surfaced as an order-dependent red two tasks later. A
  process-scoped latch in production code needs a conftest autouse reset the same commit it ships.
- 2026-09-04 (audit): **zsh does not word-split an unquoted variable, and the loop-squash exclusion
  failed silently because of it.** `for c in $LOOPS` passed the whole string as ONE argument and
  `git show` died with "ambiguous argument"; the file list that came back looked plausible (128
  files) and was simply the unsplit range. The repo's own CLAUDE.md warns about this for download
  loops. Write the shas as a literal list in the `for`, and sanity-check the split by printing each
  squash's file count -- 134/24/1/14/68 immediately showed which commits were loop output. Same
  family: `grep --include=*.py` needs the glob QUOTED or zsh tries to expand it and reports "no
  matches found" for every sweep. Five of the seven standing sweeps returned empty on the first
  try for that reason alone, which reads exactly like "the mechanism is gone".
- 2026-09-04 (audit): **the loop's own dead-man is auditable and this is where the P0s were.** Both
  P0s are in `nightly_issue_prune.py` / `report()`, shipped by `d396eacc` eight days after the
  prune was introduced to reduce issue scrollback. The walk that found them was the only one
  pointed at the wrapper scripts, and the decisive evidence was a six-line stub `gh` in `/tmp`:
  making `pr list` exit 1 produced `pruned 1/1 comments`, rc 0. Budget one walk per cycle at the
  loop machinery itself, and prefer a stub-binary repro over reading the fail-open path -- it took
  one tool call and turned a plausible reading into a confirmed P0.
- 2026-09-04 (audit): the delta was small enough (28 feature commits, 44 source files) that the
  binding constraint was neither agent capacity nor dedup -- there were ZERO cross-walk duplicates
  this run, against three or more in each of the previous four audits. What replaced dedup as the
  main lead-side work was SEVERITY ARBITRATION: two walk ratings were raised (R-608, R-609) and
  both raises came from asking the same question -- does the mechanism this suppression defers to
  actually cover the failure it hides? Write the arbitration into the row, not just the number.
- 2026-09-04 (audit): the executing regression walk's instruction "name one case the shipped test
  does not cover and RUN it" produced four PARTIALs from four reviewed fixes, a 100% hit rate, and
  two of them were P1s. The recurring shape is now explicit enough to hand the walk directly: an
  incident fix pins the branch the incident exercised, so test the ADJACENT branch -- return-1
  covered but not raise (REL-210), raise covered but not the False that the module actually
  returns (REL-209), fully-empty covered but not truncated (REL-212), stock covered but not option
  (REL-211). Ask "what is the other way this input arrives" per fix.

- 2026-09-04 (remediate): **the permanent drill list in step 4 names a suite that does not exist.**
  `test_daemon_bounded` matches nothing under `scripts/tests` (`grep -rl` over the whole tree finds
  no such file); the closest real files are `test_unbounded_io_bounds.py` and `test_ib_insync_bounded.py`.
  A `pytest` invocation listing it exits 4 before running anything, so a drill run that "failed" may
  simply have a bad path in it. Resolve every drill path with `grep -rl` BEFORE the run, and treat a
  rc-4 collection error as a list defect, not a regression.
- 2026-09-04 (remediate): **a finding's proposed remedy was wrong three times, in three different
  ways, and the repo told me each time.** R-597 asked for a `--head` prefix filter on `gh pr list`;
  GitHub search has no prefix form for head refs, and a search that misses a real PR PRUNES — the
  opposite of the fail-closed the finding wanted (a truncation bound gives the same guarantee).
  R-614 claimed calendar-day arithmetic made detection SLIP; it is the reverse — calendar days are
  larger than sessions, so the bug was false pages over weekends, and the session count is the fix
  either way. R-622 asked to reject the request when the registry is unreachable, but FastAPI being
  down during first-run setup is exactly the branch the offline path exists for, so rejecting wedges
  onboarding; a static id mirror plus a parity test closes the hole without breaking first-run.
  Read what the remedy would DO to the branch the code is defending before writing it.
- 2026-09-04 (remediate): five pinned tests contradicted a fix this run and every one was
  informative, not obstructive: three `next_attempt_at` cases pinned the exact suppression R-615
  narrows (rewritten per-branch, plus a NEW rate-limited case that keeps the original assertion
  alive), the JWKS throttle case pinned the global cooldown R-620 makes per-kid, and a fixture set
  `_jwks_refresh_after = 0.0` — a float where the fix needs a map, which surfaced as a 503 in an
  unrelated case. When a per-key refactor lands, grep the TEST fixtures for the old scalar too.
- 2026-09-04 (remediate): the wrapper-contract assertions are the fiddliest part of a five-loop
  change. `body.index("run_round")` finds the FUNCTION DEFINITION, not the call, and only two of the
  five loops factor the round loop into a function at all — anchor an ordering assertion on the
  `claude -p "/<loop>` invocation, searched from the arm point forward, and it holds across all five.
  Same shape as the comment-quotes-its-own-code trap: assert on what executes, not on what the file
  happens to contain first.
- 2026-09-04 (deliver): **a wrapper contract change breaks every harness that stubs the tool it now
  reads, not just the one the fix touched.** REL-188 made a phase OK only on commit evidence
  (`git rev-parse HEAD` + `log --format=%ct`); four separate test files stub `git` as a silent
  `exit 0`, so every phase in them read as uncommitted and returned 75. CI surfaced them in two
  rounds because the first round's log grep was capped at 20 lines — read the FULL `FAILED` list
  (`grep '^FAILED'` over the downloaded job log, then `uniq -c`), not the head of it, or you pay a
  second 3-minute CI cycle per missed file. Corollary: `gh run view --log-failed` returned EMPTY
  for every failing job on this repo; `gh api .../runs/<id>/logs` into a zip and `unzip -j` the one
  job's txt is the reliable path.
- 2026-09-04 (deliver): the deliver phase ran from a clone sitting on `main`, so the first three
  greps for the fix under review found nothing and read as "the code is not there". Check
  `git branch --show-current` BEFORE reading any code the branch changed; a stash-and-checkout
  moves in-progress edits over cleanly, but only if the mismatch is caught early.
- 2026-09-04 (deliver): the docs contract's owner-doc requirement is satisfied by prose that must
  be TRUE. Two of three paragraphs written from the PR body's summary were accurate; the third
  described `setupToken.ts` as reporting an unreadable store when it actually added a TTL. Read the
  diff of each changed mapped file before writing its doc line, not the PR body's account of it.
