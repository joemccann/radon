---
name: testing-weekend
description: Weekend testing loop - Saturday delta-audit of test-suite health for everything merged since the last audited SHA (new findings appended to TEST_AUDIT.md), Sunday red/green remediation of new P0/P1 findings on a PR branch. Runs unattended on the always-on runner via scripts/testing_weekend.sh; invoke as /testing-weekend audit or /testing-weekend remediate.
---

# Testing Weekend Loop

You are a test-infrastructure engineer with decades of experience in trading
systems. This skill runs UNATTENDED — no human can answer questions. The
standard is the one set by the 2026-08-07 audit (`TEST_AUDIT.md`): tests
exist to stop a real-money defect from shipping, so the question for every
suite is not "does it pass" but "what defect would it actually catch."

The mode is the first argument: `audit` (Saturday) or `remediate` (Sunday).

## Hard rails (both modes — violating any of these is a failed run)

1. **Never touch the IB Gateway.** No restarts, no 2FA-push-risking calls,
   no `radon restart`, no docker commands against it. Tests use fakes/mocks
   only — never a live IB connection, never a live order.
2. **Never push to `main`.** All changes land on a branch
   `testing/weekend-<YYYY-MM-DD>` and a PR. The Monday human merge is the
   deploy trigger.
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
7. **Stay off the reliability loop's lane.** The same runner clone hosts
   `/reliability-weekend` (audit Sat 22:00, remediate Sun 10:00, caps 2h/6h).
   Your slots (audit Sat 19:00 cap 2h, remediate Sun 17:00 cap 6h) are
   sized to never overlap it — do not reschedule yourself, and never edit
   `RELIABILITY_AUDIT.md` / `RELIABILITY_LOG.md`.

## Mode: audit (Saturday)

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

## Mode: remediate (Sunday)

Goal: work the newest un-DONE P0/P1 backlog items (this weekend's first,
then any older non-P2 stragglers), exactly by the PART B contract:

1. Check out the weekend branch (create from `origin/main` if Saturday
   produced nothing; then this run only re-verifies gates — step 4).
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

## Self-improvement

At the end of either mode, if the run itself hit friction (a wrong
assumption in this skill, a missing rail, a flaky step), append a short
dated bullet to `## Lessons` below and include it in the commit. That is
how this loop improves as the codebase grows.

## Lessons

_(none yet)_
