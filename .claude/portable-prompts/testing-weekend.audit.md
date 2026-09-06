# testing-weekend — audit phase (portable prompt)

You are running as a NON-INTERACTIVE agent CLI. There is no human to ask: a
question asked here is a night lost. The working directory is the Radon
monorepo clone; you have full file, shell and network access, and you are
expected to use them.

Execute the **audit** phase of the manual below, and only that phase.

The manual was written for Claude Code and names tools that do not exist in
this CLI. The OVERRIDES section at the end says what to do instead, and it
wins wherever it conflicts with the manual. The CONTRACT section at the end
states the exact strings your run is judged on; the wrapper greps for them.

---

# Testing Weekend Loop

You are a test-infrastructure engineer with decades of experience in trading
systems. This skill runs UNATTENDED — no human can answer questions. The
standard is the one set by the 2026-08-07 audit (`TEST_AUDIT.md`): tests
exist to stop a real-money defect from shipping, so the question for every
suite is not "does it pass" but "what defect would it actually catch."

The mode is the first argument: `audit`, `remediate` or `deliver`. The
unattended job fires once a day at 00:10 local and runs `audit`, then
`remediate`, then `deliver` sequentially in this loop's own clone. The loop
never merges; the human merge is the deploy trigger.

## Hard rails (both modes — violating any of these is a failed run)

1. **Never touch the IB Gateway.** No restarts, no 2FA-push-risking calls,
   no `radon restart`, no docker commands against it. Tests use fakes/mocks
   only — never a live IB connection, never a live order.
2. **Never push to `main`.** All changes land on a branch
   `testing/<YYYY-MM-DD>` and a PR. The human merge is the deploy
   trigger.
3. **Never run against the operator's working clone.** Refuse (exit
   nonzero, say why) unless BOTH `.radon-weekend-runner` and
   `.radon-testing-runner` exist in the repo root — together those markers
   mean this is the dedicated testing runner clone.
