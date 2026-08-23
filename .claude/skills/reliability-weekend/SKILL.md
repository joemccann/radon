---
name: reliability-weekend
description: Weekend reliability loop - daily delta-audit of everything merged since the last audited SHA (new findings appended to RELIABILITY_AUDIT.md), then red/green remediation of new P0/P1 findings on a PR branch. Runs unattended on the always-on runner via scripts/reliability_weekend.sh, one daily cycle at 00:00 local that runs audit then remediate; invoke as /reliability-weekend audit or /reliability-weekend remediate.
---

# Reliability Weekend Loop

You are a site reliability engineer with decades of experience in trading
systems. This skill runs UNATTENDED — no human can answer questions. The
standard is the one set by the 2026-08-09 audit (`RELIABILITY_AUDIT.md`):
this system handles live orders and real money, so the question for every
component is not "does it work" but "what happens when it doesn't."

The mode is the first argument: `audit` or `remediate`. The unattended job
fires once a day at 00:00 local and runs `audit` then `remediate`
sequentially in this loop's own clone.

## Hard rails (both modes — violating any of these is a failed run)

1. **Never touch the IB Gateway.** No restarts, no 2FA-push-risking calls,
   no `radon restart`, no docker commands against it.
2. **Never place, modify, or cancel a live order.** Fault injection is
   fakes/mocks only. Never set or clear the production trading halt.
3. **Never push to `main`.** All changes land on a branch
   `reliability/weekend-<YYYY-MM-DD>` and a PR. The human merge is the
   deploy trigger.
4. **Never run against the operator's working clone.** Refuse (exit
   nonzero, say why) unless the file `.radon-weekend-runner` exists in the
   repo root — that marker means this is the dedicated runner clone.
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
6. Commit to the weekend branch, push the branch, and open (or update)
   the weekend PR titled `Reliability weekend <date>` with the delta
   summary in the body. Zero new findings still opens/updates the PR —
   the PR is the dead-man signal that the run happened.

## Mode: remediate (second phase of the daily cycle)

Goal: work the ENTIRE un-DONE backlog to completion in severity order —
P0, then P1, then P2 (this run's items first, then older stragglers)
— exactly by the PART B contract. Deferring remaining items to a future
run is not an outcome; every backlog item ends this run as DONE or
BLOCKED-with-root-cause.

1. Check out the weekend branch (create from `origin/main` if the audit
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
5. Push the branch; update the PR body with: tasks DONE/BLOCKED by
   severity, gate counts ×3, and anything needing the operator
   (control-plane unit changes need the root bootstrap before merge —
   say so explicitly in the PR body when `cloud/services/*` changed).

## Dead-man reporting

Every phase outcome is reported three ways, so a silent-dead runner shows up
the next morning at the latest: a comment on the rolling GitHub issue
labeled `reliability-weekend`, a Pushover notification per phase carrying
the status and the weekend PR link when one exists, and the PR itself.
A quiet day means one of two things: the runner did not fire, or the
previous cycle is still running. launchd will not start a second instance of
a running label, so a long remediate phase legitimately suppresses that day's
report. Check `launchctl list | grep radon` before treating quiet as dead.
The reliability cycle is bounded to 20h so it cannot swallow the next 00:00
fire.

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
- 2026-08-16 (remediate): 10 `cloud/tests` cases fail on darwin only —
  they assert on a `sha256sum` binary macOS does not ship
  (`shutil.which("sha256sum")` is `None`). They pass in Linux CI. Do not
  chase them; state them as environmental in the log and PR body, and
  compare against a stashed baseline to prove your change did not add to
  the count.
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
- 2026-08-22 (audit): **check for a remote weekend branch BEFORE numbering
  anything.** Two rounds of the Saturday audit ran against the same delta on
  the same day. The second finished a complete 81-finding section numbered
  R-084…R-164 and only discovered the collision when `git push` was rejected —
  the first round had already pushed R-084…R-139. Recovering meant resetting
  onto the remote, diffing 81 findings against 56 by file:line, dropping the 23
  duplicates and renumbering the rest to R-140…R-197. Do this FIRST, every run,
  before the walks are even launched:
  `git ls-remote --heads origin reliability/weekend-<date>` and, if it exists,
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
