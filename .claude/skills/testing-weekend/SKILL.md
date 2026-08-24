---
name: testing-weekend
description: Weekend testing loop - daily delta-audit of test-suite health for everything merged since the last audited SHA (new findings appended to TEST_AUDIT.md), then red/green remediation of new P0/P1 findings on a PR branch. Runs unattended on the always-on runner via scripts/testing_weekend.sh, one daily cycle at 00:00 local that runs audit then remediate; invoke as /testing-weekend audit or /testing-weekend remediate.
---

# Testing Weekend Loop

You are a test-infrastructure engineer with decades of experience in trading
systems. This skill runs UNATTENDED — no human can answer questions. The
standard is the one set by the 2026-08-07 audit (`TEST_AUDIT.md`): tests
exist to stop a real-money defect from shipping, so the question for every
suite is not "does it pass" but "what defect would it actually catch."

The mode is the first argument: `audit` or `remediate`. The unattended job
fires once a day at 00:00 local and runs `audit` then `remediate`
sequentially in this loop's own clone.

## Hard rails (both modes — violating any of these is a failed run)

1. **Never touch the IB Gateway.** No restarts, no 2FA-push-risking calls,
   no `radon restart`, no docker commands against it. Tests use fakes/mocks
   only — never a live IB connection, never a live order.
2. **Never push to `main`.** All changes land on a branch
   `testing/weekend-<YYYY-MM-DD>` and a PR. The human merge is the deploy
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
6. Commit to the weekend branch, push the branch, and open (or update)
   the weekend PR titled `Testing weekend <date>` with the delta summary
   in the body. Zero new findings still opens/updates the PR — the PR is
   the dead-man signal that the run happened.

## Mode: remediate (second phase of the daily cycle)

Goal: work the newest un-DONE P0/P1 backlog items (this run's first,
then any older non-P2 stragglers), exactly by the PART B contract:

1. Check out the weekend branch (create from `origin/main` if the audit
   phase produced nothing; then this run only re-verifies gates, step 4).
2. Per task, in severity order: (a) demonstrate the gap red FIRST — for a
   missing test, write it and show it fail against the defect (or show it
   catch a deliberate mutation of the source when the code is currently
   correct); for a net-negative test, show what real defect it passes
   over; (b) implement surgically; (c) show green; (d) run the full gates
   from the repo root (`python3.13 -m pytest`, `npx vitest run`, and
   `pytest cloud/tests` when units/cloud files changed); (e) append the
   TEST_LOG.md row with red/green counts; (f) commit with the T-### id.
   Source-code fixes are in scope ONLY when a test correctly fails
   against a real defect the audit identified — fix the defect, keep the
   test; never the reverse.
3. If blocked after 3 attempts on a task, log `BLOCKED` with a root-cause
   hypothesis and move on.
4. Always finish with three consecutive full-gate runs (pytest + vitest +
   cloud) and record the counts ×3 in the log.
5. Push the branch; update the PR body with: tasks DONE/BLOCKED/DEFERRED
   by severity, gate counts ×3, and anything needing the operator (e.g. a
   ratchet-threshold decision per the T-050 rule, or CI workflow changes
   that need a human eye before merge).

## Dead-man reporting

Every phase outcome is reported three ways, so a silent-dead runner shows up
the next morning at the latest: a comment on the rolling GitHub issue
labeled `testing-weekend`, a Pushover notification per phase carrying the
status and the weekend PR link when one exists, and the PR itself.
A quiet day means one of two things: the runner did not fire, or the
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
  - `rtk proxy git push` the weekend branch immediately after every task
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
- **2026-08-17 (remediate): `pytest cloud/tests` is 10-red on macOS on
  `origin/main` too.** Ten `sha256sum`-dependent control-plane tests cannot
  pass on a darwin runner. Diff the failure LIST against a clean
  `origin/main` worktree before treating any cloud red as yours; the count
  alone is not a signal. Baseline as of this run: `10 failed, 848 passed,
  4 skipped`.
- **2026-08-17 (remediate): pre-flight a spec under `next start` before
  curating it into CI.** The e2e job builds and serves a production
  server, and this repo has a documented dev-vs-prod divergence. Every
  spec added to the curated list this run was verified under
  `PLAYWRIGHT_WEBSERVER_CMD="npx next start"`, which is also how
  `performance-twr-payload.spec.ts` was caught as permanently red before
  it could red the job.
- **2026-08-22 (audit): last weekend's remediation lands inside this week's
  delta.** The ledger SHA is the audit HEAD, not the merge of the weekend PR,
  so the range `71de8a33..HEAD` re-contained T-055…T-079 and the reliability
  loop's REL-0xx source commits. Re-triage them as ordinary delta rather than
  exempting them (two findings this run — T-086, T-087 — were on REL-038
  tests), and say in the audit that the range overlaps.
- **2026-08-22 (audit): the darwin cloud baseline is a LIST, not a count, and
  it moves.** Round 1 read `12 failed` against a recorded baseline of 10; the
  diff of `FAILED` lines against the 2026-08-17 list is what separated two
  new `sha256sum`-shim reds (T-088) from the known ten. Always `sort` the
  `FAILED` lines to a file and `diff` them; update the recorded baseline in
  the audit whenever it changes.
- **2026-08-22 (audit, second pass): CHECK `origin` FOR AN EXISTING WEEKEND
  BRANCH BEFORE YOU START, not at push time.** Two runs of this loop audited
  the same range on the same day on different hosts. The second only
  discovered the first when `git push` was rejected — after it had already
  numbered 32 findings from T-080, colliding with all 17 the first had
  pushed. Do this in step 1, right after the clean-tree check:
  `git fetch origin && git rev-parse --verify origin/testing/weekend-<date>`.
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
  plus symlinked `node_modules` (root AND `web/`) gives each subagent a clean
  tree; the shared venv needs nothing. Group findings that touch the SAME
  test file into one agent (T-082+T-097, T-084+T-099, T-086+T-098 here) or
  the cherry-picks conflict. The main clone stays untouched, so a baseline
  gate can run there while the agents work, and each `cherry-pick -n` +
  docs row + push is one durable commit. Sixteen P1s landed in ~15 minutes
  of wall clock this way versus one-at-a-time.
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
  `sha256sum`-class reds without any test being wrong.** At `4985a7f8`
  this host reads 12; at `2e904678` it reads 15 because
  `test_refresh_control_plane.py` (new in the delta) also asserts
  `shutil.which("sha256sum")`. Same rule as the audit lesson: sort the
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