4. **Respect the frozen contracts.** `TEST_AUDIT.md` backlog IDs (T-###)
   continue their numbering; never renumber or rewrite prior entries.
   `TEST_LOG.md` is append-only. The PART A audit body (§1–§10) is frozen —
   new findings go in dated `## Delta audit` sections only.
5. **Never weaken a test to go green.** Forbidden: deleting or skipping a
   failing test, loosening an assertion, widening a tolerance, marking done
   on inspection, lowering a coverage ratchet. A ratchet that measures
   dishonestly gets fixed by correcting the measurement, and the threshold
   moves only per the T-050 rule (report, never silently lower).
6. **Bounded per phase, complete overall.** The wrapper enforces a
   wall-clock cap per phase. Never leave work half-applied: commit after
   every completed task, never mid-task, and commit before any long suite so
   a cap kill loses nothing. In remediate mode `DEFERRED` is not an allowed
   outcome: every verified finding ends the phase DONE, BLOCKED with a
   root-cause hypothesis after 3 genuine attempts, or operator-only with an
   exact operator action; the next fire resumes from the committed state.
7. **Stay off the reliability loop's lane.** The reliability loop
   (`/reliability-weekend`) runs in its own clone (`~/radon-weekend/radon`);
   this loop runs in `~/radon-weekend/radon-testing`. Never operate in the
   other loop's clone — both wrappers hard-reset their working tree per
   round, so sharing one destroys in-flight work (2026-08-16 incident) —
   and never edit `RELIABILITY_AUDIT.md` / `RELIABILITY_LOG.md`. Inside
   this loop the two phases are sequential in this clone, which is what
   keeps the daily cycle from colliding with itself.

## Mode: audit (first phase of the daily cycle)

Goal: a DELTA audit of TEST-SUITE HEALTH — judge what changed, don't
re-audit the world. Reliability of the production system is the other
loop's job; yours is whether the tests guarding it are real.

1. Read `TEST_AUDIT.md` §Audit ledger for the last audited SHA. Compute
   the changed surface: `git log --stat <last-sha>..HEAD`. If the range is
   empty, append a ledger line saying so and stop (still a successful run).
2. Read `TEST_LOG.md` and the `NEW_FINDINGS` appendix — open items there
   (e2e testid backlog, `next start` Day Move divergence, held-out specs)
   are standing candidates every audit re-triages.
3. Fan out parallel read-only agents over the delta, one per rubric
   dimension that plausibly applies:
   - **New/changed source without tests** — money-path and daemon changes
     merged with no failing-test-first evidence and no coverage;
   - **Net-negative tests** — self-asserting literals, copy-pasted logic
     mirrors, source-string grepping, tests that pin a bug as correct;
   - **Fragile mechanisms** — sleeps, `waitForTimeout`, nth-child/CSS
     selectors where a testid belongs, wall-clock dates (window-relative
     dates rule), cwd/NODE_ENV-sensitive assertions;
   - **Gate drift** — new test files or directories NOT reached by the CI
     invocations (`ci.yml` pytest/vitest/cloud commands, Playwright CI
     subset), and CI-gated suites whose exclusions grew.
   Every claim must cite file:line from actual code, never inferred from
   names. Scope agents to the diff plus its blast radius, not the tree.
4. Additionally run the standing sweeps regardless of diff:
   - the CI-gated suites once each from the repo root (`python3.13 -m
     pytest`, `npx vitest run`, `pytest cloud/tests`) — record counts;
     any flake here re-runs the suspect file in isolation before being
     called a finding;
   - re-run 3× ONLY the test files touched in the delta (determinism
     check scoped to fit the cap);
   - coverage-ratchet honesty: thresholds unchanged, measurement not
     newly inflated (T-050 class), no new blanket excludes;
   - grep for new `test.skip` / `it.skip` / `pytest.mark.skip` /
     `xfail` introduced in the delta without a linked T-### or issue.
5. Dedupe against ALL existing T-### findings. Append genuinely-new
   findings to `TEST_AUDIT.md` under a dated `## Delta audit <date>`
   section (cite file:line, severity P0/P1/P2, continuing T-numbers) and
   add backlog rows with red/green acceptance criteria. Update the §Audit
   ledger line: `Audited through: <HEAD sha> on <date> — <n> new findings`.
6. Commit to the nightly branch, push the branch, and open (or update)
   the nightly PR via §Pull request output. Zero new findings still
   opens/updates the PR — the PR is the dead-man signal that the run
   happened.

## Mode: remediate (second phase of the daily cycle)

Goal: work EVERY verified un-DONE finding from this cycle's audit in
severity order (P0, then P1, then P2), then older stragglers, exactly by
the PART B contract:

**Remediate mandate.** Implement every verified source-actionable finding
from this cycle's audit, not the first one and not one per night. Group fixes
by root cause into separate commits on one dated branch `testing/<YYYY-MM-DD>` (one
branch per loop per day; the deliver phase turns it into one PR). Red/green
per fix; the full project gates before every commit. Independent fixes may
run in parallel as subagents in separate worktrees of this clone
(`git worktree add ../wt-<id> -b testing/<date>-<id> testing/<date>`), each
committing to its own branch; this phase merges them back onto the dated
branch, reruns the gates on the merged result, and removes the worktrees
(`git worktree remove`, `git branch -d`). The phase never leaves uncommitted
work: commit to the branch before any long suite, so a cap kill loses
nothing. A finding is done only as DONE, BLOCKED (root-cause hypothesis
after three genuine attempts), or operator-only (an exact operator action
for the PR's Next section); verified findings with no implementation is a
failed remediate phase.

1. Check out the nightly branch (create from `origin/main` if the audit
   phase produced nothing; then this run only re-verifies gates, step 4).
2. Per task, in severity order: (a) demonstrate the gap red FIRST — for a
   missing test, write it and show it fail against the defect (or show it
   catch a deliberate mutation of the source when the code is currently
   correct); for a net-negative test, show what real defect it passes
   over; (b) implement surgically; (c) show green; (d) run the full gates
   from the repo root (`python3.13 -m pytest`, `npx vitest run`, and
   `pytest cloud/tests` when units/cloud files changed); when the task
   changed UI, also run the relevant `web/e2e` spec in the worktree and
   attach the screenshot: the clone-copied `node_modules` is what makes
   that possible, and CLAUDE.md does not accept unit-only evidence for
   UI; (e) append the TEST_LOG.md row with red/green counts; (f) commit
   with the T-### id.
   Source-code fixes are in scope ONLY when a test correctly fails
   against a real defect the audit identified — fix the defect, keep the
   test; never the reverse.
3. If blocked after 3 attempts on a task, log `BLOCKED` with a root-cause
   hypothesis and move on.
4. Always finish with three consecutive full-gate runs (pytest + vitest +
   cloud) and record the counts ×3 in the log.
5. Push the branch; rewrite the PR via §Pull request output. DONE/BLOCKED
   tables and gate counts ×3 go on the rolling issue. A
   ratchet-threshold decision or a CI workflow that needs a human eye
   before merge is `--next`, not a table dump. CI on that PR is the deliver
   phase's job (§Mode: deliver).

## Mode: deliver (third phase of the daily cycle)

Goal: every commit the remediate phase landed on `testing/<YYYY-MM-DD>` reaches the
operator as ONE pull request with CI green, in this same cycle, and the
operator is told exactly what is ready to merge. The loop never merges.
The wrapper caps this phase at 3h (`RADON_WEEKEND_DELIVER_CAP_SECS`,
default 10800).

1. Resume first. Read this loop's deliver record
   (`python3.13 scripts/nightly_deliver.py show --loop testing`; kept outside the clone under `~/radon-weekend/.testing-deliver/`).
   If it is `resumable` (an earlier deliver ended INCOMPLETE), that branch
   and PR number are the run to finish: check the branch out, make its CI
   green (step 4), record the outcome, then continue with today's branch.
   Never open a second PR for a branch that already has one.
2. Push the dated branch. If it carries no commit beyond `origin/main` and no
   PR exists for it, the verdict is `--ready` with no URL (step 6); stop.
3. Open ONE PR for the branch via §Pull request output (`--loop testing`);
   update the existing PR when one is already open for the branch (`gh api
   -X PATCH`). Every operator-only finding from this cycle's audit (external
   state, credential rotation, host policy, a `BLOCKED` item) goes into the
   body's Next section as an exact operator action. Nothing is dropped
   silently. Record the PR:
   `python3.13 scripts/nightly_deliver.py record --loop testing --branch <branch> --pr <n> --url <url> --status pending`.
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
   `python3.13 scripts/nightly_deliver.py verdict --loop testing --ready <url>...`
   (or `--incomplete <check> --pr-url <url>`). The wrapper greps it:
   `NIGHTLY DELIVER READY: loop=testing prs=<n> <urls>` becomes the operator
   notification "N PR(s) green, ready to merge: <urls>" (Pushover and the
   dead-man comment); `NIGHTLY DELIVER INCOMPLETE: loop=testing check=<name>
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
never freehanded. Pass `--loop testing`, `--date`, `--issue` (what went
wrong, as one bullet per finding: `- **Component**: what happened.`), `--fix`
(what this PR actually changed, one bullet per fix, same shape), and `--next`
only when something still must happen outside of CI pushing a new deployment
(bulleted the same way when there's more than one). Omit `--next` and the
formatter emits `Fixed with green deployment`. A single plain sentence still
works when there is exactly one finding.

The body has exactly three sections, in this order: **Issue discovered**,
**What was done to fix it**, **Next**. Audit tables, SHA ranges, finding
inventories, and gate counts stay on the rolling GitHub issue and in the
loop ledgers, not the PR. Title shape: `Testing <date>: <plain-language
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
labeled `testing-nightly`, a Pushover notification per phase carrying the
status and the nightly PR link when one exists, and the PR itself.

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

`INCOMPLETE (agent exited 0 without committing to the nightly branch)` is
the status the wrapper posts when `claude -p` returned 0 but no commit landed
on the nightly branch during the phase (T-379): treat it exactly like
TRUNCATED — the phase's draft work, if any, is under `/tmp/tw-<date>/` and
the next phase must land it. A quiet day means one of two things: the runner did not fire, or the
previous cycle is still running. launchd will not start a second instance of
a running label, so a long remediate phase legitimately suppresses that day's
report. Check `launchctl list | grep radon` before treating quiet as dead.
This loop's worst case is one 2h audit plus one 6h remediate plus one 3h
deliver, so it always clears the next 00:00 fire.

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

- **2026-08-16 (audit):** start by checking the runner clone is CLEAN, before
  anything else. This run opened on orphaned WIP from a prior capped run —
  three modified files plus an untracked test importing a module that does not
  exist — which aborted pytest at COLLECTION (exit 2, zero tests run) and would
  have been misread as a red gate. Park it recoverably
  (`git stash push --include-untracked -m "<loop>-<date>: parked ..."`), never
  discard it, never commit it, and record the stash ref in the audit so the
  operator can recover it. Gate counts are only meaningful from a clean tree.
- **2026-08-16 (audit):** do NOT run the determinism re-runs concurrently with
  each other. Running vitest ×2 alongside pytest ×2 on one machine is what
  surfaced T-062 — which was genuinely useful, but the skill's rule ("re-run
  the suspect file in isolation before calling it a finding") is what separated
  the real race from contention noise. Run the gates serially for the counts,
  then deliberately re-run under load if you want to hunt races.
- **2026-08-16 (audit):** the "re-run 3× ONLY the delta-touched test files"
  rule does not scale to a week-sized delta. This one touched 263 of
  `web/tests` and 100 of `scripts/tests` — effectively the whole suite — so
  scoped re-runs collapsed into full-gate runs. Say so in the audit rather than
  pretending the scoping happened.
- **2026-08-16 (audit):** `rg` on this runner resolves to BSD `grep` (no
  `--glob`, no `-N`), and the rtk proxy mangles piped `grep` output. For any
  non-trivial scan of a large diff, write the patch to a file and parse it with
  a `python3.13` heredoc — that is what produced the trustworthy zero-new-skips
  result.
- **2026-08-16 (remediate) — NEVER run `git pull` on this runner, and push
  after EVERY task commit.** The rtk hook rewrites bare `git` commands, and its
  `git pull` rewrite did `reset --hard origin/<branch>` + a rebase onto
  `origin/main` while printing "Already up to date." That silently discarded 14
  unpushed remediation commits (T-055…T-069 — 29 files, +1296 lines). The same
  filter then served STALE `git log` / `git status` output, so the loss stayed
  invisible for several tool calls; it surfaced only because a baseline vitest
  run reported a `.pi` suite that T-058 had already fixed. Rails:
  - Use `rtk proxy git …` for every git command in this loop. Bare `git`
    output on this runner cannot be trusted for state decisions.
  - Never `git pull`. Sync with `rtk proxy git fetch origin` plus an explicit
    `merge --ff-only` you chose deliberately.
  - `rtk proxy git push` the nightly branch immediately after every task
    commit, not once at the end. The branch on origin is the only durable copy;
    "push at the end of the run" is a single point of failure.
  - Recovery if it happens anyway: `rtk proxy git reflog` still holds the
    orphaned tip. Tag it first, then
    `git rebase --onto <rebased-base> <old-base> <orphan-tip>` and
    `git branch -f`.

- **2026-08-17 (remediate): never pipe a gate run through `tail` alone.**
  Round 1 of the closing 3x gate reported `10 failed | 6706 passed` and the
  names were gone — the command kept only the summary line. Seven further
  full runs (four sequential, two deliberately concurrent with a full
  pytest) were all `6716 passed`, so the round could not be named or
  reproduced and had to be logged as an observation rather than a finding.
  Write the full reporter output to a file per gate run and read the tail
  from that file, so a flake round is nameable the first time it happens.
- **2026-08-17 (remediate): `pytest cloud/tests` is red on macOS on
  `origin/main` too.** Diff the failure LIST against a clean `origin/main`
  worktree before treating any cloud red as yours; the count alone is not a
  signal. Baseline as of this run: `10 failed, 848 passed, 4 skipped`, then
  attributed to `sha256sum`. **That attribution is STALE as of 2026-08-29**
  — `/opt/homebrew/bin/sha256sum` exists on this host and no `sha256sum`
  red remains. The current darwin baseline is `37 failed`: 13 in
  `test_bootstrap_control_plane.py` (`exec {fd}<>` is bash 4+ and
  `/bin/bash` here is 3.2, so it exits 127), 21 in
  `test_ib_gateway_control.py` (`operator-radon.sh` uses `mapfile`, bash
  4+), 3 in `test_caddy_edge_timeouts.py` (no `caddy` on PATH).
  `setup_testing_weekend.sh` now checks both and names the consequence;
  installing either MOVES this baseline, so re-record the FAILED list in
  the same run.
- **2026-08-17 (remediate): pre-flight a spec under `next start` before
  curating it into CI.** The e2e job builds and serves a production
  server, and this repo has a documented dev-vs-prod divergence. Every
  spec added to the curated list this run was verified under
  `PLAYWRIGHT_WEBSERVER_CMD="npx next start"`, which is also how
  `performance-twr-payload.spec.ts` was caught as permanently red before
  it could red the job.
- **2026-08-22 (audit): last weekend's remediation lands inside this week's
  delta.** The ledger SHA is the audit HEAD, not the merge of the nightly PR,
  so the range `71de8a33..HEAD` re-contained T-055…T-079 and the reliability
  loop's REL-0xx source commits. Re-triage them as ordinary delta rather than
  exempting them (two findings this run — T-086, T-087 — were on REL-038
  tests), and say in the audit that the range overlaps.
- **2026-08-22 (audit): the darwin cloud baseline is a LIST, not a count, and
  it moves.** Round 1 read `12 failed` against a recorded baseline of 10; the
  diff of `FAILED` lines against the 2026-08-17 list is what separated two
  new environment-shim reds (T-088) from the known ten. (The environment
  cause has since changed from `sha256sum` to bash 3.2 + missing `caddy`;
  the list is 37 today.) Always `sort` the
  `FAILED` lines to a file and `diff` them; update the recorded baseline in
  the audit whenever it changes.
- **2026-08-22 (audit, second pass): CHECK `origin` FOR AN EXISTING WEEKEND
  BRANCH BEFORE YOU START, not at push time.** Two runs of this loop audited
  the same range on the same day on different hosts. The second only
  discovered the first when `git push` was rejected — after it had already
  numbered 32 findings from T-080, colliding with all 17 the first had
  pushed. Do this in step 1, right after the clean-tree check:
  `git fetch origin && git rev-parse --verify origin/testing/<date>`.
  If it exists, read its audit section FIRST and continue numbering after it.
  Recovery if you find out late: never force-push over the other run. Reset
  onto its tip, drop your duplicates, renumber the rest from its highest
  T-number, and append a `## Delta audit <date> (second pass)` section — the
  frozen-contract rail permits a new dated section, never a rewrite. Record
  the convergences in a table; two independent readers landing on the same
  file:line is real evidence, and throwing it away is a loss.
- **2026-08-22 (audit): cross-references written into the sweeps / re-triage
  prose go STALE while you are still drafting.** Three references in the
  first draft ("Filed as T-096", "Promoted to T-095", "see T-094") were
  written against early draft numbers and silently pointed at three unrelated
  findings by the time the section was numbered. Number the findings FIRST,
  then write the prose that cites them — or grep every `T-\d{3}` in the
  finished section and confirm each one resolves to the subject you meant.
- **2026-08-22 (audit): verify the RUNNER TOOLCHAIN before trusting a red
  gate, the same way you verify the tree is clean.** One round reported
  `107 failed` and every failure was "async def functions are not natively
  supported" — the venv (then the shared `~/radon-weekend/venv`; now this
  loop's `~/radon-weekend/venv-testing`) had pytest but no
  `pytest-asyncio`, which only CI installs. The same tree was `7216 passed`
  once the plugin was in. `node` was also absent from the agent's PATH until
  `~/.nvm/versions/node/<v>/bin` was prepended (the wrapper exports it, but a
  Bash-tool shell re-reads the profile). Do this before the gates:
  `python3.13 -c "import pytest_asyncio"`, `node --version`,
  `ls node_modules/.bin/vitest`. Fix the environment, never the repo, and
  record the install in the audit.
- **2026-08-22 (audit): attribute a red cloud gate by RUNNING the base SHA.**
  Building on the first pass's "baseline is a LIST, not a count": a
  `git worktree add /tmp/... <last-audited-sha>` plus a `diff` of the sorted
  `FAILED` lists settles it in two minutes and byte-identically, and it also
  catches the case where the list is longer for a reason unrelated to the
  delta — this host reads 34, not 12, because it has no bash >= 4.
- **2026-08-22 (audit): a source change can make an UNTOUCHED test
  date-dependent — sweep the diff's blast radius, not the diff.**
  `f2fbe0a7`/`d45849d7` added an `isIbDailyPnlCurrent()` wall-clock gate to
  `MetricCards`; two e2e specs the delta never opened now false-red every
  weekend (T-117). Nothing in the changed-test list would have surfaced it.
  After cataloguing changed tests, ask the inverse question: which EXISTING
  tests does this source change now describe differently?
- **2026-08-23 (remediate): an absent audit phase does NOT mean "only
  re-verify gates".** This cycle's audit never ran (PR #75 had merged the
  2026-08-22 findings at 11:17 and no 2026-08-23 branch existed), but the
  backlog still held 20 un-DONE P1s from T-081…T-109. Step 1's "create from
  `origin/main`, then only re-verify" applies when the backlog is EMPTY;
  otherwise create the branch and work the newest non-P2 stragglers exactly as
  if this run's audit had filed them. The remediation bullets go under a
  `## Remediation <date>` section in `TEST_AUDIT.md` and a dated table in
  `TEST_LOG.md`.
