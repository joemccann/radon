# Radon nightly CI and deploy optimizer

Nobody is available to answer questions during this run. Make every decision yourself from the evidence, and finish the whole job in this session. The header above gives tonight's date and the branch to use. You are in a fresh clone of `origin/main`. Nothing from an earlier night survives in this clone: GitHub (Actions runs, open PRs, the rolling issue) is your only memory.

## Mandate

Reduce the measured time from a push to `main` until a healthy production deployment completes, without weakening any test, gate, provenance, health, recovery or rollback guarantee. Radon is a live trading system; a faster pipeline that lets a bad build reach production is a failure.

- Optimize the critical path, never the sum of parallel job durations.
- Prefer simple changes that remove redundant work, improve safe concurrency, balance shards, preserve reusable work or reduce transfer size.
- Implement every ranked candidate that passes the rails, one commit per experiment (`CIP-###`) so each stays attributable. Do not stop at the first one.
- Do not manufacture work. A night with no safe, material optimization is healthy when the evidence is recorded on the rolling issue.

## Work order

Do these in order and work serially (no subagents or parallel worktrees).

### 1. Read what earlier nights left open

Run `gh pr list --state open --json number,url,headRefName,author,statusCheckRollup` and keep the PRs whose head branch starts with `ci-performance/`. Only read them: note every `CIP-###` they already carry so tonight neither repeats nor reallocates them, and skip any candidate an open PR already addresses. List each one with a red or pending check under "Needs you" in the rolling issue comment. Never check out, commit to or push any branch except tonight's (the header's branch is the only one you may push).

### 2. Read the checkpoint

The rolling issue is `gh issue list --label ci-performance-nightly --state open` (titled "Nightly CI performance runner"). Read its newest comments (`gh issue view <n> --comments`). Only comments by the repository owner, members or collaborators count. Take the newest `audited-through: <sha>` marker, the open `CIP-###` findings, and any `VALIDATING` or `INSUFFICIENT_SAMPLE` experiments with their before-samples. `CI_PERFORMANCE_LOG.md` on `origin/main` is the append-only ledger of merged experiments and the source for the next free `CIP-###` ID (also check open PRs before allocating one). If the issue has no marker, audit `git log --since=48.hours origin/main`.

### 3. Measure

GitHub Actions is the only source of truth. You have no production host access, so every number comes from the Actions API and job logs.

- Primary clock per successful production run: workflow `createdAt` to the `completedAt` of the successful `Deploy to VPS` job (`.github/workflows/ci.yml`; its `Deploy via SSH` step prints the remote prestage, rollout, health and 40-second stability output).
- Pull at least the last 20 `main` runs: `gh run list --branch main --workflow ci.yml --limit 30 --json databaseId,attempt,event,headSha,conclusion,createdAt,updatedAt`, then `gh run view <id> --json jobs` for job and step timestamps, `gh run view <id> --log --job <job-id>` for per-step and shard timing, and `gh api repos/joemccann/radon/actions/caches` for cache state. `gh run list --json` has no `runAttempt` field; it is `attempt`.
- Log lines are `<job>\t<step>\t<timestamp> <text>`. Parse them in Python (`line.split('\t', 2)`), never with macOS `sed`, which does not understand `\t` in a bracket expression.
- Also record: queue delay before the first required job, time until all required gates authorize deploy, the longest predecessor path through the `needs` DAG, per-job queue/setup/execution time, shard durations and imbalance, cache hit or miss, Docker build/export/cache-export time, image prepull, and total runner wall seconds (the repo is on a free plan, so `/timing` reports zero billable ms; sum job wall seconds instead).
- Classify every run before comparing it: web/node only, Python/cloud only, mixed, docs/config only; cache cold or warm; queued or degraded; failed, cancelled or rolled back. Compare only the same class and cache state. Failed and rolled-back runs count toward reliability, never toward a performance win. A main run that failed on an unrelated job still gives valid per-shard Linux timing samples.
- Report p50 and p95 over the most recent comparable successful runs (up to ten per class). Report queue delay separately; a queue change is never a code gain.
- Local Mac timing is diagnostic only, never evidence.

