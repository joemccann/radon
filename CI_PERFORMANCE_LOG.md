# CI Performance Ledger

Append-only ledger for the nightly CI/deploy performance loop
(`.claude/skills/ci-performance/SKILL.md`, runner
`scripts/ci_performance_nightly.sh`). Never renumber or rewrite prior entries.
IDs continue as `CIP-###`.

## Measurement contract

- **Primary clock:** GitHub `workflow_run.created_at` -> successful
  `Deploy to VPS` job `completed_at`, for `push` runs of `ci.yml` on `main`.
  Source of truth is `gh api repos/joemccann/radon/actions/runs/<id>/jobs`.
- **Critical path** is reconstructed from `needs:` edges by walking back from
  `Deploy to VPS` to the latest-completing predecessor at each hop. The sum of
  parallel job durations is never reported as elapsed time.
- **Queue delay** (job `started_at - created_at`) is recorded separately and
  never counted as a code performance change.
- **Run classes:** `mixed` (Vitest and pytest shards both ran), `web` (Vitest
  only), `python` (pytest only), `docs` (both gates skipped by
  `scripts/ci/path_filter.py`), plus cache-cold / cache-warm, queued or
  degraded, and failed / cancelled / rolled back. Only same-class, same-cache
  runs are compared. Failed, cancelled and rolled-back runs count toward
  reliability, never toward performance.
- **Windows:** rolling last ten successful comparable runs; p50 and p95 both
  reported. A final `ACCEPTED` needs >= 5 comparable before and >= 5 after.
- **Acceptance:** same-class p50 improves >= 10% and >= 15s; p95 regresses
  <= 5% / 15s; cold p50 regresses <= 10%; runner-seconds grow <= 20% unless the
  PR documents the trade; no test inventory, coverage, path ownership, gate
  dependency, provenance, health, recovery or rollback coverage shrinks.
- **Runner usage:** the repo is on a free plan (`/timing` reports
  `billable.UBUNTU.total_ms = 0`), so runner cost is tracked as the sum of job
  wall seconds per run ("runner-seconds").
- **Cache state proxy:** the `Build and publish node image` step duration
  (~60-75s warm with every dependency layer `CACHED`; 250-340s cold) and the
  `setup-uv` / `actions/cache` "Cache hit" log lines.

## Ledger

### CIP-000 - bootstrap anchors (recorded 2026-08-31, mode audit)

Historical evidence carried in from the 2026-08-30 image work; starting
points, not permanent baselines.