- **2026-08-23 (remediate): fan the backlog out to one worktree per task
  group; cherry-pick back serially.** `git worktree add --detach /tmp/...`
  plus an APFS clone copy of `node_modules` (`cp -Rc <clone>/node_modules
  <wt>/node_modules` and the same for `web/`; fall back to `cp -R` off APFS)
  gives each subagent a clean tree; the loop venv needs nothing. NEVER
  symlink `node_modules`: a symlink out of the worktree root breaks BOTH
  gates. vitest cannot resolve `@rollup/rollup-darwin-arm64` through the
  link's real path, and Turbopack hard-fails the Playwright webServer with
  `Symlink [project]/web/node_modules is invalid, it points out of the
  filesystem root`, so the worktree cannot run e2e at all. `cp -Rc` is
  copy-on-write, so it costs seconds and near-zero disk. Group findings that
  touch the SAME test file into one agent (T-082+T-097, T-084+T-099,
  T-086+T-098 here) or the cherry-picks conflict. The main clone stays
  untouched, so a baseline gate can run there while the agents work, and
  each `cherry-pick -n` + docs row + push is one durable commit. Sixteen
  P1s landed in ~15 minutes of wall clock this way versus one-at-a-time.
- **2026-08-23 (remediate): a subagent's "green" is scoped; re-read the
  source diff before landing.** Two things the per-task reports could not
  show: (a) the relay is ESM with socket side effects on import, so T-087's
  builder was never executed by the relay in any test — verify by hand that
  the variables the extracted call uses (`freshness`) are in scope at the
  call site; (b) `_read_deploy_evidence` gained a `now` kwarg (T-103) and its
  second caller lived in `grok_page_responder.py`, outside the agent's
  scoped run. Grep every caller of a changed signature in the LANDED tree,
  not the worktree.
- **2026-08-23 (remediate): the darwin cloud baseline grew by three
  environment-class reds without any test being wrong.** At `4985a7f8`
  this host reads 12; at `2e904678` it reads 15 because
  `test_refresh_control_plane.py` was new in the delta. (The `sha256sum`
  cause named at the time is stale; as of 2026-08-29 the baseline is 37 and
  the cause is bash 3.2 + missing `caddy`.) Same rule as the audit lesson: sort the
  `FAILED` lines, run the base SHA in a worktree, `diff` — and record the
  new list in the log so the next run does not misattribute it.