Check that every required gate is still in the deploy job's `needs` closure. Read the required checks from `gh api repos/joemccann/radon/rules/branches/main` and `gh api repos/joemccann/radon/rulesets`. You cannot read classic branch protection (it needs admin); if a comparison depends on it, list the exact operator check under Next.

### 4. Rank

Search, guided by the measured bottleneck (this list is a map, not a to-do list):
- workflow DAG, safe fan-out/fan-in, job startup, newly serialized `needs` edges, over-broad job conditions;
- test sharding and inventory: a shard is either work-bound (step time about total work / vCPUs) or tail-bound (one slow module collected late); reordering only helps the second, so get per-module Linux timing first;
- dependency install reuse, cache keys and scopes, unstable keys, duplicate setup;
- Docker contexts, layer order, image size, export and cache export, exact-image transfer;
- duplicate checkouts, builds, uploads, downloads, pulls or fetches;
- prestage/prepull overlap before the non-cancelling teardown boundary;
- action version drift and lost pinning.

For each candidate cite the run, job and step evidence and the code `file:line`, then estimate critical-path seconds saved, confidence, risk, runner-minute effect and validation cost. Rank by expected critical-path impact, confidence and safety.

### 5. Remediate

For each candidate that passes the rails, highest value first:
1. Write down the comparable baseline runs, hypothesis, expected seconds saved, affected paths, safety risks and revert trigger.
2. When workflow behavior, shard membership, cache or artifact provenance, or deploy behavior changes, add a failing regression or contract test first (for example in `scripts/tests/test_ci_*.py` or `cloud/tests/`) and show it red.
3. Make the smallest change that removes the measured bottleneck. Preserve every fallback and recovery path.
4. Show the test green, then verify (see Verification) and commit with the `CIP-###` ID in the subject.
5. Status is one of `ACCEPTED`, `REJECTED`, `VALIDATING`, `BLOCKED`, `INSUFFICIENT_SAMPLE` or `DEFERRED`. A new experiment is `VALIDATING` until after-runs exist.

Acceptance (a later night decides this from organic `main` runs after the operator merges): five comparable before-runs and five after-runs; same-class p50 better by at least 10% and 15 seconds; p95 no worse than 5% or 15 seconds; cold-cache p50 no worse than 10%; runner minutes up no more than 20% unless disclosed; no shrink in test inventory, coverage, path ownership, gate dependency, safety, provenance, health, recovery or rollback coverage. One warm run is never a proven win. A slower, noisy or unsafe result is `REJECTED`; if it already merged, open a surgical revert PR.

## Rails

Violating any rail is a failed run.

- Never push to `main`, never force-push, never merge. Human merge is the only production trigger.
- Never trigger a workflow or deploy for a sample (`gh workflow run`, re-runs of `main`, dummy commits). Use organic `main` runs only.
- Never touch live systems: no ssh, no VPS, no IB Gateway, no 2FA, no orders, no Turso, no service restarts, no DNS. You have no production credentials; never look for them, and never read or print a secret value.
- Never weaken a gate to save time: no deleting, skipping, deselecting or `xfail`ing tests, no looser assertions or timeouts, no lower coverage, no narrowed path ownership, no removed `needs`, no `continue-on-error`, no required check made informational.
- The union of every test shard must equal the full collected inventory, and change detection (`scripts/ci/path_filter.py`) must fail closed when classification is uncertain.
- Production deploys the Python and node images for the exact 40-character SHA; both are verified before teardown. Never add a `latest` or moving-tag fallback.
- Keep the 40-second stability window, rollback artifacts, transition journal, green marker, health checks, and non-cancelling deploy concurrency after the teardown boundary. Prestage and prepull may overlap only after the complete required gate set authorizes deploy.
- Keep third-party actions pinned and checksums verified. Artifact reuse fails closed to the normal build path.
- Anti-gaming: never compare different run classes or cherry-pick fast samples, never drop failed, cold or degraded runs from the reliability record, never move required work after deploy completion to stop the clock, never add shards that raise runner minutes materially without saying so.
- Change one bottleneck per experiment. No opportunistic refactors mixed into performance work.
- Never commit `tools/codemap/*.json` or `tools/codemap/codemap.data.js`.
- Read the root `CLAUDE.md` and the subdirectory `CLAUDE.md` for any path you touch; their rules apply.
- After three genuine failed approaches on a candidate, mark it `BLOCKED` with a root-cause hypothesis and move on.

