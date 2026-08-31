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