- **2026-08-23 (remediate): two hosts remediated the same branch at once —
  the 2026-08-22 "check origin first" lesson is necessary but not
  sufficient for REMEDIATE.** Both runs fetched at pre-flight, found no
  branch, and created it; the second host's first push was rejected, it
  reset onto this host's tip (correctly) and started from the BOTTOM of the
  P1 list — which this host had already fanned out in parallel, so T-100,
  T-106, T-108, T-109 were still at risk of being done twice. Rails:
  push the EMPTY branch immediately after creating it (this host did, and
  that is what made the second host detect the collision); before EVERY
  landing, `rtk proxy git fetch origin` and rebase onto the remote tip with
  `rtk proxy git rebase` (never force-push, never `git pull`); keep the
  per-task landing script inserting rows ABOVE any other host's section
  in `TEST_LOG.md` so the two tables do not interleave; and list every
  landed T-### in the PR body as soon as it lands, because the PR body is
  the only channel the other host reads. A `TEST_LOG.md` conflict on
  rebase is expected; resolve it by keeping both sections, never by
  dropping a row.
- **2026-08-23 (remediate, second host): a CLAIM COMMENT on the PR is what
  actually de-conflicts two live runs.** The "check origin first" rail did not
  fire here — `origin/testing/weekend-2026-08-23` did not exist at pre-flight
  and appeared before the first push — so the first two tasks (T-081, T-109)
  were done twice and thrown away. What stopped it was posting a comment on
  the nightly PR naming the exact T-### items this host would take, BEFORE
  starting them; zero collisions across the five that followed. Do it as soon
  as the branch exists: list the items, say which end of the list you are
  working from, and re-`fetch` before every landing.
- **2026-08-23 (remediate, second host): a duplicate task is not wasted if you
  DIFF the two answers.** Both hosts fixed T-081; comparing the two
  implementations is what found that the landed one keys precedence on
  `report_date` alone, which drops a second account's mirror-only row.
  Reset onto the other host's tip, drop your commit, then probe their fix with
  YOUR test cases before moving on. That was the only surviving product change
  from this host's first hour.
- **2026-08-23 (remediate): `rtk` is not installed on every runner.** The
  2026-08-16 lesson mandates `rtk proxy git …`; on this host `rtk` is not on
  PATH at all and bare `git` is correct and trustworthy. Check
  `command -v rtk` at pre-flight and follow that rail only where the proxy
  actually exists — otherwise every git call fails with exit 127 and the run
  looks blocked.
- **2026-08-23 (remediate): check `uptime` before calling a vitest round red.**
  One full gate read `13 failed / 7169 passed`, 11 of them bare
  `Test timed out in 5000ms` across nine unrelated files, with the run taking
  336 s instead of ~110 s. Load average was 66 (`corespotlightd` at 367% CPU).
  The nine files were 44-passed in isolation and the next full run was
  7182 passed in 107 s. Capture the reporter output to a file, name the files,
  re-run them in isolation, and record the load average alongside the counts.
- **2026-08-23 (remediate): a new tree-walking contract test must be timed, not
  just made green.** The first draft of the inverted table-overflow contract
  built a fresh RegExp against the whole ~1 MB `globals.css` for every class
  token of every ancestor of every tag: 4.5-6 s against vitest's 5 s default,
  so it flaked 8/8 on a TIMEOUT rather than an assertion. Precompute the
  stylesheet side once and re-run the new file 3x checking the reported
  duration, not only the pass count.
- **2026-08-23 (remediate): inverting a net-negative contract surfaces PRODUCT
  defects — budget for filing them, not for fixing them.** Turning the
  table-wrapper test from "named wrappers must be styled" into "every table
  must have an overflow ancestor" produced six real horizontal-overflow bugs
  (T-121). Fixing them is six UI changes needing 390px browser verification,
  which is outside a test-quality task. Pin them in a named list under an
  EQUALITY assertion — so a seventh reds immediately and fixing one reds until
  its entry is removed — and file the finding. Do NOT skip them, and do NOT
  quietly widen the rule until they pass.
- **2026-08-25 (audit): when CI's test invocation changes shape, diff COLLECTION, not
  pass counts.** `424e66da` sharded pytest into shell globs (`test_[a-c]*.py`) that
  cannot match a directory; CI stayed green while 752 tests in two subdirectories
  stopped running. `pytest --collect-only -q` over the full tree vs the union of the
  CI path sets, `sort -u` on the file names, `comm -23` — two minutes, and it is
  the only check that sees a silent drop. Pull the per-job pass counts from CI
  (`gh api repos/{owner}/{repo}/actions/jobs/<id>/logs`; `gh run view --job --log`
  returns empty on this host) and compare the shard SUM to the last unsharded run.
- **2026-08-25 (audit): agent-reported findings need a lead spot-check before
  filing, and it is cheap.** Six agents returned ~45 candidates; every one the
  lead re-read at the cited line held, but two summaries overstated a mechanism
  (an `apply_` stamping claim that needs a `contract.secType` the agent's repro
  omitted). Reproduce the top P1 in-process from the cited file (a 10-line
  python heredoc), read the cited lines of every P0/P1, and only then number.