| Run | SHA | Class | Push -> production | Note |
|---|---|---|---|---|
| [33290751126](https://github.com/joemccann/radon/actions/runs/33290751126) | 0f33b878 | mixed | 467s | pre-image-work baseline (skill text says 468s) |
| [33294190643](https://github.com/joemccann/radon/actions/runs/33294190643) | 92278f6a | mixed, cold node image | 471s | node image 283s + prepull 85s on the critical path |
| [33294882038](https://github.com/joemccann/radon/actions/runs/33294882038) | 2f46a166 | mixed, warm | 231s | `perf(ci): eliminate duplicate node image layer`; node build 71s, prepull 16s |
| [33295066378](https://github.com/joemccann/radon/actions/runs/33295066378) | fda36450 | docs, warm | 113s | cache-behaviour evidence only |

Outcome: `ACCEPTED` as the standing baseline shift (before n=10 mixed p50 455s
/ p95 481s; after n=15 mixed p50 238s / p95 269s; see CIP-001 tables).

### 2026-08-31 - audit - branch `ci-performance/2026-08-31`

- Audited range: `2f46a166..39bf6f5e` (52 commits; first audit, so the range
  starts at the CIP-000 baseline shift). Runner state: dedicated clone
  `~/radon-weekend/radon-ci-performance`, lock pid owned by this cycle, clean
  tree at `39bf6f5e`, no orphaned stash.
- Changed CI/build/deploy surfaces in range: `.github/workflows/ci.yml` (+38/-2:
  contract-* jobs, cloud `omit`, app-images gated on secret-scan R-442),
  `scripts/ci/path_filter.py` (+87: green-base resolution T-312),
  `cloud/scripts/deploy.sh` (+9), `cloud/scripts/radon-app-runtime.sh` (+10),
  `cloud/scripts/sync-control-plane.sh` (+18/-5), `docker/app/.dockerignore`
  (+2/-2). Test inventory: 84 files / +8528 lines (largest: STREAKS, vol-cone).
- Gate closure verified on `origin/main`: `scripts/tests/test_ci_gate_integrity.py`
  + `test_ci_deploy_concurrency.py` + `test_path_filter.py` = 67 passed;
  `cloud/tests/test_app_images.py` + `test_actions_node24.py` = 40 passed.
  `main` has no `required_status_checks` (branch protection: enforce_admins
  only; rulesets empty); the Production environment carries a branch policy and
  no reviewer rule; every `uses:` is a 40-char SHA. All 14 gate jobs remain in
  `deploy.needs` and in `stage-release`/`prepull-images` `needs`.

**Sample set** (40 most recent `ci.yml` push runs on `main`, 2026-08-30T00:19Z
to 2026-08-31T05:17Z): 30 success, 8 failure, 2 cancelled. Failures and
cancellations are excluded from performance windows and counted here for
reliability. No queued/infra-degraded run: first-job queue delay 1-3s on every
run; inter-job hops 2-4s.

| Class / window | n | p50 | p95 | min | max | gates authorize p50 |
|---|---|---|---|---|---|---|
| mixed, warm, after `2f46a166` | 15 | 238s | 269s | 214s | 274s | 123s |
| mixed, warm, before `2f46a166` | 10 | 455s | 481s | 414s | 490s | 238s |
| web, warm, after | 2 | 247s | - | 212s | 282s | 106s |
| docs, warm, after | 3 | 127s | - | 113s | 141s | 34s |
| python-only | 0 | - | - | - | - | - |

Cold-cache after-window sample: none (every after-run shows all dependency
layers `CACHED` and uv/bun cache hits). Runner-seconds per mixed run: 1592-1741
(p50 1690); web 918-1026; docs 137-153.

**Current critical path, mixed class** (representative run
[33359053839](https://github.com/joemccann/radon/actions/runs/33359053839),
226s): `Path filter` 7s (+1s queue) -> `pytest (scripts-rs)` 87s (+2s) ->
`pytest coverage ratchet` 13s (+2s) -> `Prestage VPS release` 17s (+2s) ->
`Deploy to VPS` 88s (+4s). Across the 15 after-runs the binding pytest shard
alternates between `scripts-npsz` (job p50 86s, pytest step 73s),
`scripts-jm` (81s / 65s), `scripts-rs` (71s / 55s, max 97s / 84s) and
`pytest (cloud al)` (97s / 84s, no ratchet behind it). The web gate closes at
~104-106s (slowest Vitest shard p50 74s + coverage merge 9s) and the node image
at ~95-110s (job 78s p50 after secret-scan), so both become the wall as soon as
the python gate drops below ~105s.

**Deploy job breakdown** (fast run 88s): ssh + job fetch 4s, runner/recover/sync
3s, deploy.sh stale-tip fetch 3s, stop-clean + activate 6s, `restart-managed`
16s, gates 1s, fixed 40s stability window, `sync-scheduled-units` 8s, finalize
2s. Three of 15 after-runs took 123-150s: +60s from `wait_for_gateway_ready`
(`cloud/scripts/deploy.sh:1204-1218`, 12 x 5s) while the broker reported
`auth_state=unreachable, upstream_dead=true` (IB nightly/weekend reset). That
wait is the pre-teardown relay-ordering guard; an early exit would weaken it
and is rejected under rail 9.

**Standing sweeps**: no new serialized `needs` edge; shard union contracts
green; no duplicate checkout/install on the critical path (setup overhead per
shard ~9s, all cache hits; `Path filter` full-history fetch costs 0.3s over
shallow and is load-bearing for the 30-green lookback); no unstable cache key;
node image 737MB compressed / 26 layers, per-push fresh layers 29MB, second tag
reuses blobs (0.3s); prepull 10s; `actions/cache@v4.2.3` is a node20 action
running under forced node24 (hygiene, not latency; not in
`cloud/tests/test_actions_node24.py` NODE24_PINS); `.prestage` runner dirs
never match `RUNNER_NAME` in `prune-deploy-runners.py:14` so they accumulate
(reliability note, not latency).

#### CIP-001 - python test gate tail: duration-balanced pytest shards + xdist on the cloud shards - SELECTED

- Evidence: `.github/workflows/ci.yml:295-316` letter-glob shards;
  `ci.yml:521` runs the cloud shards serially (`python -m pytest ... -q -rs
  --durations=25`, no `-n`) while `pytest-xdist==3.8.0` is already installed
  from `requirements-dev.txt`. Job 99386754211 (`cloud al`): 765 passed in
  91.2s serial, top-25 = 55s (`test_ib_gateway_control.py` 18.2s,
  `test_deploy_corrections.py` 17.1s, `test_bootstrap_control_plane.py`
  15.3s). Job 99386754867 (`scripts-jm`): 794 passed in 64.7s, of which one
  test burns a real 40.0s
  (`scripts/tests/test_menthorq_dashboard_bootstrap.py:270`
  `test_expired_session_payload_is_rejected` stubs `wait_for_timeout` to
  return instantly while `scripts/clients/menthorq_dashboard_client.py:436,522`
  spins on `time.monotonic()` for the full
  `REQUEST_PATH_AUTH_BUDGET_SECONDS=40.0`) and `test_leap_garch_no_duplicate_scan.py`
  sleeps 2 x 10s. Job 99386754684 (`scripts-npsz`): 1463 passed in 72.8s;
  `test_vixcor.py` and `test_weekend_wrapper_self_rewrite.py` are files #60 and
  #73 of 78 so `--dist loadfile` starts them last. Job 99386754761
  (`scripts-rs`): 1024 passed in 70.7s; `test_rel137_weekend_wrapper_survivability.py`
  is a 45.7s serial floor (real subprocess timeout/SIGKILL drills; leave it).
- Hypothesis: (a) run cloud `al`/`mz` with `-n auto --dist loadfile` (keep
  `edge` serial: it is the wall-clock Caddy mechanism shard), (b) re-partition
  the scripts shards so the heavy file leads its shard's collection order and
  no shard exceeds the rel137 floor, (c) make the menthorq expired-session test
  deadline-injected so it exits in <1s while the production 40s budget stays
  covered by the existing budget assertions. Predicted python gate: max shard
  job ~65-69s + ratchet ~18s = ~85-90s (from 123s), which makes the web gate
  (~106s) the wall: predicted mixed p50 238s -> ~218-222s (-16 to -20s, -7 to
  -8%), p95 269s -> ~250s. Not enough alone to clear the 10% bar; it is the
  first of a three-step stack (CIP-002 node image, CIP-003 Vitest gate) and
  its effect on each shard is separately measurable from job timestamps.
- Shard count unchanged (10 + 3); runner-seconds change ~-90s per mixed run
  (cloud al 97 -> ~45s, jm 81 -> ~40s) = about -5%.
- Affected paths: `.github/workflows/ci.yml` (`py-tests` matrix rows, cloud
  run line), `scripts/tests/test_ci_deploy_concurrency.py:310-328` (pinned
  shard names; union test re-derives), `scripts/tests/test_menthorq_dashboard_bootstrap.py`.
  `DOCS_CONTRACT_BASE` fetch-depth pin at `ci.yml:326` (`scripts-df`) unchanged.
- Safety: recursive union == inventory contract must stay green
  (`test_pytest_shard_union_equals_recursive_collection`,
  `test_cloud_shard_union_equals_recursive_collection`); no test removed,
  skipped or deselected; coverage combine unchanged (`--expect-shards` is the
  Vitest count, untouched). Risk: xdist on cloud may surface state leaks
  (`test_deploy_corrections.py::TestRootHelper` process-group kills); the PR's
  own `pull_request` run exercises the cloud shards before merge.
- Revert trigger: any cloud/scripts shard flake attributable to xdist in the
  first five main runs, or a python gate p50 that does not drop below 105s.
- Validation: five comparable mixed warm after-runs vs the 15 before-runs
  above; per-shard job wall from `gh api .../runs/<id>/jobs`.

#### CIP-002 - node image: `COPY scripts/` invalidates the Next build on python-only pushes - DEFERRED (next)

`docker/app/Dockerfile.node:61-62,79`: `COPY scripts/ ./scripts` precedes `bun
run build`, so a scripts-only push (47 of 167 pushes in the last 7 days) pays
the 37s dependency-layer unpack + 23s Turbopack build for a 1.7MB layer (run
33356840635 node job 82s vs 16-19s on runs where neither tree changed). The
build reads only `scripts/config/market_holidays.json`
(`web/lib/serviceHealthWindows.ts:14`); `@tools` imports are type-only. Move
`scripts/` after the build (`COPY --link --chown`), copy the one JSON before
it. Saves ~60s of node-job wall on ~28% of pushes; ownership of
`/home/radon` under `--link` needs an image-level check;
`cloud/tests/test_app_images.py` ordering assertions to add. Becomes
critical-path-relevant once CIP-001 lands.

#### CIP-003 - Vitest gate (~106s) - DEFERRED

Eight shards p50 63-74s (shard 5 slowest), `--shard` is file-count balanced;
exec 53s of a 69s job, `import 43s / environment 44s / setup 28s` per shard
report. Options: duration-aware shard assignment or +2 shards (+~30
runner-seconds). Measure after CIP-001 exposes it as the wall.

#### CIP-004 - deploy tail: `sync-scheduled-units` 7-8s and duplicate job-level fetch 2-3s - DEFERRED

`cloud/scripts/deploy-root-helper.sh:1137-1233` spawns ~300 processes (per-unit
`git cat-file` + 2 x sha256sum over 96 allowlisted units) after the green
marker; batch with `git cat-file --batch` keeping every check. Deploy job
`git -C ~/radon fetch --prune origin main` (`ci.yml:913`) can be conditional
on `git cat-file -e "$SHA^{commit}"` (deploy.sh:1685 refetches and rejects a
non-tip). Together ~8-10s p50; root-helper edits ship via
`sync-control-plane`.

#### Rejected / no-change

- Gateway-wait early exit: weakens the pre-teardown ordering guard (rail 9).
  Zero-risk follow-up is to log the observed `auth_state/port_listening/
  upstream_dead` per attempt so p95 waits are classifiable.
- Dependency/cache lane: every install step is at floor (uv cache hit, 81
  wheels in 2s; bun cache 225MB restore 4s); shallow `Path filter` checkout
  saves 0.3s and weakens the green lookback; py-coverage 4s is source parsing,
  not output. No candidate above ~1s.
- Image compression / mediatype / Turbopack persistent cache: <=5s net, would
  rewrite 26 layer digests or add experimental flags.

Outcome for tonight: `VALIDATING` pending remediate (CIP-001). Residual
bottleneck after CIP-001: web gate ~106s and node image ~95-110s in parallel,
then the fixed ~88s deploy (40s of it the stability window).

### 2026-08-31 - remediate - CIP-001 implemented - branch `ci-performance/2026-08-31`

**Pre-edit record.** Baseline: the 15 mixed warm after-runs in the audit table
(p50 238s / p95 269s; python gate p50 123s = slowest scripts shard job ~87s +
2s hop + ratchet ~13-18s; `cloud al` job 97s with no ratchet behind it).
Critical path: `Path filter` -> slowest pytest shard -> `pytest coverage
ratchet` -> `Prestage VPS release` -> `Deploy to VPS`. Hypothesis: three
independent, separately measurable cuts to the python gate tail: (a) xdist on
cloud `al`/`mz`, (b) lead the measured wall-floor modules in the scripts
shards, (c) deadline-inject the one test that burns a real 40s. Expected:
python gate 123s -> ~85-90s, mixed p50 238s -> ~218-222s (-7 to -8%), the web
gate (~106s) becomes the wall. Affected paths: `.github/workflows/ci.yml`,
`scripts/tests/test_ci_deploy_concurrency.py`,
`scripts/tests/test_menthorq_dashboard_bootstrap.py`,
`scripts/tests/test_menthorq_bootstrap_deadline.py`. Revert trigger: a cloud
`al`/`mz` failure attributable to worker contention in the first five `main`
runs, or a python gate p50 that stays above 105s.

**Change.**

- `ci.yml` `cloud-tests`: new matrix key `xdist` (`-n auto --dist loadfile`
  on `al` and `mz`, empty on `edge`) word-split into the run line. `edge`
  stays serial (Caddy wall-clock mechanism). `al`'s only other caddy spawner,
  `test_caddyfile.py::TestRestartWindowMechanism`, runs `admin off` on
  ephemeral ports and `loadfile` keeps the module on one worker.
  `test_deploy_corrections.py` kill-groups target children started with
  `start_new_session=True` / their own recorded pgid, never the worker's.
- `ci.yml` `py-tests`: `scripts-jm`, `scripts-npsz`, `scripts-rs` name their
  measured wall-floor modules ahead of the letter glob (pytest de-duplicates
  the glob hit; `--collect-only` counts identical: jm 795, npsz 1464, rs
  1024; first collected id is the lead in all three). Shard names, count (10),
  globs, `DOCS_CONTRACT_BASE` fetch-depth pin and coverage combine unchanged.
- `test_menthorq_dashboard_bootstrap.py::test_expired_session_payload_is_rejected`:
  the fake page's `wait_for_timeout` returns instantly, so the session poll
  spun on `time.monotonic()` for the full 40.0s budget. It now monkeypatches
  `REQUEST_PATH_AUTH_BUDGET_SECONDS` to 0.5 for that test only and asserts the
  poll exits in < 5s. `test_menthorq_bootstrap_deadline.py` pins the module
  default `== 40.0` so the override can never become the production value;
  the existing `< 50` proxy-fit and total-deadline contracts are untouched.

**Contracts (red -> green).**

- New `test_heavy_pytest_modules_lead_their_shard` (pins
  `PYTEST_SHARD_LEAD_MODULES`, asserts each lead exists on disk and matches
  its own shard's glob so a lead can only reorder, never move, a module) and
  `test_cloud_shards_parallelise_except_the_wall_clock_edge_shard`: both
  failed on the pre-change workflow (`KeyError: 'xdist'`, lead order), pass
  after.
- `_partition` in `test_pytest_filename_shards_partition_scripts_tests` now
  counts overlap per shard ROW instead of per token (a same-row lead + glob
  is one shard; the fixture test gains the two-row double-run case that must
  still red). Recursive-union contracts
  `test_pytest_shard_union_equals_recursive_collection` /
  `test_cloud_shard_union_equals_recursive_collection` unchanged and green.
- Baseline red for (c): `7 passed in 40.20s`, `40.00s call
  test_expired_session_payload_is_rejected`. After: 41 menthorq tests in
  0.89s, that test 0.50s.
- Focused: `test_ci_deploy_concurrency.py` + `test_ci_gate_integrity.py` +
  `test_path_filter.py` = 69 passed; `cloud/tests/test_app_images.py` +
  `test_actions_node24.py` = 40 passed. YAML parses; every cloud matrix row
  dry-run through the real step script (mapfile shim on bash 3.2): `al` 26
  files + xdist flags with the edge module omitted, `edge` 1 file serial, `mz`
  17 files + xdist flags; `bash -n` clean.

**Local xdist evidence (diagnostic only; Mac mini at load 6-14 with three
other nightly loops running).** `cloud mz` under `-n 4 --dist loadfile`: 744
passed in 61.8s. `cloud al` under `-n 4`, two runs: 723/725 passed, 38/36
failed, 35 of them identical serially (`operator-radon.sh: line 266:
mapfile: command not found`, macOS bash 3.2 baseline; two supervisor tests
are serially flaky on macOS too). One test,
`test_ib_gateway_control.py::test_healthy_start_and_concurrent_stop_are_serialized`
(2.0s wait for the fake-docker inspect marker), failed in both loaded xdist
runs and passed serially and when its module ran alone under `-n 4`: a
cross-worker CPU-contention sensitivity, not an xdist ordering defect. Its
budget was NOT loosened (rail 6). It is the named revert trigger; the PR's
own `pull_request` run is the first Linux sample.

**Full local gate (serial, this tree).** ac 1825, df 1283, i 816, gh 401, jm 795, npsz 1463, rs 1024, daemons 772, rest-api 786, rest 346 passed (0 failed); cloud mz 744, edge 30 passed; cloud al 725 passed + 35 macOS-only baseline failures (`mapfile` on bash 3.2, identical on the unchanged tree) + the contention-sensitive marker-wait test noted below.

**Runner-seconds.** Shard count unchanged (10 + 3). Expected -80 to -100s per
mixed run (`cloud al` 97 -> ~45s job, `scripts-jm` 81 -> ~40s), about -5%.

**Residual bottleneck after CIP-001.** Web gate ~106s (8 Vitest shards p50
63-74s + coverage merge) in parallel with the node image ~95-110s, then the
fixed ~88s deploy (40s stability window). Next: CIP-002 (node image
`scripts/` copy boundary), then CIP-003 (Vitest shard balance).

PR: https://github.com/joemccann/radon/pull/211 (commit is the branch tip carrying `CIP-001` in its subject).

Outcome: `VALIDATING` (0 of 5 comparable after-runs; human merge required).

**First Linux sample - PR run
[33370066924](https://github.com/joemccann/radon/actions/runs/33370066924)
(`pull_request`, `a0b3b178`, warm cache, n=1; not a production run, not an
after-sample).** Every pytest job green with the same inventories. Per-shard
pytest step, before (run 33359053839) -> now:

| Shard | Before | Now | Read |
|---|---|---|---|
| `cloud al` | 91.2s (job 97s) | 29.9s (job 45s), 765 passed 2 skipped | (a) confirmed; the contention-sensitive marker-wait test passed |
| `cloud mz` | 17.4s (job 21s) | 10.0s (job 21s) | (a) confirmed, off the path |
| `scripts-jm` | 64.7s (job 81s) | 41.3s (job 59s) | (c) confirmed, -23s |
| `scripts-npsz` | 72.8s (job 86s) | 73.7s (job 88s); top files unchanged (vixcor 23.2s, self_rewrite 20.3s) | (b) no effect: the shard is work-bound, W ~ 4 x 73s ~ 290s across 78 modules, so lead order cannot move the wall |
| `scripts-rs` | 70.7s (job 71s; before-window job range 71-97s) | 80.1s (job 97s) | (b) inconclusive at n=1 inside the shard's own noise band; also work-bound (W ~ 300s) |

Python gate authorization in this run: run created 07:49:19Z -> `pytest
coverage ratchet` completed 07:51:25Z = 126s (before p50 123s), bound by
`scripts-rs` 97s + 2s hop + 16s ratchet. Correction to the audit model: the
binding constraint on `npsz` and `rs` is total per-shard work divided by the
4 runner vCPUs, not the position of the 20-45s floor modules. (b) is
therefore `INSUFFICIENT_SAMPLE` and (a)/(c) are the measured cuts; CIP-001
as merged is expected to remove the `cloud al` and `scripts-jm` walls only,
leaving the mixed p50 roughly unchanged until the work-bound shards are
re-partitioned.

Unrelated in the same run: `Playwright P0-financial smoke (non-gating)` failed
at `bun install --frozen-lockfile` with `Fail extracting tarball for "next"`
(registry transient; the job is non-gating and did not run on the last six
`main` runs). Re-run requested for a clean PR check.

**Next safest candidate - CIP-005 (supersedes the (b) part of CIP-001):**
re-partition `scripts-npsz` / `scripts-rs` by measured per-module work into
the light shards (`scripts-gh` 16.7s, `rest` 12.6s, `scripts-daemons` 19.3s,
`rest-api` 19.5s, `scripts-ac` 28.8s) without changing shard count. Needs
per-module work on Linux: `--durations=25` truncates it and local Mac mini
profiles are contention-distorted (self_rewrite 488s locally vs 20s in CI).
Cheapest evidence: one PR-branch run with `--durations=0` on the two shards
(log-only, no workflow change on `main`), or a `--junitxml` artifact from
`py-tests`. Target: every scripts shard <= ~55s pytest step, python gate
<= ~85s, which makes the web gate (~106s) the wall as originally predicted.

### 2026-09-01 - audit - branch `ci-performance/2026-09-01`

- Audited range: `39bf6f5e..b1c0008a` (7 commits: PRs #210-#216). Runner
  state: dedicated clone `~/radon-weekend/radon-ci-performance`, lock
  `.weekend-runner.lock` owned by this cycle's wrapper (pid 31517), clean
  tree at `b1c0008a`, no orphaned stash, Mac mini load 6.1 (three sibling
  loops running; local timings diagnostic only).
- Changed CI/build/deploy surfaces in range: `.github/workflows/ci.yml`
  (+23/-4: CIP-001 xdist/lead modules, `changes` job `timeout-minutes: 5`
  R-509), `scripts/ci/path_filter.py` (+71: 60s bound on the green-run
  lookback; REL-179 routes `CLAUDE.md`/`AGENTS.md`/`SKILL.md` edits fail-CLOSED
  to both gates and test-referenced `.md` files to their focused modules),
  `cloud/scripts/{drift_audit.py,ib-gateway-control.sh,ib-gateway-remote-certs.sh}`,
  `cloud/.gitleaks.toml` (+18, allowlist repair). No Dockerfile, action pin,
  `needs:` edge, timeout, health wait or rollback path changed; every `uses:`
  in `ci.yml` / `app-images.yml` is still a 40-char SHA. Test inventory:
  +30 files / +2519 lines across `scripts/tests`, `cloud/tests`, `web/tests`.
- Gate closure re-verified on `origin/main`: `test_ci_deploy_concurrency.py`
  + `test_ci_gate_integrity.py` + `test_path_filter.py` = 78 passed (was 69;
  the range added 9 path-filter contracts). All 14 gate jobs remain in
  `deploy.needs` and in the `if:` expression; `branches/main/protection` still
  carries `enforce_admins` and no `required_status_checks` (unchanged; the
  workflow `needs` closure is the gate). Environment `Production`: branch
  policy only, no reviewer rule.

**Samples (last 30 `push` runs of `ci.yml` on `main`, 2026-08-30T05:31Z ..
2026-09-01T01:57Z; 20 success, 8 failure, 2 cancelled; queue delay 2-4s on
every run, no degraded run).**

| Class / cache | n | p50 | p95 | min-max | Notes |
|---|---|---|---|---|---|
| mixed, warm | 15 | 238s | 274s | 214-305s | 14 before CIP-001 merge + 1 after |
| web, warm | 3 | 212s | 282s | 206-282s | 282s run had a 60s gateway wait |
| docs, warm | 2 | 134s | 141s | 127-141s | |
| python, warm | 0 | - | - | - | |

Runner-seconds per mixed run p50 ~1690 (free plan, billable 0); latest
mixed run 1759 (the `scripts-i` shard grew, see below).

**Reliability record (not performance).** Five consecutive `main` runs failed
between 13:18Z and 15:46Z on 2026-08-31 (33396202710, 33396538584 = the
CIP-001 merge, 33396577585, 33407177442, 33410378860): all five at `Secret
scan (gitleaks)` on a `generic-api-key` hit in
`scripts/tests/test_rel180_loop_launchers.py` (fixture `.env` literal,
introduced by #210's range; allowlisted in `cloud/.gitleaks.toml` by
32318ae4 with `test_gitleaks_policy.py`), plus three merge-collision test
failures in 33410378860 (`scripts-npsz`, `scripts-rs`, `rest-api`). No
production deploy for 11h 10m (39bf6f5e 05:19Z -> 32318ae4 16:35Z). Not
caused by CIP-001: the CIP-001 run's own test jobs were cancelled by the next
push's per-shard `cancel-in-progress` groups, as designed.

**CIP-001 status: `INSUFFICIENT_SAMPLE` (1 of 5 comparable mixed after-runs).**
The one after-run, 33414421566, took 305s (deploy 139s, see the 59s gap
below), so the mixed p50 is unchanged at 238s. The shard-level effect is
confirmed on all four post-merge Linux `main` runs (33396577585, 33407177442,
33410378860, 33414421566; the failed runs still ran the shards to completion):

| Shard | before step p50 (n=15) | after step p50 (n=4) | Read |
|---|---|---|---|
| `cloud al` | 84s (job 97s) | 34s (job 47s) | (a) confirmed |
| `cloud mz` | 18s | 10s | (a) confirmed, off path |
| `scripts-jm` | 65s (job 80s) | 46s (job 60s) | (c) confirmed |
| `scripts-npsz` | 73s (job 86s) | 76s (job 90s) | (b) no effect, work-bound |
| `scripts-rs` | 63s (job 76s, max 99s) | 80s (job 94s, max 106s) | (b) no effect; grew with new `test_s*`/`test_r*` modules |
| `scripts-i` | 27s (max 48s) | 54s (job 68s) | grew ~+27s in range (new `test_i*` modules) |

Python gate authorization (run created -> `pytest coverage ratchet` done):
after-runs 129s, 134s, 141s, 129s vs before p50 123s. The gate is
`scripts-rs` job (94s p50) + 2s hop + ratchet 20s. Web gate p50 104s
(slowest Vitest job p50 78s + hop + ratchet 8s). The revert trigger
("python gate p50 > 105s") is technically hit but by the work-bound shards
CIP-001 (b) could never move, not by (a)/(c), which removed 50s and 20s of
work from their shards with zero flake in four Linux runs; no revert.

**Critical path, mixed (33414421566, 305s):** `Path filter` 8s -> `pytest
(scripts-rs)` 92s -> `pytest coverage ratchet` 20s -> auth at 129s ->
`Prestage VPS release` 24s -> `Deploy to VPS` 145s (of which a 59s silent
gap, below) -> 305s. **Critical path, web (33460698950, 206s):** `Path
filter` 8s -> `Vitest (shard 4/8)` 72s -> `Vitest coverage ratchet` 8s ->
auth at 95s (node image done at 94s: co-critical for web-only runs) ->
prestage 18s -> deploy 86s -> 206s.

**Deploy decomposition (normal, 33460698950, job 86s):** SSH + runner
extract + recover-only pass 6s; `sync-control-plane` 3s; preflight +
`deploy.sh` fetch of origin/main 4s (the ci.yml step already fetched: CIP-004
duplicate); prestaged release reuse + exact image pair verify <1s; gateway
ready + python env check 5s; `stop-clean` + checkout + `restart-managed`
15s; post-deploy gate <1s; stability window 40s (fixed); `sync-scheduled-units`
7s (CIP-004); edge config + prune + SSH close 4s.

**Deploy p95 drivers (4 of 20 successful deploys >= 123s).** 33342589109 and
33359662191: `wait_for_gateway_ready` ran all 12 attempts (60s, rail 9,
gateway not authenticated); 33414421566: a 59s silent gap between `IB gateway
data plane ready` 16:32:50 and `HEAD is now at 32318ae4` 16:33:49, i.e.
inside `stop_services_for_transition` (`sudo radon-deploy-root stop-clean`)
or the checkout in `activate_staged_release` (`deploy.sh:1027,1225-1236`);
normal runs take 5s here and neither step logs a duration. 33336928809:
worktree build 37s because no prestaged release existed (`Seeded web/.next`
path). Hypothesis for the 59s: a unit whose stop waited on its
`TimeoutStopSec` after 11h without a deploy; unverifiable from the log.

**Standing sweeps.** No new serialized `needs` edge; no duplicate checkout /
install added; caches at floor (uv hit, bun 225MB restore ~6s); node image
warm 60-62s (cold 250-340s, none in window); prepull 6-7s; no `latest` tag
fallback; path filter fail-closed strengthened (REL-179); Vitest shard
imbalance unchanged (step p50 48-59s, slowest per run p50 78s job).

#### CIP-005 - re-partition the work-bound `scripts-rs` / `scripts-npsz` shards by measured per-module work, with a durations instrument - SELECTED

- Evidence: `ci.yml:311-316` letter globs; `scripts-rs` step 74-89s across
  the four post-merge runs (1080-1089 tests; `--durations=25` top files:
  `test_rel137_weekend_wrapper_survivability.py` 45.3s serial floor,
  `test_run_flow_refresh_wrapper.py` 11.8s, `test_run_portfolio_refresh_retry.py`
  11.1s, `test_run_signals_refresh_wrapper.py` 9.6s, `test_robinhood_priority.py`
  9.0s, `test_robinhood_client.py` 4.0s; top-25 sum 93s of ~300s total
  work), `scripts-npsz` step 69-76s (1472-1486 tests; `test_vixcor.py`
  22.3s, `test_weekend_wrapper_self_rewrite.py` 20.3s, `test_path_filter.py`
  6.0s; top-25 sum 51s of ~290s). Light shards with headroom: `scripts-gh`
  18s step, `rest` 13s, `scripts-daemons` 21s, `rest-api` 30s, `scripts-df`
  32s, `scripts-ac` 36s. `--durations=25` covers under a third of each
  shard's work, so the remaining ~200s per shard is invisible; last night's
  lesson says do not re-partition on that.
- Hypothesis: (1) instrument first: add `--junitxml` to the `py-tests` run
  line and ship the XML inside the existing `pytest-coverage-<shard>`
  artifact (same upload step, +~50KB, 0s on the critical path; retention 3
  days); the PR's own `pull_request` run then yields exact per-module work
  on Linux for all ten shards. (2) Move whole module groups by bash glob
  negation, keeping ten shards and the recursive-union contract: `test_ru*.py`
  (~33s) to `rest`, `test_ro*.py` (~13s) to `scripts-gh`, `test_vi*.py` and
  `test_we*.py` (~50s) to `scripts-daemons` / `rest-api`, and whichever
  further modules the junit data shows are needed to bring every scripts
  shard to <= ~60s step (`test_r[!ou]*.py test_s*.py`, `test_v[!i]*.py
  test_w[!e]*.py` style rows; `fnmatch` in `_partition` and `glob` in
  `_expand_shard_paths` both honour `[!x]`). Predicted python gate 129s ->
  ~97-101s (slowest scripts job ~75-79s + 2s + 20s), which puts the web gate
  (104s) on the wall: mixed p50 238s -> ~215-220s (-18 to -23s, -8 to -10%),
  p95 274s -> ~255s. Borderline for the 10% bar alone; with CIP-004 (-8 to
  -10s on every class) it clears it. Each shard's effect is separately
  measurable from job timestamps.
- Shard count unchanged (10 + 3); runner-seconds ~0 (work moves, no new
  job); junit adds <1s per shard.
- Affected paths: `.github/workflows/ci.yml` (`py-tests` matrix rows, run
  line, upload path), `scripts/tests/test_ci_deploy_concurrency.py`
  (`test_pytest_shards_then_combines_coverage_ratchet` pins `upload.path ==
  ".coverage"`; `PYTEST_SHARD_LEAD_MODULES` names the lead modules that move
  with their glob; the per-row overlap guard and both union contracts are the
  safety net), `scripts/ci/merge_vitest_coverage.py` untouched, `py-coverage`
  `find -name .coverage` untouched.
- Safety: no test removed, skipped or deselected; union == recursive
  inventory contracts stay green; coverage combine reads `.coverage` by name
  so a sibling XML in the artifact cannot change the ratchet;
  `DOCS_CONTRACT_BASE` fetch-depth pin (`scripts-df`) untouched. Risk: a
  moved module that depends on shard-local state (none of the candidates
  share fixtures across files; `loadfile` keeps each module on one worker).
- Revert trigger: any moved module failing on its new shard in the first
  five `main` runs, or `scripts-rs`/`npsz` step p50 not dropping below 65s.
- Validation: five comparable mixed warm after-runs vs the 15 before-runs
  above; per-shard job wall + junit per-module sums from
  `gh api .../runs/<id>/jobs` and the artifacts.

#### CIP-004 - deploy tail (`sync-scheduled-units` 7s + duplicate fetch 3-4s) - DEFERRED (second)

Unchanged from 2026-08-31; measured again tonight at 7s + 4s on 33460698950.
On every class's critical path; root-helper edit ships via
`sync-control-plane`. Next after CIP-005.

#### CIP-006 - deploy p95: silent 59s `stop-clean`/checkout gap and 60s gateway wait are unattributable from the log - DEFERRED (observability only)

`deploy.sh` logs nothing between `IB gateway data plane ready` and `HEAD is
now at` (`deploy.sh:1225-1236` -> root helper `stop-clean` ->
`activate_staged_release`), and `wait_for_gateway_ready` logs neither
`auth_state` nor `port_listening` per attempt. Log the per-unit stop
duration and the observed gateway fields; no wait shortened (rail 9). Worth
~0s p50, but it is the only way to classify the 4/20 slow deploys.

#### CIP-002 (node image `scripts/` copy boundary) / CIP-003 (Vitest balance) - DEFERRED

Node image (94s from run start) is co-critical only for web-only runs (3 of
20); in mixed runs it finishes ~35s before the python gate. Vitest slowest
job p50 78s vs shard p50 67-70s: ~8-10s of imbalance, becomes the wall only
after CIP-005 lands.

#### Rejected / no-change

- Gateway-wait early exit (rail 9) and a shorter stability window (rail 9):
  never.
- Dropping the `py-coverage` barrier VM (20s hop) to a check inside the
  slowest shard: the ratchet must see all ten data files; T-160.
- Increasing to 11 scripts shards: +35-45 runner-seconds per run for the
  same effect CIP-005 gets by moving work into shards with 40-60s of
  headroom.

Outcome for tonight: `VALIDATING` pending remediate (CIP-005). CIP-001
remains `INSUFFICIENT_SAMPLE` (1/5 mixed after-runs; shard effects
confirmed n=4). Residual bottleneck after CIP-005: web gate ~104s in
parallel with the python gate ~100s, then prestage ~18s + deploy ~86s (40s
fixed), with CIP-004 the next cut.

### 2026-09-02 - audit - branch `ci-performance/2026-09-02`

- Audited range: `b1c0008a..db25990d` (25 commits: PRs #217-#239 plus three
  direct fixes). Runner state: dedicated clone
  `~/radon-weekend/radon-ci-performance`, exclusive lock
  `/tmp/radon-ci-performance.lock` owned by this cycle, clean tree at
  `db25990d`, no orphaned stash.
- Changed CI/build/deploy surfaces in range: `.github/workflows/ci.yml`
  (+76/-15 across #217 CIP-005 instrument, #224 docs-ownership gate repair,
  #229 PR-output formatter, #230 safety interlocks),
  `cloud/scripts/setup-vps.sh` (+3), `test_ci_deploy_concurrency.py` (+9),
  `test_path_filter.py` (+1). Range diff has NO `needs:`, `uses:`, `timeout`,
  `continue-on-error` or `if:` gate-relevant change; all action pins remain
  40-char SHAs. Gate closure re-verified on `origin/main`:
  `test_ci_deploy_concurrency.py` + `test_ci_gate_integrity.py` +
  `test_path_filter.py` = 78 passed (matches last night).
- CIP-005 stage 1 (junitxml instrument) merged 12:26Z as `5664cb3c` (#217);
  the re-partition was deliberately staged behind the junit data, so no gate
  movement was expected or observed. The instrument works: all ten
  `pytest-coverage-<shard>` artifacts on run 33598078630 carry
  `pytest-junit.xml`.

**Samples (25 `push` runs on `main` since last audit, 2026-09-01T09:44Z ..
2026-09-02T06:15Z; 19 success, 5 failure, 1 cancelled; queue delay 2-4s).**

| Class / cache | n | p50 | p95 | min-max | Notes |
|---|---|---|---|---|---|
| mixed, warm | 17 | 255s | ~343s | 235-344s | all post-CIP-001; 15 post-CIP-005-stage-1 |
| web, warm | 2 | 267s | - | 231-303s | 303s run: node image 129s cold-ish layer |
| docs, warm | 0 | - | - | - | |

Decomposition of the mixed set: gate-auth (all required gates done) p50 135s
(126-160s, stable); deploy job p50 94s but p95 ~180s. **The apparent total
p50 regression (238s -> 255s) is entirely deploy-side: 7 of 20 successful
deploys ran 113-182s against an 84-96s floor.** Decoded logs for the 182s
(job 99995952645), 149s (100142669277) and 145s (100071713121) deploys show
each burned the full bounded 60s `wait_for_gateway_ready` ("Gateway not
ready (attempt 1/12)" at +12s, `HEAD is now at` at +77s; rail 9, wait
preserved); the 182s run additionally lost ~90s after `Scheduled units match`
to a "known-good caddy reload reconciliation also failed" retry path. The
slow deploys cluster behind back-to-back merges (19:26-20:14Z, 23:44-23:55Z,
05:59Z) - consistent with each deploy's restart leaving the gateway
data-plane not-ready for the next deploy minutes later. Every deploy log also
prints "install-units: ignoring malformed manifest line" x6 (benign-looking,
unattributed; noted for CIP-006).

**Critical path, fast mixed (33598078630, 235s):** `Path filter` 8s ->
`pytest (scripts-rs)` 99s job (ends +112s) -> `pytest coverage ratchet` 18s
(ends +133s = gate auth) -> `Prestage VPS release` 12s -> `Deploy to VPS`
84s -> 235s. Web gate ends +109s: the python gate is the wall by ~24s on
every mixed run.

**Exact per-module work (junit, run 33598078630, Linux).** Shard work totals:
`scripts-npsz` 157s, `scripts-rs` 115s, `scripts-i` 61s, `scripts-ac` 34s,
`scripts-jm` 33s, `rest-api` 30s, `scripts-df` 23s, `scripts-daemons` 14s,
`scripts-gh` 6s, `rest` 3s (total 476s; job wall = ~22-25s setup + makespan
+ upload). Serial floors (loadfile keeps a module on one worker):
`test_vixcor` 50.4s, `test_rel137_weekend_wrapper_survivability` 47.2s,
`test_weekend_subscription_only` 36.5s, `test_weekend_wrapper_self_rewrite`
29.3s, `test_ib_gateway_remote_serve` 28.4s, `test_leap_garch_no_duplicate_scan`
21.3s, `test_ib_gateway_remote` 20.2s. Movable bulk: `test_we*` in npsz
~72s over 7 files; `test_ru*` in rs ~39s over 6 files; `test_ro*` in rs
13.5s over 2 files. `--durations=25` had covered <1/3 of this; the junit
data settles the 2026-08-31 work-bound-vs-tail-bound question: npsz and rs
are BOTH - big movable bulk plus one ~50s floor each.

#### CIP-005 stage 2 - move `test_we*` out of npsz and `test_ro*`/`test_ru*` out of rs - SELECTED

- Hypothesis: with floors at 50.4s (vixcor, stays in npsz) and 47.2s
  (rel137, stays in rs), the best achievable slowest scripts makespan is
  ~52s. Moves: `test_we*.py` (~72s work) -> `scripts-gh` (6s work, job 28s);
  `test_ru*.py` (~39s) -> `rest` (3s, job 28s); `test_ro*.py` (13.5s) ->
  `scripts-daemons` (14s, job 34s). New globs stay letter-expressible:
  npsz drops `test_we*` via `test_[t-v]*.py test_w[!e]*.py test_[x-z]*.py`;
  rs becomes `test_r[!ou]*.py test_s*.py` (rel137 = `test_re…` stays; both
  `fnmatch` and shell `[!x]` semantics already proven in the 09-01 plan).
  No `test_q*.py` exists; union contract remains fail-closed.
- Predicted: npsz work 157->85 (makespan ~52s, job ~77s from 95-118s); rs
  work 115->63 (makespan ~48s, job ~73s from 95-99s); gh job ~60s, rest
  ~40s, daemons ~36s; slowest scripts job ~77s, python gate ~133s ->
  ~110-115s; gate-auth ~135s -> ~110-113s (web gate 109s becomes co-wall).
  Mixed p50 -20 to -25s on the deploy-clean floor (235s -> ~212s). Clears
  10% + 15s against the deploy-clean before-set; deploy-noise runs excluded
  by class rules.
- Shard count unchanged (10); runner-seconds ~0 (work moves); no new job.
- Affected paths: `ci.yml` py-tests matrix rows (npsz, rs, gh, rest,
  daemons), `test_ci_deploy_concurrency.py` (lead-module pin
  `PYTEST_SHARD_LEAD_MODULES`: `test_weekend_subscription_only` leads gh;
  vixcor keeps leading npsz, rel137 keeps leading rs; per-row overlap guard
  and both union contracts are the safety net).
- Safety: no test removed/skipped/deselected; union == recursive inventory
  contract stays green; `DOCS_CONTRACT_BASE` fetch-depth pin (scripts-df)
  untouched; coverage combine (`find -name .coverage`) untouched. Risk:
  a moved module depending on shard-local state (candidates share no
  cross-file fixtures; `loadfile` keeps each module on one worker).
- Revert trigger: any moved module failing on its new shard in the first
  five `main` runs, or npsz/rs job p50 not dropping below 85s.
- Validation: five deploy-clean mixed warm after-runs vs the deploy-clean
  before-set (235,236,237,240,247,250,252,254 - n=8 available); per-shard
  job walls from `gh api .../runs/<id>/jobs`.

#### CIP-001 - REJECTED as a performance win; kept merged

n=17 mixed after-runs (>=5): total p50 255s vs 238s before fails every
acceptance bar, but the regression is attributable to the deploy gateway-wait
cluster, not the change: gate-auth p50 135s vs python gate 123-129s before
(within noise). The real delivered effect: `cloud al` job 97s -> 44-48s
(xdist), off the critical path, and a real 40.0s test burn removed. No gate
weakened, runner minutes flat, nothing to revert. Closed.

#### CIP-004 (deploy tail: `sync-scheduled-units` 7s + duplicate fetch) - DEFERRED (next after CIP-005 stage 2)

Measured again at ~7s + 4s on 33598078630. Unchanged plan.

#### CIP-006 - deploy p95 observability - DEFERRED, evidence grew

Now 7/20 deploys (was 4/20) hit the 60s gateway wait; the caddy
reconciliation retry (90s, one run) and the x6 "malformed manifest line"
warnings are new unattributed signals. Per-attempt `auth_state` +
`port_listening` logging and per-unit stop durations remain the ask; rail 9
forbids any wait change. Worth ~0s p50; explains ~all of current p95.

#### Reliability record (not performance)

5 failures in range: 33507703819 (#217 merge - non-gating Playwright smoke
only; deploy of `5664cb3c` still completed), 33531056589, 33563795226,
33574419366, 33577243449; 1 cancelled (33585420153, superseded by the next
push, as designed). Not performance samples.

Outcome for tonight: `VALIDATING` pending remediate (CIP-005 stage 2).
Residual bottleneck after stage 2: python gate ~110-115s and web gate ~109s
co-wall, then prestage + deploy 84-96s floor with a 60s gateway-wait p95
tail (CIP-006), with CIP-004 the next p50 cut.

### 2026-09-03 - audit - branch `ci-performance/2026-09-03`

- Audited range: `db25990d..0202e32d` (17 commits: PRs #240-#255 plus two
  direct pushes). Runner state: dedicated clone, exclusive lock
  `/tmp/radon-ci-performance.lock` owned by this cycle, clean tree, no
  orphaned stash.
- **CIP-005 stage 2 was selected on 09-02 but never implemented**: PR #241
  merged as the audit ledger only; `.github/workflows/ci.yml` and
  `test_ci_deploy_concurrency.py` are unchanged in the whole range (range
  has zero `.github/` diffs). The 09-02 remediate phase produced no ledger
  entry, no commit, and no PR - a silent remediate no-op. Stage 2 carries
  forward as tonight's remediate item unchanged.
- Changed CI/build/deploy surfaces: none. Changed test surfaces: 9 new
  python test files (notably `test_rel189_*` and `test_rel191_*` land in
  `scripts-rs`, adding work to the already-heaviest movable shard, and
  `test_iv_spread` lands in `scripts-i`). Union contract remains fail-closed;
  gate closure re-verified locally: 78 contract tests passed
  (`test_ci_deploy_concurrency` + `test_ci_gate_integrity` +
  `test_path_filter`).

**Samples (15 `push` runs on `main` in range, 2026-09-02T13:43Z ..
2026-09-02T23:34Z; 12 success, 1 failure, 2 cancelled; queue delay 2-4s).**

| Class / cache | n | p50 | p95 | min-max | Notes |
|---|---|---|---|---|---|
| mixed/py, warm | 11 | 267s | ~400s | 231-408s | 390s+408s runs follow back-to-back merges (gateway wait) |
| web, warm | 1 | 262s | - | - | 33667322880 |

**Critical path (33695685875, 252s, mixed warm):** `Path filter` 7s ->
`pytest (scripts-rs|scripts-npsz)` 98s (end +108) -> `pytest coverage
ratchet` 19s (end +130) -> `Prestage` 23s -> `Deploy to VPS` 89s -> 248s.
Identical shape to 09-02: the npsz/rs pair is still the python-gate wall,
web gate ends +85, exactly what stage 2 targets. Deploy tail cluster
(gateway wait) reproduced on 33682386903 (390s) and 33694683198 (408s),
both minutes after a prior deploy - CIP-006 evidence grows again.

#### CIP-005 stage 2 - RE-SELECTED for tonight's remediate

Plan, predicted savings, safety rails, revert trigger, and validation set
are unchanged from the 2026-09-02 entry (moves: `test_we*` -> scripts-gh,
`test_ru*` -> rest, `test_ro*` -> scripts-daemons; predicted gate-auth
~135s -> ~110-113s, deploy-clean mixed p50 -20 to -25s). The new
`test_rel18*/19*` files stay in `scripts-rs` by glob (`test_r[!ou]*.py`)
alongside rel137 - consistent with the plan.

#### CIP-004, CIP-006 - DEFERRED, unchanged plans

Outcome for tonight: `VALIDATING` pending remediate (CIP-005 stage 2).
Residual bottleneck after stage 2: python/web gate co-wall ~110s, then the
84-96s deploy floor with the 60s gateway-wait p95 tail (CIP-006).

#### Lesson

- 2026-09-03: a remediate phase can no-op silently (09-02 selected stage 2,
  wrapper posted phases, but no commit/PR/ledger entry ever appeared). The
  next audit must always diff `origin/main` for the previously selected
  change before assuming it merged - which tonight's step 2 caught.

### 2026-09-04 - audit - branch `ci-performance/2026-09-04`

- Audited range: `0202e32d..2b936ebc` (50 commits: PRs #256-#270 plus direct
  pushes). Runner state: dedicated clone (`.radon-weekend-runner` present),
  lock `/tmp/radon-ci-performance.lock` taken by this cycle, clean tree, no
  orphaned stash. `flock` is unavailable on macOS zsh here - the lock is the
  pid file plus the wrapper's own serialization.
- **CIP-005 stage 2 still has not landed - third consecutive silent remediate
  no-op.** PR #258 (the 09-03 audit) merged as ledger text only. The only
  `.github/workflows/ci.yml` diff in the whole 50-commit range is the security
  loop's caddy pin + sha512 verification (`dd81b66c`); the `py-tests` matrix
  rows are byte-identical to 09-01. Stage 2 carries forward again.
- Changed CI/build/deploy surfaces: `ci.yml` caddy install pinned to v2.11.4
  with checksum verification (rail 10 strengthened, not weakened; `GH_TOKEN`
  dropped from that step). `cloud/services/radon-mcp.service`,
  `radon-ib-gateway-remote.service`.
- Changed test surfaces: 35 new test files. 25 land in `scripts-rs`
  (`test_rel1xx/2xx`), 1 new `test_weekend_prune.py` lands in the `test_we*`
  group of `scripts-npsz`, 4 in `rest-api`, 5 in `cloud`. Both already-heaviest
  movable shards grew, which is exactly what stage 2 relieves.
- Gate closure re-verified locally: 78 contract tests passed
  (`test_ci_deploy_concurrency` + `test_ci_gate_integrity` + `test_path_filter`,
  8.44s). Every required gate remains inside the deploy dependency closure.

**Samples (13 successful `push` runs on `main` in range,
2026-09-03T13:20Z .. 2026-09-04T02:53Z; 6 failures, 2 cancelled excluded;
queue delay 2-4s, reported separately and not counted as a win).**

| Class / cache | n | p50 | p95 | min-max |
|---|---|---|---|---|
| mixed/py, warm | 13 | 271s | ~500s | 232-623s |

Prior window p50 was 267s (09-03) and 255s (09-02): flat, no drift, and no
merged perf change to attribute movement to. 33776679233 (623s) and
33786454504 (381s) are back-to-back-merge deploy-gateway-wait runs (CIP-006).

**Critical path (33830913939, 261s, mixed warm; identical shape on
33828589948 / 271s):**
`Path filter` 9s (+10) -> `pytest (scripts-npsz)` 119s (+131) ->
`pytest coverage ratchet` 21s (+155) -> `Prepull exact app images` 8s (+165)
-> `Prestage VPS release` 12s (+169) -> `Deploy to VPS` 86s (+259).

Every other gate finishes by +115: `scripts-rs` +115 (101s), Vitest tail
+101, `cloud edge` +93, node image +38. `scripts-npsz` alone is the wall and
is now 119-124s, up from the 95-118s band measured on 09-01/09-02 - the
shard grew as predicted when new modules landed.

#### CIP-005 stage 2 - RE-SELECTED (unchanged) for tonight's remediate

Moves, predicted savings, safety rails, revert trigger and validation set are
unchanged from the 2026-09-02 entry: `test_we*` -> `scripts-gh` (ends +51,
huge headroom), `test_ru*` -> `rest` (ends +44), `test_ro*` ->
`scripts-daemons` (ends +52). New glob shapes: npsz becomes
`test_[t-v]*.py test_w[!e]*.py test_[x-z]*.py`; rs becomes
`test_r[!ou]*.py test_s*.py`. The explicit lead-module pin
`scripts/tests/test_weekend_wrapper_self_rewrite.py` must move off the npsz
row with the glob, and `PYTEST_SHARD_LEAD_MODULES` in
`test_ci_deploy_concurrency.py` updated to match. `test_weekend_prune.py`
(new this range) rides the same move. Union contract stays fail-closed;
no test removed, skipped, deselected or reweighted.
- Predicted: npsz job 119s -> ~77-85s, rs 101s -> ~75-85s, coverage ratchet
  start pulled ~35-40s earlier; mixed warm p50 271s -> ~232-240s. Clears the
  10% + 15s bar. Shard count unchanged (10), runner-seconds ~flat.

#### CIP-004 (deploy tail: `sync-scheduled-units` + duplicate fetch) - DEFERRED

Unchanged plan. Deploy floor is now a stable 86-88s across all three sampled
runs, so this remains the next p50 cut after stage 2.

#### CIP-006 - deploy p95 observability - DEFERRED, evidence unchanged

2 of 13 successful runs in range carry the gateway-wait tail (623s, 381s),
both minutes after a prior deploy. Still ~0s p50, still ~all of p95.

#### Reliability record (not performance)

6 failures in range (33797295439, 33784525460, 33775920298, 33774024242,
33771071956, 33769343624, 33767456463), 2 cancelled (33767296903,
33767212158 - superseded pushes, as designed). Not performance samples.

Outcome for tonight: `VALIDATING` pending remediate (CIP-005 stage 2).
Residual bottleneck after stage 2: python/web gate co-wall ~100-110s, then
the 86-88s deploy floor (CIP-004) with the gateway-wait p95 tail (CIP-006).

#### Lesson

- 2026-09-04: the 09-03 lesson ("diff `origin/main` for the previously
  selected change before assuming it merged") fired again and caught a third
  no-op. Add the stronger rule: when an audit re-selects the SAME `CIP-###`
  for a third consecutive night, the audit must state the no-op explicitly in
  the PR body's Issue section so the operator sees the loop is not shipping,
  rather than burying it in the ledger.

### 2026-09-04 - remediate - branch `ci-performance/2026-09-04`

- **CIP-005 stage 2 IMPLEMENTED** (first time after three consecutive silent
  no-ops on 09-02, 09-03 and the 09-04 audit's carry-forward).
- Changed files: `.github/workflows/ci.yml` (py-tests matrix rows
  `scripts-gh`, `scripts-npsz`, `scripts-rs`, `scripts-daemons`, `rest`),
  `scripts/tests/test_ci_deploy_concurrency.py`
  (`PYTEST_SHARD_LEAD_MODULES`).
- Moves: `test_we*` npsz -> `scripts-gh` (8 modules, gh ended +51 with
  headroom); `test_ru*` rs -> `rest` (6 modules, rest ended +44);
  `test_ro*` rs -> `scripts-daemons` (2 modules, daemons ended +52).
  New globs: npsz `test_[n-p]*.py test_[t-v]*.py test_w[!e]*.py
  test_[x-z]*.py`; rs `test_r[!ou]*.py test_s*.py`. Lead pins follow their
  modules: `test_weekend_wrapper_self_rewrite.py` now leads `scripts-gh`,
  the three `test_run_*_refresh` modules now lead `rest`, `test_vixcor.py`
  keeps leading npsz and rel137 keeps leading rs.
- Red/green: `PYTEST_SHARD_LEAD_MODULES` updated first ->
  `test_heavy_pytest_modules_lead_their_shard` FAILED (`scripts-gh: got
  ['scripts/tests/test_[g-h]*.py']`); after the `ci.yml` edit, 78 passed in
  8.32s (`test_ci_deploy_concurrency` + `test_ci_gate_integrity` +
  `test_path_filter`). Union/partition contract green: no overlap, no
  unsharded file, no unsharded subdirectory.
- Every glob resolves against the checkout (27 / 79 / 80 / 4 / 9 tokens for
  gh / npsz / rs / daemons / rest), so an empty expansion cannot silently
  drop a module; an unmatched literal would fail pytest closed.
- Safety: no test removed, skipped, deselected or reweighted; shard count
  unchanged (10); no `needs` edge, coverage ratchet, provenance, health,
  stability-window or rollback surface touched; runner-seconds ~flat (work
  moves between existing jobs).
- Predicted: npsz job 119s -> ~77-85s, rs 101s -> ~75-85s, coverage ratchet
  starts ~35-40s earlier, mixed warm p50 271s -> ~232-240s.
- Revert trigger: any moved module failing on its new shard in the first
  five `main` runs, or npsz/rs job p50 not dropping below 85s.
- Validation: five deploy-clean mixed warm after-runs vs the deploy-clean
  before-set; per-shard job walls from `gh api .../runs/<id>/jobs`.
- Outcome: `VALIDATING` (implemented, awaiting post-merge samples).
- Residual bottleneck: python/web gate co-wall ~100-110s, then the 86-88s
  deploy floor (CIP-004) with the gateway-wait p95 tail (CIP-006).

### 2026-09-05 - audit - branch `ci-performance/2026-09-05`

- Audited range: `2b936ebc..391aaaea` (118 commits: PRs #272-#301). Runner
  state: dedicated clone verified, stale lock reclaimed, tree clean at
  `391aaaea`.
- CI/deploy surface delta: `ci.yml` (CIP-005 stage 2 shard globs via PR
  #272; e2e job gained a second `bun run build` with
  `NEXT_PUBLIC_RADON_DEMO=1` plus `demo-workstation-data.spec.ts` via
  PR #293), `app-images.yml` (`load: true` + hardened Playwright smoke on
  the python image), python image gained Playwright Chromium (PR #285).
- Samples (mixed warm, deploy-clean `main` successes, workflow createdAt ->
  Deploy-to-VPS completedAt, 16 runs 33890263669..33938475254):
  231 234 236 243 244 244 244 252 254 260 262 263 271 287 306 319 —
  **p50 253s, p95 ~316s**. Push-to-production is UNCHANGED by the delta;
  Deploy job wall 72-96s (CIP-004 floor intact). Queue delay negligible on
  all 16.
- Failed/cancelled runs (33934165998, 33933705972, 33932478962,
  33929247644, 33911589496, 33892597609, 33892580953, 33892167740,
  33891258539) excluded from performance windows; counted for reliability
  only.
- **New bottleneck evidence — e2e container apt tail.** The
  `Playwright P0-financial smoke` job runs
  `apt-get update -qq && apt-get install -y -qq unzip`
  (`.github/workflows/ci.yml:725`, needed because
  `mcr.microsoft.com/playwright:v1.58.2-jammy` ships without unzip and
  setup-bun requires it). Step norm is 5-8s (runs 33890263669: 5s,
  33917247042: 8s, 33934337009: 7s) but both 2026-09-05 successes hit a
  degraded mirror: **319s in 33938475254 and 448s in 33935134766**, tripling
  the job (130s norm -> 463/594s run wall). Off the deploy critical path
  (job is non-gating for deploy) but it inflates PR time-to-green (the
  deliver phase's watch clock) and runner minutes by up to ~7.5 min/run
  with no retry/timeout defense.
- Demo rebuild cost inside the same job measured at 22s build + 5s spec —
  real but minor; the 09-05 wall explosions are apt, not the demo build.
- CIP-005 stage 2 post-merge shard walls so far (2 mixed samples):
  scripts-npsz 91s (was 119s, on target), scripts-gh 91s, scripts-rs
  103s/108s — **rs is above the 85s revert-trigger line**; 3 more samples
  needed before invoking it. Outcome stays `VALIDATING`
  (`INSUFFICIENT_SAMPLE` for acceptance).

#### CIP-007 - remove the unbounded apt-get network dependency from the e2e job (selected)

- Hypothesis: provisioning unzip via apt with no timeout/retry exposes every
  e2e run to Ubuntu-mirror latency; bounding or eliminating the network
  fetch caps a measured 319-448s tail back to <10s.
- Options for remediate, smallest first: (a) add
  `-o Acquire::Retries=3 -o Acquire::http::Timeout=15` and a step
  `timeout-minutes`; (b) eliminate apt entirely by extracting bun without
  unzip (hermetic); either preserves the pinned setup-bun action and all
  gates.
- Expected: ~0s on the p50 (step norm already 5-8s), up to ~440s off the
  tail per affected run; runner-minute reduction on degraded days;
  confidence high (step-level timestamps), effort low, risk low
  (provisioning-only, no test or gate change).
- Validation: step duration across the next five mixed runs; no e2e spec
  count change.

#### CIP-008 - demo workstation rebuild serialized in e2e (ranked second)

- 27s serialized per e2e run (`ci.yml:764-776`). Candidate: reuse
  `web/.next/cache` between the two builds or split into a parallel job.
  Deferred below CIP-007 tonight: small, and off the deploy path.

#### CIP-004 / CIP-006 - unchanged evidence, remain DEFERRED

- Deploy floor 72-96s and gateway-wait tail unchanged across the 16-run
  window.

Outcome for tonight: audit `DONE`; CIP-007 handed to remediate (with
CIP-008 behind it). Residual bottleneck for the production clock stays the
python/web gate co-wall then the deploy floor (CIP-004).

### 2026-09-05 - remediate - branch `ci-performance/2026-09-05`

- **CIP-007 IMPLEMENTED** (commit `1bb2cfa2`). Both apt-get invocations in
  the e2e job's unzip provisioning step now carry `Acquire::Retries=3` and
  `Acquire::http::Timeout=15`, and the step is capped at
  `timeout-minutes: 3`. A degraded mirror now fails fast (~90s worst case
  with retries) instead of running 319-448s inside the job's 25-minute
  budget.
- Changed files: `.github/workflows/ci.yml` (e2e-financial-smoke apt step),
  `scripts/tests/test_ci_gate_integrity.py`
  (`test_e2e_apt_provisioning_is_bounded`, added first).
- Red/green: new contract test FAILED against the unbounded step
  (`Acquire::Retries` assertion), green after the `ci.yml` edit. Full
  workflow contract set: 79 passed in 9.23s
  (`test_ci_gate_integrity` + `test_ci_deploy_concurrency` +
  `test_path_filter`). YAML parse clean.
- Safety: provisioning-only; no test, spec, gate, `needs` edge, coverage,
  provenance, health, stability-window or rollback surface touched.
  setup-bun pin unchanged. Runner minutes strictly reduced on degraded
  days, unchanged otherwise.
- Expected: ~0s p50 effect (step norm 5-8s); caps the measured 319-448s
  tail; up to ~7.5 runner-min/run saved on degraded days.
- Revert trigger: the apt step failing on a healthy mirror in any of the
  next five `main` runs.
- Validation: step duration + conclusion across the next five mixed runs;
  spec count unchanged (21 e2e specs).
- Outcome: `VALIDATING`.
- **CIP-008 DEFERRED** with rationale: `web/.next/cache` is already shared
  between the two builds (same workspace, cache survives the rebuild), so
  the 22s is post-cache demo-boundary compile; a parallel-job split would
  duplicate checkout + bun install + build (~60s+ runner time) to save 27s
  off a non-gating job. No safe material change tonight.
- CIP-005 stage 2 remains `VALIDATING` (rs shard 103/108s vs 85s trigger;
  needs 3 more post-merge samples). CIP-004 / CIP-006 unchanged, DEFERRED.
- Residual bottleneck: python/web gate co-wall ~100-110s, then the 86-88s
  deploy floor (CIP-004).
