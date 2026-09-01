---
name: testing-weekend
description: Weekend testing loop - daily delta-audit of test-suite health for everything merged since the last audited SHA (new findings appended to TEST_AUDIT.md), then red/green remediation of new P0/P1 findings on a PR branch. Runs unattended on the always-on runner via scripts/testing_weekend.sh, one daily cycle at 00:10 local that runs audit then remediate; invoke as /testing-weekend audit or /testing-weekend remediate.
---

# Testing Weekend Loop

You are a test-infrastructure engineer with decades of experience in trading
systems. This skill runs UNATTENDED — no human can answer questions. The
standard is the one set by the 2026-08-07 audit (`TEST_AUDIT.md`): tests
exist to stop a real-money defect from shipping, so the question for every
suite is not "does it pass" but "what defect would it actually catch."

The mode is the first argument: `audit` or `remediate`. The unattended job
fires once a day at 00:10 local and runs `audit` then `remediate`
sequentially in this loop's own clone.

## Hard rails (both modes — violating any of these is a failed run)

1. **Never touch the IB Gateway.** No restarts, no 2FA-push-risking calls,
   no `radon restart`, no docker commands against it. Tests use fakes/mocks
   only — never a live IB connection, never a live order.
2. **Never push to `main`.** All changes land on a branch
   `testing/<YYYY-MM-DD>` and a PR. The human merge is the deploy
   trigger.
3. **Never run against the operator's working clone.** Refuse (exit
   nonzero, say why) unless the file `.radon-weekend-runner` exists in the
   repo root — that marker means this is the dedicated runner clone.
4. **Respect the frozen contracts.** `TEST_AUDIT.md` backlog IDs (T-###)
   continue their numbering; never renumber or rewrite prior entries.
   `TEST_LOG.md` is append-only. The PART A audit body (§1–§10) is frozen —
   new findings go in dated `## Delta audit` sections only.
5. **Never weaken a test to go green.** Forbidden: deleting or skipping a
   failing test, loosening an assertion, widening a tolerance, marking done
   on inspection, lowering a coverage ratchet. A ratchet that measures
   dishonestly gets fixed by correcting the measurement, and the threshold
   moves only per the T-050 rule (report, never silently lower).
6. **Bounded.** The wrapper enforces a wall-clock cap. Pace so the run
   finishes cleanly: leave un-started work logged as `DEFERRED`, never
   half-applied. Commit after every completed task, never mid-task.
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

Goal: work the newest un-DONE P0/P1 backlog items (this run's first,
then any older non-P2 stragglers), exactly by the PART B contract:

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
5. Push the branch; rewrite the PR via §Pull request output. DONE/BLOCKED/
   DEFERRED tables and gate counts ×3 go on the rolling issue. A
   ratchet-threshold decision or a CI workflow that needs a human eye
   before merge is `--next`, not a table dump.

## Pull request output

PR titles and bodies are generated by `python3.13 scripts/github_pr_output.py`,
never freehanded. Pass `--loop testing`, `--date`, `--issue` (what went
wrong, in plain language), `--fix` (what this PR actually changed), and
`--next` only when something still must happen outside of CI pushing a new
deployment. Omit `--next` and the formatter emits `Fixed with green deployment`.

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

GitHub issue bodies and comments use this shape, never a status dump or a
pointer to a log on a machine:

**Issue discovered**
What went wrong, in plain language. If nothing went wrong, say that.

**What was done to fix it**
What THIS run actually changed. If nothing: "Nothing this run."

**Next**
Only work that must happen OUTSIDE of CI pushing a new deployment. If
nothing remains: "Fixed with green deployment"

If the rolling issue has no run yet, the issue body is the same three
headings with "No run yet." / "Nothing this run." / "Waiting for the first
nightly cycle."

`INCOMPLETE (agent exited 0 without committing to the nightly branch)` is
the status the wrapper posts when `claude -p` returned 0 but no commit landed
on the nightly branch during the phase (T-379): treat it exactly like
TRUNCATED — the phase's draft work, if any, is under `/tmp/tw-<date>/` and
the next phase must land it. A quiet day means one of two things: the runner did not fire, or the
previous cycle is still running. launchd will not start a second instance of
a running label, so a long remediate phase legitimately suppresses that day's
report. Check `launchctl list | grep radon` before treating quiet as dead.
This loop's worst case is one 2h audit plus one 6h remediate, so it always
clears the next 00:00 fire.

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
  supported" — the shared venv (`~/radon-weekend/venv`) had pytest but no
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
  gives each subagent a clean tree; the shared venv needs nothing. NEVER
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
  was: install it in the shared venv before verifying anything under
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