- **2026-08-25 (remediate): this runner has no `rtk`, no `setsid`, and no
  `pytest-xdist`.** Bare `git` is the only git here and its output was
  trustworthy (the 2026-08-16 rtk lesson applies only where rtk is installed —
  check `which rtk` first). `pytest-xdist` is CI-only like `pytest-asyncio`
  was: install it in this loop's venv (`~/radon-weekend/venv-testing` — the
  legacy shared `~/radon-weekend/venv` is unused since the per-loop split)
  before verifying anything under
  `-n auto --dist loadfile` (a new shard is only proven with CI's flags).
  `setsid` does not exist on darwin: detach a long job with
  `subprocess.Popen(..., start_new_session=True)`, never `nohup setsid`.
- **2026-08-25 (remediate): the closing 3× gate does not fit the Bash tool's
  600 s cap.** One serial round (pytest ~275 s + vitest ~300 s + cloud
  ~185 s) already exceeds it and a backgrounded tool call is still killed at
  the cap. Run the rounds from a detached script that writes one file per
  gate run plus a done marker, and arm a Monitor on the marker — do not
  chain nine background calls. Also `setopt nullglob` before any
  `rm -f pattern-*`: zsh aborts the whole line on a non-matching glob and the
  launch that followed silently never happened.
- **2026-08-25 (remediate): a red that SIGKILLs the runner is not a usable
  red.** The T-127 reproduction (`run_module` spawned in the caller's process
  group, then `killpg` on timeout) killed pytest itself on the first attempt.
  When the defect under test is "signals the wrong group", record the signal
  call instead of delivering it (patch `os.killpg`), assert on the recorded
  pgid, and keep a real child so the pgid observation stays honest.

- **2026-08-26 (audit): a squash-merged nightly PR leaves its branch looking
  orphaned — check the CONTENT, not the ancestry.**
  `git merge-base --is-ancestor origin/testing/weekend-2026-08-25 HEAD` said
  NOT MERGED and the branch read 15 commits ahead of `main`, which looks
  exactly like a remediate phase that pushed after its PR closed. It was a
  squash merge: PR #90 landed as the single commit `e690c85b` and every one of
  those 15 commits' changes is on `main`. Settle it by grepping for the actual
  fix (`grep -n scripts-daemons .github/workflows/ci.yml` for T-122's ninth
  shard), not by ancestry. Do this before writing "N commits are stranded on
  origin" into an audit.
- **2026-08-26 (audit): a "checked and clean" from one agent does NOT refute a
  positive finding from another — their scopes differ, and the lead has to
  re-derive from source.** The blast-radius agent concluded "no weekend
  false-red found" for the new `PositionTable` wall-clock gate; the
  fragile-mechanisms agent found one at
  `account-day-move-ib-daily-pnl.spec.ts:239`. The fragile agent was right, and
  the reason the other missed it is instructive: it looked for a spec asserting
  "the Today P&L cell" and `:239` asserts over `wulfRow.locator("td")` — the
  whole row. Two reads of `positionUtils.ts:656` and `PositionTable.tsx:507-508`
  settled it in one tool call. When two agents' territories overlap, treat a
  negative as "did not find", never as "is not there", and always spend the
  one call to check the cited line yourself.
- **2026-08-26 (audit): a diff of a CI gate's SHAPE hides a change to who
  ENFORCES it.** The shard-union check (the 2026-08-25 lesson) came back clean
  — 466/466 py, 30/30 cloud, and the CI shard pass counts summed to the local
  recursive total exactly. The regression was one level up: `deploy.needs` had
  quietly dropped `web-coverage` and `py-coverage`, and
  `gh api repos/{owner}/{repo}/branches/main/protection` returns no
  `required_status_checks` key at all, so the ci.yml comment's "deferred to
  required workflow job status" pointed at a mechanism that does not exist.
  Add to the standing sweeps: dump `deploy.needs` + `deploy.if` at BOTH the
  base SHA and HEAD and diff them, then check branch protection over the API.
  Collection coverage and gate enforcement are two different questions.

- **2026-08-26 (remediate): `gh pr edit --body-file` ABORTS on this repo** with
  `GraphQL: Projects (classic) is being deprecated … (repository.pullRequest.projectCards)`
  — and it aborts AFTER printing nothing else, so it reads like a success until
  you re-read the body and find the old text. The PR body is one of the three
  dead-man channels, so a silent no-op here is exactly the failure this loop
  exists to prevent. Use the REST path instead:
  `gh api -X PATCH repos/{owner}/{repo}/pulls/<n> --input <json-with-body>`,
  then VERIFY with
  `gh api repos/{owner}/{repo}/pulls/<n> --jq '.body' | grep -c '<a phrase you just wrote>'`.
  `gh pr comment` and `gh issue comment` are unaffected.
- **2026-08-26 (remediate): 12 concurrent worktree agents drove load average to
  179 on this host, and that makes every agent-reported count untrustworthy.**
  The fan-out itself was the right call — 17 P0/P1 findings landed in roughly
  35 minutes of wall clock — but six of the twelve agents independently hit
  slow or timing-shaped test behaviour, and one stalled outright. Two rails:
  (a) cap the fan-out at about 6 concurrent agents on this machine and run a
  second wave, rather than launching all groups at once; (b) treat every agent
  "green" as SCOPED evidence only, and re-run the landed change's own tests in
  the main clone after each cherry-pick. Doing that caught nothing wrong this
  run, but it is what makes the closing gate a confirmation rather than a
  discovery. Never run the closing 3x gate until the fan-out is fully drained.
- **2026-08-26 (remediate): SUPERSEDED by the `cp -Rc` rule at the fan-out
  bullet above. Kept for the symptom.** A worktree with symlinked
  `node_modules` cannot start vitest on this host: `@rollup/rollup-darwin-arm64`
  resolves relative to the symlink's real path and is absent, so vitest dies at
  startup. Three agents hit it independently and each worked around it by
  installing that one binding to a scratch dir and setting `NODE_PATH`; do NOT
  do that any more, clone-copy `node_modules` instead, because the `NODE_PATH`
  patch rescues vitest but leaves e2e dead, which is worse because it looks
  green. The main clone is UNAFFECTED (verified with a scoped `npx vitest run`
  before trusting the closing gate) and nothing in the repo was modified.
  Always smoke-test vitest in the MAIN clone before concluding the suite is
  broken.
- **2026-08-26 (remediate): two agents branched from the same base can land
  CONTRADICTORY pins — diff the landed tree, not the two reports.** T-162
  wrapped `run()` in the credit-spread and IEI/HYG producers; T-163, working
  in a sibling worktree from the same base, added an AST class contract whose
  `_UNGUARDED_CTOR_BASELINE` pins both of those modules as unguarded. Read
  together the two reports look like a direct conflict that would red the suite
  after both land. They do not conflict — the baseline entries are
  `fetch_uw_closes`, a different function from the `run()` T-162 wrapped — but
  the only way to know is to RUN the contract in the landed tree (22 passed).
  Whenever two task groups touch the same module from separate worktrees, run
  the second one's contract test immediately after the second cherry-pick,
  before moving on.
- **2026-08-26 (remediate): resume a stalled agent, do not take over its task.**
  One agent stopped with `Staged. Waiting on the full-suite confirmation before
  committing.` — it had done ~95k tokens of work and staged everything, but a
  full-suite run under load-179 was never going to return. `SendMessage` to its
  agent id resumed it from its own transcript; the reply told it to run only
  the SCOPED set, that the lead owns the closing full gate, and to commit and
  report. It finished correctly in one round. Re-doing that task in the main
  clone would have thrown away all of that context. Say explicitly in the
  ORIGINAL prompt that the lead owns the full gate and the agent must never run
  one — that is what caused the stall.

- **2026-08-27 (audit): the two loops keep separate CLONES but SHARE `/tmp` —
  namespace every scratch file.** Mid-run, `/tmp/delta_section.md` (this
  audit's drafted findings) was silently OVERWRITTEN by the reliability loop's
  own draft: REL-numbered content, same generic filename, different clone. It
  surfaced only because an integrity check reported the five P0 findings as
  "missing" from a file that had been 71 KB a moment earlier. `/tmp/gates/`
  had the same problem — it already held files from the other loop that looked
  like yesterday's run. Nothing in the repo was touched and no finding was
  lost, but the P0 block had to be rewritten from the agent reports. Rails:
  put ALL scratch under `/tmp/tw-<date>/` (and the reliability loop under
  `/tmp/rel-<date>/`), never a bare `/tmp/<generic>.md`; and when an integrity
  check says content vanished, re-read the file before assuming your own bug.
- **2026-08-27 (audit): BSD grep here does not support `\|` alternation, and it
  fails SILENTLY.** `grep -n 'T-190\|T-194' file` returned zero hits on a file
  that contained both, which briefly read as confirmation that the content was
  gone. The existing lesson covers `--glob` and piped mangling; add this. Use
  `grep -E`, or parse in a `python3.13` heredoc — which is what settled it.
- **2026-08-27 (audit): a concentrated red is not load flake — check the FILE
  distribution before invoking the load rule.** The pytest gate came back
  `7 failed / 8153 passed` under load average 21, which fits the 2026-08-23
  load-flake profile exactly. It was not: all 7 were in ONE file and reproduced
  7/7 in isolation in 2.26 s. The tell is distribution — the 2026-08-23 flake
  was 11 timeouts across NINE unrelated files. Scattered + timeout-shaped means
  load; concentrated + AttributeError-shaped means real. It was real (T-237,
  `main` red), and treating it as flake would have shipped an audit that said
  the tree was green.
- **2026-08-27 (audit): when a gate is red, ask whether CI agrees BEFORE
  attributing it to your host.** Two commands settled T-237 end to end:
  `gh run list --json headSha,conclusion` filtered to the HEAD sha (failure),
  then `gh run view <id> --json jobs` for which job (`pytest (scripts-npsz)`)
  and what the gate did about it (`Deploy to VPS` skipped). That last part is
  the finding's real weight — the gate HELD, so the story is not "CI missed it"
  but "main has been red and undeployed since". A darwin-only red would have
  shown CI green; this showed CI red, which is a different and more urgent
  report.

- **2026-08-27 (remediate): a P0 may already be fixed in an OPEN PR — check
  before you write a line of code.** The audit's headline (T-237, `main` is
  red) was already fixed by open PR #109, correctly and more completely than
  the audit described: the audit named one of THREE causes, and renaming the
  constant alone still left the file 7-red (the ladder stubs take one argument
  where `_fetch_closes_via_ladder` takes three, and a second constant did not
  exist either). Fifteen minutes of duplicate work was avoided by
  `gh pr list --state open` at pre-flight and reading the one PR whose title
  matched the finding. Add to step 1, next to the origin-branch check:
  `gh pr list --state open --limit 20` and grep the titles against the P0 list.
  Then say so explicitly in the log, the PR body and the claim comment — a
  finding left undone for a good reason has to be distinguishable from one
  that was missed.
- **2026-08-27 (remediate): converting a skipped-or-grepping test into a real
  one is the highest-yield work this loop does, because the thing it was not
  testing is often BROKEN.** Two of this run's conversions found live
  production defects, not test gaps: running the caddy edge tests for the first
  time (T-205) proved `cloud/caddy/Caddyfile` does not adapt at all — both
  installers gate on `caddy validate`, so R-219/R-220/R-258 were never in force
  at the edge while 14 text-regex assertions shipped green; and executing this
  loop's OWN dead-man forensics (T-209) proved `report()` is defined below the
  prologue that calls it, so every prologue death was silent. Budget for the
  fallout: prefer the finding whose AC is "make this actually run" over the one
  whose AC is "add a case", and verify the fallout yourself rather than
  trusting the agent — a public release binary in `/tmp` settled the Caddyfile
  question in two commands, both directions.
- **2026-08-27 (remediate): two agents off the same base WILL collide in one
  file, and the resolution is always "keep both", never "take one side".**
  Two conflicts this run. (a) T-197 and T-232 both converted the same
  grep-to-behaviour test; T-197's version was strictly richer (three extra
  tests), so it was kept — but T-232's unique contribution was asserting the
  row's MESSAGE, not just its state, and that was merged in rather than
  dropped. (b) T-198 added a `docker_body` parameter to a fake `docker` while
  T-232 added `RADON_TEST_MISSING_TAGS` to the same stub. Keeping both is not
  enough: the merged stub failed only `manifest inspect` for a missing tag, so
  T-198's new local-store fallback resolved it and T-232's `:latest` fallback
  was never reached — one test red on landing. The fix was to make the FAKE
  honest (a tag missing from the registry is missing from the local store too),
  which is a fixture correction, not a weakening. Always run the second
  agent's own tests immediately after the second cherry-pick.