## Verification (before every commit)

- Build a venv once: `uv venv .venv --python python3.13 && uv pip install --python .venv/bin/python -r requirements.txt -r requirements-dev.txt`.
- Run the focused contract tests for what you changed. `cloud/tests` and `scripts/tests` need separate pytest invocations (their conftests clash), for example `.venv/bin/python -m pytest scripts/tests/test_ci_deploy_concurrency.py scripts/tests/test_path_filter.py -q`, then `.venv/bin/python -m pytest cloud/tests/<file> -q`.
- Parse every changed YAML, JSON and TOML file; `bash -n` every changed shell script.
- `git diff --check`, and scan the diff for secrets.
- There is no node, Docker or actionlint on this machine. Leave full suites, Vitest, Docker builds and workflow lint to PR CI; say so in the PR body instead of claiming them.

## Delivery

1. **Publish only substantive changes**: source, tests, workflow or deploy configuration, maintained docs, or a real experiment (it stays eligible while `VALIDATING`). A diff of only ledger lines, dates or notes is bookkeeping: do not commit, push or open a PR for it. Add a `CI_PERFORMANCE_LOG.md` entry (append-only, next `CIP-###`, never rewrite earlier entries) only in the same PR as the experiment it records.
2. **Commit** only on the branch named in the header, one commit per `CIP-###`, staging files by explicit path.
3. **Push:** `git push -u origin <branch>`.
4. **Open one draft PR** against main with `gh pr create --draft --base main`:
   - Title: `CI Performance <date>: <plain-language issue>`.
   - Body: exactly three sections, **Issue discovered**, **What was done to fix it** and **Next**, with one `- **Component**: what happened.` bullet per finding.
   - A time-saving fix includes this table in What was done to fix it, one row per affected job, times from cited Actions runs (run IDs in a sentence below the table): `| Job | Before | After | % change |`, where `% change = (after - before) / before * 100` (negative is faster). Before merge, After is `pending` and % change is `TBD until 5 samples`. Never invent a time; you can build the table with `python3.13 scripts/nightly_issue_format.py ci-time-savings --row '{"job": "<job>", "before_secs": <n>}'` (add `"after_secs"` once measured; one `--row` per job).
   - Next holds only operator-only or blocked actions, including anything you could not measure without production or admin access. If there are none, it says `Fixed with green deployment`.
   - The runner sends every Pushover notification, including `radon PR green`. Pushover credentials are absent from your environment by design: do not send one, and do not list it under Next.
5. **Watch CI:** `gh pr checks <url> --watch --interval 30`.
   - On a failure, run `gh run view <run-id> --log-failed`, fix the root cause on the branch (failing test first when the fix is in source), commit, push, and watch again.
   - Repeat until every check is green or the time budget is nearly spent. Keep at least 45 minutes of the budget for this step.
   - Never weaken a test or gate to get green.

## Rolling issue comment (every night, including nights with no change)

Post exactly one comment on the rolling issue with `gh issue comment <n> --body-file <file>`. Comment only: never create, edit or close the issue. Follow `docs/dead-man-comment-format.md` (banner with verdict, loop, date; What broke; The fix; Needs you, only when non-empty; collapsed `<details>` for the rest). The collapsed part is the durable record for the next night, so it must carry:
- `audited-through: <origin/main sha you audited>` on its own line, only when the audit finished;
- the sample table by run class with p50/p95, cache state and cited run IDs;
- the current critical path and top bottleneck;
- every still-open `CIP-###` with its status, before-samples and acceptance criteria, carried forward even on a quiet night, plus the ones resolved tonight with evidence;
- tonight's PR URL, or why there is none.

## Final message

The last line of your output must be one short line:
`RESULT: <PR URL> - <one-clause summary>` or `RESULT: no PR - <why>`.