- **2026-08-27 (remediate): `str.format()` is applied to shell-stub heredocs
  in this repo's cloud tests — a literal `{` in bash is a format field.**
  Adding `if { [ "$1" = a ] || [ "$1" = b ]; }` to a fake `docker` turned
  1 failure into 15, because `.format()` tried to interpret the brace group.
  Use a brace-free test (`[ "$1" = a -o "$1" = b ]`) or double the braces.
  The symptom — a small edit exploding the failure count — is the tell.
- **2026-08-27 (remediate): fix the load-class flake BEFORE the closing gate,
  not after.** `portfolio-startup-performance-contract.test.ts` (T-238, filed
  P2 for a 41ms margin) hard-timed-out twice consecutively **in isolation** at
  load average 42 while verifying an unrelated agent's work. Left alone it
  would have redded the closing 3x gate and cost a round of re-runs to
  attribute. A P2 that will red your own verification is worth promoting for
  the duration of the run. The honest fix was `vi.setConfig` per the T-161
  pattern, with the measured numbers written into the comment — not the AC's
  suggested `beforeAll` hoist, which would not have helped because the cost is
  a dynamic import, and a `beforeAll` only moves that under `hookTimeout`.
- **2026-08-27 (remediate): a claim comment plus a running landed-table on the
  PR is what makes an 8-agent fan-out legible.** Posting the claimed T-### list
  BEFORE starting, then editing a cumulative table onto the PR after every
  landing, meant the PR was a truthful dead-man signal at every moment of the
  run rather than only at the end. `gh pr comment` works on this repo;
  `gh pr edit --body-file` still aborts on the Projects-classic GraphQL
  deprecation, so the body goes through
  `gh api -X PATCH repos/{owner}/{repo}/pulls/<n> --input <json>` and is
  verified with a grep for a phrase you just wrote.

- **2026-08-29 (audit): check `git status` AFTER the gates, not only before.**
  The clean-tree rail is a pre-flight check in this skill, but a gate run can
  DIRTY the tree: `scripts/api/tests/test_flow_report_capacity_shed.py` (new in
  the delta) POSTs `/flow-analysis/JOBY` without redirecting
  `server._FLOW_REPORTS_DIR`, so every run writes `data/flow_reports/JOBY.json`
  into the checkout — reproduced isolated in 0.56 s, `3 passed`, file recreated
  every time (T-275). It surfaced only because a mid-run `git status` (run to
  confirm the read-only agents were behaving) showed an untracked path that was
  not there at pre-flight. Two rails: run `git status --porcelain` after each
  gate and attribute anything new to the file that wrote it; and treat a test
  that writes into the repo as a P1, not housekeeping — the loop's own
  clean-tree precondition is what it breaks.
- **2026-08-29 (audit): "green in the full suite, red in isolation" is a real
  shape — do not assume isolation is the clean signal.** The standing
  `orders-place-cache-race.test.ts` item had been recorded as cross-file
  pollution on the strength of "6-passed ×3 in isolation". At HEAD it is
  `1 failed | 5 passed` on one isolated run and `6 passed` on the next, always
  the same case, while the FULL vitest gate passed it — the full run's
  scheduling happens to be kinder to a `vi.waitFor` polling for an in-flight
  route handler (T-311). When a finding's diagnosis rests on an isolation run,
  run it at least twice; one green in isolation proves nothing about a race.
- **2026-08-29 (audit): probe the installed dependency, do not read the driver
  docs.** The headline P0 (T-250) is `getattr(result, "rows_affected", 0)`
  against a libsql Cursor that exposes only `rowcount`. Two agents found it
  independently, but what made it filable in one tool call was ten lines of
  python against the PINNED build — `connect(':memory:')`, insert twice, print
  `dir(cursor)` and both `rowcount`s. Any finding of the form "this code reads
  an attribute that is not there" should be settled that way before it is
  written down, never from the source alone.
- **2026-08-29 (audit): run the gates BEFORE the fan-out, or after it drains —
  not alongside it.** Launching six audit agents concurrently with round 1 drove
  load average from 74 to 224 (`corespotlightd` was already at 589% CPU), which
  made pytest take 870 s instead of ~275 s and produced three timing-shaped reds
  that all needed isolation re-runs to attribute. The reds were correctly called
  as load — scattered across three files, timing-shaped, CI green at the same
  SHA — but the attribution cost several rounds. The agents are read-only and
  cheap to start late; the gate is the thing whose numbers have to be quotable.
- **2026-08-29 (audit): running on a Saturday makes the weekend-false-red class
  LIVE, and that is worth keeping.** `f7b5eeb9` — the newest commit in the
  delta, authored by the grok responder hours before this run — pins
  `web/tests/account-metric-modal.test.ts:165-170` to a Friday session because
  the unpinned test redded vitest shard 7 on Saturday 2026-08-29 and blocked the
  deploy. That is T-117 / T-248 recurring for a third time, and this audit saw
  it as a fact in the git log rather than as a hypothesis. Note the day of the
  week in the audit preamble; it changes what the gate run is evidence of.

- **2026-08-30 (audit): when a gate flips red with NO source or test change in
  the range, diff the ENVIRONMENT, and start with the files the setup script
  copies.** Twenty-two pytest reds appeared in files untouched since May;
  `git log` on every module in the import chain was empty. The cause was
  `web/.env` — provisioned into this clone by the delta's own `14065b74`
  (`cp -p`, so its mtime still read Aug 24 and looked old) — and a `load_dotenv`
  spy on `import cash_flow_sync` named the loader in one run. Two rails:
  (a) `stat -f '%SB'` (birth time) on any `.env`-class file, not `ls -l`; (b)
  prove an env-dependence claim in BOTH directions before filing it
  (`TURSO_DB_URL= TURSO_AUTH_TOKEN= pytest <files>` → green; unmasked → red),
  and yesterday's gate output under `/tmp/tw-<date-1>/gates/` is the cheapest
  proof that the tree, not the host, is what changed.
- **2026-08-30 (audit): one detached script per collection ROOT for the added-
  file determinism runs.** `pytest cloud/tests/x.py scripts/tests/y.py` in one
  invocation dies at collection with `ImportPathMismatchError: tests.conftest`
  (both roots own a `tests/conftest.py`), so the 3× run reported nothing and
  looked like a hung job. Split by root — it is also why CI runs them as
  separate jobs.
- **2026-08-30 (audit): the reliability loop's gates WILL overlap yours, and
  its vitest alone takes this host to load 250.** Round 2 of the full gate was
  launched at load 4.5 with only two light agents left; nine minutes later the
  other loop's `npx vitest run` started in `~/radon-weekend/radon/` and the
  round's vitest came back `3 failed`, all bare timeouts, 22/22 green in
  isolation. Before calling any round's number, `ps -eo pcpu,args | grep
  radon-weekend/radon/` — if the other clone is mid-gate, the round is a load
  sample, not a verdict, and the isolation re-run is the number to quote.
- **2026-08-30 (audit): a delta TEST can create the artifact that reds an
  UNTOUCHED test.** `test_next_clerk_guard.py`'s `"pk_live_" + "fixture" * 4`
  dodges gitleaks and the source-level scan, but CPython constant-folds it into
  the `.pyc` written at collection, and `test_integration.py`'s "tracked files"
  walker reads `__pycache__`. CI never sees it because the two files are in
  different shards. Add to the after-gate sweep: when a cloud red names a path
  under `__pycache__`, `node_modules` or `.next`, the finding is the walker,
  not the environment — and check whether the shard split is what keeps CI
  green.

- **2026-08-31 (remediate): a text-only reply ENDS the run — every response
  in this loop must carry a tool call until the phase is finished.** The
  audit phase drafted 33 findings to `/tmp/tw-2026-08-31/findings.md`,
  launched its gates from a detached script, and then answered a mid-run
  "say what you're doing" nudge with a sentence and no tool call. Print mode
  read that as the end of the turn: `claude -p` exited 0 after 18 minutes with
  zero commits, no PR, and `phase_status` said OK on all three dead-man
  channels while the cloud gate was still running (T-379, now INCOMPLETE).
  Two rails: (a) when you are only waiting, wait INSIDE a tool call — a
  foreground `until`/poll loop under the 600 s cap, or a `run_in_background`
  waiter — never by replying with text; (b) the remediate phase must check
  `/tmp/tw-<date>/findings.md` and `logs/testing-weekend/audit-<stamp>.log`
  at pre-flight when `origin/testing/<date>` sits at `origin/main` with no
  commits, and land the draft under `## Delta audit <date> (landed by the
  remediation phase)` before touching the backlog. The draft is the audit's
  work product; re-auditing the range would have cost two hours and thrown
  away six agents' convergences.
- **2026-08-31 (remediate): a "coverage gap" P1 on an auth gate was a live
  bypass — read the fall-through, not only the guard.** T-346 was filed as
  "the `:873` predicate is stubbed open in every test"; the agent's first
  un-stubbed wire test returned 200 with no bearer at HEAD, because the
  middleware withheld the loopback bypass and then called `verify_clerk_jwt`,
  which re-grants the same loopback trust. The guard was correct; the code
  path AFTER the guard undid it. When a finding says "this line is untested",
  write the wire test against HEAD before assuming green, and read what the
  request reaches once the guard says no.
- **2026-08-31 (remediate): an agent probing a Gateway route in DOCKER mode
  will try to start the stack — pin `GATEWAY_MODE=cloud` in the PROMPT, not
  only in the committed tests.** A T-346 recon probe of `/ib/restart` ran with
  the clone's default mode and attempted `docker compose up` on this laptop
  (daemon not running, nothing started; the 2FA push-lock file it created
  was released by the probe's own `/ib/reset-backoff`). Rail 1 was brushed,
  not broken, and only because Docker was down. Any prompt that hands an
  agent an `/ib/*` or `radon-ib-gateway.service` route must say: stub
  `remote_gateway_action` / `control_unit`, set `GATEWAY_MODE=cloud`, never
  call the route un-stubbed.
- **2026-09-02 (remediate): the T-379 exit recurred VERBATIM with the rail in
  the prompt — detection is not prevention, and the fix that sticks is
  structural.** Tonight's audit again answered a mid-run nudge with text at
  "gates 53%" and exited 0 at four minutes; the wrapper correctly posted
  INCOMPLETE (T-379's check works) but the night's audit was still forfeit
  (T-380 filed: the wrapper should retry an INCOMPLETE phase once; not yet implemented). Two things that
  worked and should repeat: the remediate phase ADOPTED the dead audit's
  still-running detached gates script as its round 1 (zero wasted wall
  clock), and `/tmp/tw-<date>/` scratch survived the phase boundary exactly
  as designed.
- **2026-09-02 (remediate): two subagents stalled by ENDING THEIR TURN to
  "wait" for a build/suite — put the anti-wait rail in every agent PROMPT,
  not just this skill.** The 2026-08-26 resume-don't-redo lesson worked both
  times (SendMessage got each back on track with context intact), but the
  stall is now a pattern: any prompt that can involve a long build or
  detached run must say "wait inside a bounded foreground poll; never end
  your turn to wait; the lead owns the full gates."
- **2026-09-02 (remediate): grep the gate logs for the runner's own exported
  secrets before calling a round harvested.** T-381: a test that subprocesses
  a wrapper snippet WITHOUT `env=` printed the runner's live PUSHOVER_* into
  the pytest gate log, because the wrapper exports them to page and the
  snippet prefers env over file by design. The red was also invisible in
  isolation (93 passed) — it only fires under the wrapper. After each gate
  run: `grep -c "$PUSHOVER_TOKEN"`-style checks on the gate output for every
  secret the wrapper exports, and treat any hit as a P1 test-isolation
  finding.

- **2026-09-04 (audit): `github_pr_output.py --issue` does NOT accumulate across
  repeated flags — it keeps the LAST one.** Passing `--issue` five times
  produced a PR body with a single bullet and a title taken from the fifth,
  which reads exactly like a correctly-formatted one-finding night. The same
  applies to `--next`. Pass ONE multi-line string per flag, with the bullets
  separated by real newlines, and always print the generated body before
  `gh pr create` — the PR is a dead-man channel, so a silently truncated body
  is the failure this loop exists to prevent (same class as the 2026-08-26
  `gh pr edit --body-file` silent abort).
- **2026-09-04 (audit): a bounded wait loop that RUNS OUT is not a completed
  wait — check the sentinel before you read the artifact.** The base-SHA cloud
  attribution used `for i in $(seq 1 16); do ... sleep 20; done`, which expired
  while pytest was still at 54%. `grep '^FAILED' cloud-base.log` therefore
  returned zero lines, and `comm -13` against a 37-line HEAD list reported all
  37 reds as NEW IN THIS DELTA — a five-alarm result that was pure artifact.
  The tell was that neither "BASE DONE" nor "GONE" printed. Always branch on the
  sentinel explicitly after the loop and refuse to diff when it is absent; an
  empty FAILED list from a red run is impossible and should be treated as such.
- **2026-09-04 (audit): the sibling loop's gates cost this phase its second
  round.** With the reliability loop mid-gate in `~/radon-weekend/radon`
  throughout, pytest took 2617 s (43 min) against a usual ~275 s — nearly 10x —
  which alone consumed most of the cap and forced round 2 to be abandoned. The
  2026-08-30 lesson says to check `ps` before trusting a round's numbers; extend
  it to PLANNING: check for the sibling at pre-flight, and when it is already
  running, budget for one round only and say so in the ledger rather than
  starting two and killing one. One honest round plus a base-SHA diff is worth
  more than two contended rounds.

- **2026-09-04 (remediate): `nohup env -i` for the detached gate script is a
  TRAP — a minimal `PATH` reds 75 tests that have nothing to do with your
  changes.** The closing 3x gate came back `75 failed`, concentrated in the
  weekend-wrapper, loop-launcher and mTLS gateway-remote suites: with
  `PATH=/usr/bin:/bin` there is no `node`, no `openssl`, no `launchctl`, and
  those suites shell out to all three. The same 8 files were `257 passed` in a
  normal shell. The detach rail is right; the minimal env is not. Export the
  full `PATH` inside the stage script (venv bin, nvm node bin, `/opt/homebrew/bin`,
  then the system dirs) and put the venv on `PATH` directly rather than
  `source`ing `activate` and then overwriting `PATH` after it — the second
  ordering silently gives you the system python and `No module named pytest`.
  Both mistakes cost a full round each.
- **2026-09-04 (remediate): a new guard can fire on the very branch that adds
  it, and that is the guard working.** T-438's "a changed held-out e2e spec
  needs a dated ledger annotation" redded on landing, because a sibling agent
  had modified both named specs the same day against a previous-day stamp. The
  correct response was to re-stamp the annotations with the `next start`
  evidence that agent had actually captured, not to loosen the date comparison.
  Land guards that read the branch diff LAST, after the changes they will judge.

- **2026-09-04 (deliver): a CI red the remediate phase never saw can be the
  remediate phase's own work — attribute by AUTHORSHIP, not by `git diff
  --name-only | grep`.** Three shards redded. Two were a pre-existing
  whole-second `date` granularity bug in the shed ladders that only samples red
  under load, correctly fixed here. The third (`test_rel178_...`) was read as
  "untouched by this branch" because a `git diff --name-only origin/main..HEAD |
  grep rel178` printed nothing — but `git log origin/main..HEAD -- <file>` named
  the branch's own commit as its author. The file existed on main; the branch
  ADDED a class to it, and diff-name matching had already been satisfied
  upstream. Use `git log <base>..HEAD -- <path>` to attribute, never a name
  grep, before writing "pre-existing" anywhere.
- **2026-09-04 (deliver): a test that passes locally and fails on CI with an
  errno is a PLATFORM-default test, not a flake.** The watchdog cert cycle
  acquired the real 2FA lease, which resolves to per-user Application Support on
  macOS and `/var/lib/radon/ib-lease` on Linux. Every new test that drives a
  REAL cycle (rather than patching around it) inherits every host path that
  cycle touches; grep the branch's new tests for the entry point and confirm
  each one redirects the paths it will reach. That sweep is what proved this was
  the only unguarded `run_cycle` on the branch.
- **2026-09-05 (audit): when the delta touches `package.json`/`bun.lock`, run the
  dependency install BEFORE the vitest gate.** A new `thinking-orbs` dep merged
  in-range was absent from this clone's `node_modules`, so the first vitest round
  read 13 failed + 34 files failed at import — one cause, zero repo defects, a
  full round of attribution lost. Pre-flight: `git diff <base>..HEAD --name-only
  | grep -E 'package.json|bun.lock'` → if it hits, `bun install --frozen-lockfile`
  in each affected project first. Also: `bun install` in `site/` repeatedly died
  extracting the `next` tarball on this host; `npm install --prefix site` worked —
  and it rewrites `package-lock.json` via bun's earlier migration, so
  `git checkout -- site/package-lock.json` after.

- **2026-09-05 (audit, second pass): the `.radon-testing-runner` marker was
  ABSENT and the skill's rail says refuse — but the WRAPPER self-heals on the
  canonical path.** `scripts/testing_weekend.sh:517-526` admits
  `~/radon-weekend/radon-testing` once and `touch`es the marker; only a
  non-canonical path refuses. A directly-invoked `/testing-weekend` never runs
  that code, so the rail as written would abort every manual run in the correct
  clone. Resolve it the way the wrapper does: compare `pwd -P` against
  `$HOME/radon-weekend/radon-testing`, stamp the marker on a match, refuse
  otherwise — and say in the report that you stamped it.
- **2026-09-05 (audit, second pass): 39 vitest FILES failed with only 14 failed
  TESTS — that ratio IS the signal, and it is an environment read every time.**
  The first pass had already hit this same morning (`thinking-orbs` missing),
  yet it recurred because the clone's tree reset wiped `node_modules` again
  while `package.json` was UNCHANGED in the range — so the 2026-09-05 pre-flight
  rule ("check whether the delta touched package.json") did not fire. Better
  rule: whenever failed-FILES greatly exceeds failed-TESTS, grep the vitest log
  for `Failed to resolve import` / `Cannot find package` BEFORE attributing
  anything, and re-run only the failed set after installing. Here that turned a
  39-file red into `323 passed / 0 failed` with the repo untouched. Also: `bun`
  is at `~/.bun/bin/bun`, not on the default PATH; `npm install --no-audit
  <pkgs>` in `web/` worked and leaves an untracked `web/package-lock.json` that
  must be deleted to keep the tree clean.
- **2026-09-05 (audit, second pass): the darwin cloud "baseline" is a PATH
  artifact, not a host property — filed as T-484.** Ledger entries have carried
  10/12/34/35/37/33 for weeks, attributed to bash 3.2. This run's gate script
  put `/opt/homebrew/bin` ahead of `/bin`, resolving `bash` to 5.3.9, and the
  cloud gate read **5 failed** — all of them `test_caddy_edge_timeouts.py` with
  `caddy` absent. The 2026-08-22 rail ("the baseline is a LIST, not a count")
  is necessary but insufficient: record the RESOLVED `bash --version` and
  `command -v caddy` next to the FAILED list, or the list itself is not
  comparable run to run.
- **2026-09-05 (audit, second pass): read-only agents can safely run CONCURRENTLY
  with the gates if the prompt forbids running suites.** The 2026-08-29 rail
  (gates before the fan-out, never alongside) exists because agents that ran
  test suites drove load to 224. Four agents given an explicit "NEVER run a test
  suite, npm, bun, a build, pytest or vitest — grep and read only" rail ran for
  the whole pytest gate at load 3-6, and the gate's 1730s was uncontended. The
  mechanism to remove is the agents' SUITE RUNS, not their existence; that
  recovered roughly 30 minutes of otherwise-idle cap.


---

# OVERRIDES — read these as amendments to everything above

These win over the manual on every conflict.

1. **No subagents, no fan-out, no worktree swarm.** The manual's `Task` tool,
   `Agent` tool, `Workflow` tool, subagent dispatch and parallel worktree
   patterns do not exist here. Do the work serially, in this one session.

2. **No Claude-only tools.** `SlashCommand`, `Skill`, MCP tools (`mcp__*`),
   plugin skills and `chrome-cdp` are unavailable. Where the manual calls for
   `chrome-cdp`, use Playwright (`web/playwright.config.ts`). Where it invokes
   another slash command, do that work inline.

3. **Long commands must not block the session.** For anything over about two
   minutes (full test suites, builds, CI waits), launch it detached, poll a
   file, and read the result:

       nohup <cmd> > /tmp/<name>.log 2>&1 &
       echo $! > /tmp/<name>.pid
       # poll: test -s /tmp/<name>.log && tail -5 /tmp/<name>.log

   Write a `DONE <rc>` sentinel as the command's last act and poll for it,
   rather than waiting on the foreground.

4. **Remediation scope on a reduced-capability rung.** When the environment
   variable `RADON_WEEKEND_REDUCED` is `1`, remediate ONLY P0 and P1 findings,
   and say so in the phase's own report. At any other time remediate the full
   verified set exactly as the manual describes.

5. **Never widen a gate to make something pass.** Every rail, refusal and
   "stop, name the gate" instruction in the manual applies here unchanged. If
   you cannot complete the phase honestly, print the contract's INCOMPLETE
   form and stop. A false green is the one unrecoverable outcome.


---

# CONTRACT — what the wrapper reads

The wrapper does not read your prose. It reads these signals, and nothing
else decides whether tonight counted:

- **audit / remediate:** the phase counts as complete only if you have made at
  least one commit on the branch `testing/<YYYY-MM-DD>` (today's date, the
  branch the manual tells you to use). An exit without a commit is scored
  INCOMPLETE, whatever you print.

