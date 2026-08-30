# Lessons

## 2026-08-29 — Botocore Config retries are not application-level retry

- Page `29c8a560`: `radon-db-backup` dumped 100 tables then paged P1 on
  `ConnectionClosedError` of a 576 MB B2 PUT. `_s3_client` already had
  `retries max_attempts=3 mode=standard`.
- Media-backup (page `02ccb70e`) treated those botocore bounds as enough.
  An injected client and a spent-retry multipart PUT still fail the
  oneshot. Wrap LIST/PUT/HEAD/DELETE in `call_s3_with_retry`.

## 2026-08-29 — Container newsfeed needs the host Playwright revision

- Page `3e952746`: `radon-newsfeed` crash-looped after the container
  cutover (`NRestarts=21`, `Result=exit-code`). Turso
  `newsfeed-scraper` said `Executable doesn't exist at
  /ms-playwright/chromium_headless_shell-1217`.
- `bun x playwright install` from WORKDIR `web/` is not the repo-root
  playwright the scraper imports. Host deploy already had 1217 in
  `~/.cache/ms-playwright`. Bind that cache onto `/ms-playwright`;
  install in the image via `./node_modules/.bin/playwright` from
  `/home/radon/radon`.
- A new GHCR tag is not required to recover (R-234): the wrapper is
  control-plane and takes effect on the next unit restart.

## 2026-08-29 — Sidecar Restart=always is activating, not just down

- Page `0b7726f8` moved newsfeed/monitor `down` onto DEPENDENCY_UNITS.
- Page `344f0592` sampled the same storm in `activating`. The starting
  check still scanned every unit, so overall_state stayed `starting` and
  the off-box probe wrote `aggregate_down` while ping and `/sign-in` were 200.
- Classify sidecar `starting` like sidecar `down`: degraded, not an edge
  outage. Serving-path starting stays `starting`.

## 2026-08-27 — Ticket max gain is the limit fill, not mid minus spread

- CBRS 40× short $182.5 put, limit $4: TOTAL $16,000 CR, MAX GAIN $12,248.
- FU7 folded `estimateRoundTripCost` (quoted half-spread on entry + estimated exit) into `useOrderRisk` on top of the limit. Bid 2.50 / ask 4.30 → $3,752 haircut.
- The limit is the fill. At-expiry max is not an exit trade. Leave round-trip cost on `computeOrderRisk(..., { roundTripCost })` for mid-fill backtests only.

## 2026-08-25 — Kill-before-green beats a successor in_flight journal

- Page `a70a393e`: latched `radon-bpi` `Result=signal` from 12:41Z stop-clean
  (green 15:14Z) was re-paged P1 at 16:20Z when a successor deploy wrote a
  fresh transition journal. `in_flight` short-circuited past kill-before-green;
  the 60-min age cap then failed on the old kill.
- Evaluate kill-before-green (24h oneshot horizon) before the in_flight branch.
  A fresh journal only owns kills inside the single-deploy window that are not
  already explained by a post-kill green marker.
- Discriminator: `InactiveEnterTimestamp` before green-marker mtime within 24h
  + sibling oneshot `Result=signal` + edge `/health/lite` up → classifier only,
  do not restart.

## 2026-08-23 — A persisted 1025/permanent row is still a live lockout

- `75ded753` stopped classifying NEW 1025s as permanent. It did not rewrite
  the 2026-08-21 13:58Z row (`class=permanent`, `next_attempt_at` Monday
  08:00 ET) and the sidecar is only armed by `record_lockout`.
- `flex_embargo.active_until` must reconstruct from Turso when
  `data/flex_token_embargo.json` is missing. Deadline is
  `last_attempt_finished_at + 7d`, not the stored 08:00 window. 1012 stays
  not-lockout.
- `is_due` must consult `flex_embargo.is_blocked` or Monday 08:00 ET
  SendRequests and extends 1025. `/orders` blotter rehydrate shares the token.
- Do not SendRequest to "check". Recover with `--from-file`. Do not heartbeat
  `cash-flow-sync` on reconstruct (that would stamp `last_attempt` as now and
  slide the 7-day clock).
## 2026-08-23 — Verified-journal recover must not die on unit sync

- `sync-scheduled-units` requires HEAD == GitHub main tip. A verified journal is often one SHA behind a tip that moved during the 40s gate. Fatal sync left the journal in place and the next deploy exited 76 (`Unfinished transition recovery failed`).
- Recover: `sync_scheduled_units || log_warn`. Do not `return 1`. The gate already passed; finish the journal.

## 2026-08-21 — Scheduled units sync through a fixed helper, not a radon install

- CI cannot write `/etc/systemd/system`. A general sudo `install`/`systemctl` grant is a root shell. `radon-deploy-root sync-scheduled-units` is the only write: exact sudoers verb, git objects at the GitHub main tip, manifest hash match, regular file, daemon-reload, no start/stop/enable.
- First enablement is one `bootstrap-control-plane.sh` so live helper and sudoers gain the verb. After that, add the unit to `cloud/config/auto-sync-units.txt` and bump `installed-units.sha256`. Do not add scanner oneshots to the control-plane SOURCES list.
- Do not copy from the live checkout. Radon owns that tree.

## 2026-08-21 — Wrapper curl -m must outlive the FastAPI scan child

- `radon-signals-refresh` paged P1 `Result=exit-code, NRestarts=0` every RTH hour. Curl `-m 200` aborted while FastAPI's theta/strength children still had 420s/480s. Client disconnect cancelled the request and `run_script` killed the scanner; the oneshot exited 1. NRestarts=0 is normal for `Type=oneshot`.
- BUG-013 correctly stopped the duplicate fallback on timeout. It did not align deadlines. A shorter curl budget is not a bound — it is a self-kill.
- Curl `-m` >= FastAPI `run_script` timeout. `TimeoutStartSec` >= the sequential POSTs. Discriminating log: `curl=28, http=000`.
- 502/503 slot-cap is a different class (`6093c087`): retry, do not treat as a curl-28 timeout.

## 2026-08-21 — MenthorQ dashboard remint is the OIDC Authorize click

- `client_id=aws_cognito_client_id` on `wp-login.php` is the OpenID consent page, not proof remint is impossible. The form is `input[name=authorize]`.
- Headless that waits for `dashboard.menthorq.io` after WordPress submit will 504/503. Click Authorize first (`d2d595e7`).
- `menthorq-session` ok + `menthorq-login-probe` error = unspendable jar. Session only reads authjs expiry; probe hits the live serving path.
- Do not stand down as "upstream placeholder client_id". Do not copy the CTA WordPress jar onto the dashboard jar. After installing a reminted jar, restart `radon-api` to clear the 300s auth embargo.

## 2026-08-21 — Flex 1025 is a token lockout; Monday retry extends it

- Cash Flows lozenge `TOO MANY FAILED ATTEMPTS. PLEASE REVIEW YOUR CONFIGURATION` is Flex code 1025, not a live IB socket miss. Official v3 table ends at 1021; 1025 is undocumented.
- 1014/1012/1015 are the real config/token errors. 1025 is earned by retrying 1001. `_request_reference_code` retried `_FlexTransientError` (2 SendRequests). Classified as permanent, next attempt Monday 08:00 ET. `radon-perf-twr` 07:30 ET and GET `/api/performance` background rebuild poke the same token with no shared embargo.
- TWR treated `<FlexStatementResponse>` as a ready statement because `<FlexStatement` is a prefix of the error envelope.
- `/orders` `useBlotter(true)` POSTs `/api/blotter` every 5 min → `journal_rehydrate` → FlexQueryFetcher on query `1422766`, **same token**. Looking at the cash-flows lozenge was itself a SendRequest.
- Stop internal 1001 retry. 1025 is exit 15, 7-day shared sidecar (`utils.flex_embargo`). Do not SendRequest. Recover with `python -m scripts.cash_flow_sync --from-file`. Portal Run on 1442520 to verify the query; do not set `IB_FLEX_FLOWS_QUERY_ID`.

## 2026-08-21 — Yahoo is last resort; never the scheduled source

- CREDIT shipped with Yahoo as the only fetcher because IB historical "needs 2FA" and UW "has no HYG history." That skipped the priority chain.
- 2FA skips the IB socket (`auth_state != authenticated`), not the IB path. UW is next. Yahoo is last.
- Do not document Yahoo as the scheduled source. Hard rule: `CLAUDE.md` Mandatory Rules #7 and `AGENTS.md` Data Source Priority (`ABSOLUTE LAST RESORT`).

## 2026-08-21 — Combo modify P&L is close-out, not opening risk

- `ModifyOrderModal` single-leg SELL-to-close already passed `closeOut`. The combo path did not.
- A SELL BAG that matches a held risk reversal is a flatten. Covering the short call with the held long call leaves a synthetic long put, so opening-risk math reports Max Gain ≈ put-strike notional and Max Loss = round-trip cost.
- CBRS 50x @ $8: Max Gain $1,035,835 / Max Loss $4,165 instead of Close Credit $40,000 and realized P&L vs basis.
- Match structure legs (not inverted economic legs), SELL envelope, qty ≤ held BAG units, then pass `closeOut.entryCostDollars`.

## 2026-08-20 — Request-path login must finish under the Next.js options proxy

- `/options/net-gex` 504 is not the same bug as the 2026-08-07 503. `menthorq-session` can be ok while `menthorq-login-probe` is error: cookies look live, re-login is dead.
- Login-probe read timeout is 90s so it can see FastAPI 503. Next.js `OPTIONS_PROXY_TIMEOUT_MS` is 50s. A 60s Playwright login 504s the browser first.
- `_storage_state_expired` only skips chromium for a provably dead jar. An unspendable live jar still mint-then-bootstraps on every click unless a process-wide embargo is tripped.
- Cap request-path login under the proxy (25s / 40s budget) and embargo auth failures. Do not raise the proxy to hide a broken remint. Remint by clicking OIDC Authorize, then land on the dashboard.

## 2026-08-19 — Do not kill a UW producer without a cheaper replacement

- Unloading laptop `com.radon.data-refresh` stopped the 40k burn but left discover/scanner/flow with no scheduled producer (no VPS timer existed).
- Replace 15-minute market-wide pagination with hourly VPS + scoring page caps + FastAPI cooldown in the same change, or the tabs go stale and the operator puts the incinerator back.

## 2026-08-15 — TWR snapshots are not the reconstruction PerformanceData contract

- `perf_twr_builder.py` rows omit `contracts_missing_history`, `trades_source`, and `price_sources`. Empty `warnings` makes `hasWarnings` evaluate `undefined.length` and trip the `/performance` error boundary.
- Normalize at the panel before any array or string method. Do not assume Turso snapshots match `web/lib/types.ts` PerformanceData.
- Chart hardening (HB-117) does not cover panel field access. A finite chart model can still crash the page.

## 2026-08-15 — Verify third-party setup labels against the live UI

- IBKR Activity Flex Queries do not offer `Custom Date Range` in the current query builder. For rolling NAV history, use the visible `Last 365 Calendar Days` option.
- `EquitySummaryByReportDateInBase` is an XML element, not a current IBKR UI label. The Activity Flex section is `Net Asset Value (NAV) in Base`; select `Report Date` and `Total`.
- IBKR Transfers has no generic `Amount` field. Name the exact fields: `Cash Transfer` for cash, `Position Amount in Base` for transferred-position market value, plus `Report Date`, `Type`, and `Direction`; `Transfer Price` is a unit price, not the total transfer value.
- Before publishing external-console instructions, verify exact field labels and selectable values against the current screen rather than inferring them from API concepts.

## 2026-08-15 — Operator mutations use ALLOWED_USER_IDS, not the demo-admin list

- `DEMO_ADMIN_USER_IDS` is unset on app.radon.run and default-denies everyone. Gating `/api/preferences` PUT/DELETE on the demo-trial admin helper 403'd the signed-in operator (`Operator authorization required`) while GET still worked.
- Operator writes go through `requireRouteAccess({ operatorOnly: true })`. The demo-trial helper stays on `/api/admin/demo-users` only.

## 2026-08-20 — Stacked successor green can move kill-to-marker past 60 min

- a231 stop-cleaned BPI at 00:04:23Z (green 00:05:39Z → P3). 0f7d greened at 02:42:18Z and overwrote the marker; kill-to-latest-marker became 158 min and the 60-min kill-before-green bound paged P1 at 02:45.
- Any post-kill green within the 24h oneshot recovery horizon is still stop-clean collateral. Do not re-bind kill-before-green (or in_flight) to the 60-min single-deploy window.
- Keep the 60-min now-to-kill cap only on kill-after-green (no post-kill green yet).

## 2026-08-15 — Oneshot stays failed after stop-clean; do not age-cap against now

- `Type=oneshot` remains `ActiveState=failed` until the next timer. A now-to-kill 60-min cap on kill-before-green flips P3 to P1 after the first hour and pages (BPI 00:34:41Z kill, 00:35:59Z green, 01:35Z page). Measure that branch kill-to-marker.
- Keep the now-to-kill 60-min cap on kill-after-green only. in_flight and kill-before-green use the 24h oneshot horizon (see 2026-08-20).

## 2026-08-14 — Stacked-deploy stop-clean is still Result=signal collateral

- A 20-min window around the *latest* green marker misses stop-clean of deploy N when deploy N+2 greens 34 min later. Between stacked deploys the transition journal is gone and the previous green sits *before* the kill, so `marker - failed_at` is negative.
- Discriminating check: `InactiveEnterTimestamp` plus runner dir mtimes / green marker; sibling oneshots failing `Result=signal` the same second; edge and `/health/lite` stay up.
- Classifier: kill-after-green uses the 60-min age cap; in_flight and kill-before-latest-green use the 24h oneshot horizon. Exit-code / timeout / start-limit-hit stay P1.

## 2026-08-14 — UW daily quota on a oneshot is a unit P1

- Skew's heartbeat embargo does not cover `Type=oneshot` writers. `fetch_oi_changes.py` printed the 40k daily-cap error and `sys.exit(1)`; `radon-oi-changes.service` went failed and the unit watchdog paged. NRestarts=0 is normal for oneshot.
- Discriminating check: journal `FAILED (exit 1)` in the same second as the start line; `logs/oi_changes.err.log` has `daily request limit of 40000`.
- Daily-cap path: write `error` + `next_attempt_at` 20:00 ET, persist a local embargo, keep the last snapshot, exit 0. Do not fail the unit. Ticker eval lookups still exit 1.

## 2026-08-14 — Act ticket must shrink or Confirm clips

- `.act-region` is `overflow: hidden`. A `.act-ticket` of `flex: 0 0 auto` grows to the confirm summary + Gate 3 CRB and clips Place / Confirm. There is no scrollport.
- Ticket must be `flex: 0 1 auto; min-height: 0; overflow-y: auto`. `min-height: 0` is required; flex `min-height: auto` refuses to shrink below content.

## 2026-08-14 — Ticker book depths cannot stay ref-only behind a memoized shell

- `WorkspaceSections` is `memo()`. High-frequency `depths`/`tape` lived in `TickerDetailContext` refs. A depth-batch that did not also change the `prices` identity never re-rendered `TickerWorkspace`, so the Book tab stayed on the empty L1 fallback even when L2 or tape had arrived.
- Pass `depths` and `tape` as props from `WorkspaceShell` on `ticker-detail`, same as the portfolio reactivity fix. Seed a one-level `L1 BBO` montage from bid/ask sizes when L2 is missing so the panel is not a header over empty space.

## 2026-08-14 — DAY working orders die at the close; RTH-gated sync will not

- `orders-sync` only runs 09:30–16:00 ET. A DAY order still in IB at 15:59 is written to Turso, then IB cancels it at the close. The next snapshot does not land until the following RTH open.
- Modify/cancel talk to live IB (`find_trade` / `reqAllOpenOrders`). A ghost DAY row still offers Modify and fails with Trade not found.
- Filter prior-ET-session `tif=DAY` rows on snapshot read. Do not treat a stale Turso working-order row as live.

## 2026-08-13 — Name-ranking vol scanners live under Scanner, not Regime

- `/indicator` defaults to a Regime chart tab. Cheap-wing IV ranking is a name scanner like LEAP and GARCH. Put it on `/scanner?mode=vol-cone`. Redirect `/regime/vol-cone`.

## 2026-08-13 — Vol cone scans every standard monthly, not one 45-DTE pick

- The NVDA/SMH setup is a named third-Friday monthly (Sep 18), not "whatever expiry is nearest 45 DTE". A single-expiry picker collapses Oct/Nov cones and reads like a rolling tenor.
- Scan all standard monthlies (third Friday only, never weeklies/dailies) with DTE in `[21, 180]`. Each `(ticker, expiry)` is its own cone and table row.

## 2026-08-10 — Sortable metric headers must keep labels intact

- A narrow numeric column can wrap `Return %` between the word and symbol while displacing its adjacent help control, even when the column is otherwise wide enough. Sortable label groups need an explicit no-wrap contract, and regression coverage must pin that contract rather than merely asserting that the header text exists.

## 2026-08-07 — Credit-position return must use risk capital, not net premium

- `P&L / abs(net entry premium)` is only a premium-return statistic. For credit and undefined-risk structures it can report extreme percentages while ignoring the buying power used to carry short-option risk.
- Keep signed entry cost for dollar P&L (`MV - entry`), but use verified position capital for the percentage denominator: max risk for defined-risk structures, full-loss debit for long stock/all-long options, and a fill-linked opening-margin basis for undefined-risk structures. If that denominator is unavailable, show `N/A` rather than falling back to the opening credit.
- Persist any broker margin basis as an immutable, fill-linked lifecycle event with source/time provenance. A projected what-if is not filled-position capital, a later close-order preview measures current marginal relief, and portfolio-margin offsets make position margins non-additive.

## 2026-08-01 — Worthless option expiry must synthesize $0 closes

- IB often writes no journal fill when OTM options lapse. `computeRealizedPnl` only saw CLOSED rows or opposite-side fills, so residual inventory (e.g. KWEB 1500× $31C Jul-17) never became realized P&L and monthly totals understated large debit losses.
- After the lot-matcher walk, residual OPT inventory with `expiry <= report as-of (opts.to)` must emit a synthetic close: proceeds $0, long realized = −basis, short realized = +basis, close date = expiry ET day. Do not expire residual past `opts.to` (partial covers with later expiry stay open).
- **Phantom residual trap (SNDK $1300C):** Flex open (composite exec ids) plus daemon fills for the same open are often *not* linked by constituent ids. Constituent dedup alone leaves 2× open qty; a full SELL close leaves residual that falsely “expires worthless” alongside the real close. Fix: `preferFlexOverFillSameDaySide` — when flex_agg exists for (ET day, side), drop fill-family rows on that day+side before the inventory walk. A fully closed book must never also expire.

## 2026-07-31 — Unrealized P&L modal must show signed entry and MV

- Never `Math.abs` entry cost or market value in a breakdown that claims `P&L = MV − entry`. Credits and short marks become unverifiable (META debit + abs(short MV) looked like a gain; AAOI credit looked like a smaller gain than the real P&L).
- Format with explicit signs (`+$…` / `-$…`) so each row is eye-checkable. Multi-leg use signed `resolveEntryCost` / `resolveMarketValue` from `positionUtils`, not unsigned leg sums.

## 2026-07-28 — Day P&L ESTIMATED (LIVE) vs IB during RTH

- Screenshot showed Day P&L **-$159k ESTIMATED (LIVE)** while Turso `account_summary.daily_pnl` was **-$40.9k** and `sum(ib_daily_pnl)` matched IB. Client close-based math was ~4× off.
- When `acct.daily_pnl` is null (reqPnL race / Phase 6 no-wait), the UI falls back to `computeDayMoveBreakdown`. That path must **prefer `pos.ib_daily_pnl` without requiring live last/close**. Gating IB daily on full quotes dropped or replaced good IB numbers with prior-close fiction.
- Same-day opens (AAOI/META/SPCX on the bug day) must not use yesterday's option close; use IB daily first, else entry-cost baseline (`getTodayPnlDollars` / `isSameDay`).
- When diagnosing Day P&L, compare three numbers: `account_summary.daily_pnl`, `sum(positions.ib_daily_pnl)`, and the rendered ESTIMATED total. If 1–2 agree and the UI disagrees, the bug is in the client fallback, not the market.

## 2026-07-18 - Control-plane refresh must start from the target checkout

- Bootstrap hashes the current VPS checkout, not an immutable release runner. When manifest preflight rejects a newer unit hash, first prove `/home/radon/radon` is at the exact tested SHA and fast-forward it as `radon` if it is behind; then run `bash cloud/scripts/bootstrap-control-plane.sh` as root from that checkout, without restarting Gateway, and rerun CI. A stale checkout can make bootstrap report “current” while preserving an old manifest. Never bypass or relax `preflight_control_plane`.

## 2026-07-17 - Broad reliability work requires parallel, metric-bound evidence

- When asked to pressure-test services or improve reliability broadly, use multiple focused agents from the outset: one for inventory and service boundaries, one for web, and one for backend/daemon paths. Do not report an aggregate percentage without a reproducible per-service baseline, a defined metric, and post-change evidence.

## 2026-07-11 — portfolio LIVE DATA DEGRADED on weekend (snapshot age)

- GET `/api/portfolio` must never call IB (browser amplification fix). That is correct.
- A flat **60s** snapshot-age check still painted **LIVE DATA DEGRADED** all weekend because `portfolio-sync` only runs Mon–Fri RTH and last Friday's row is hours old by design.
- Reuse **`portfolio-sync` service-health windows** (`open` 10m, `extended`/`closed` 3d) via `getMarketStateFromDate` + `isStale`. Expected off-hours lag is silent; true RTH silence and Turso `staleWhileError` still warn.
- Helper: `web/lib/portfolioSnapshotFreshness.ts`. Do not reintroduce wall-clock 60s for this surface.

## 2026-07-11 — production reliability cutover (monorepo + broker)

Source-green is not production-green. After source hardening, live recovery still required deploy, bootstrap, 2FA, env invariants, host packages (Bun), and GitHub control-plane settings.

### Health schema and aggregate

- HTTP 200 on FastAPI `/health` is transport truth only. Nested `auth_state`, `service_state`, and `upstream_dead` must drive the aggregate. Version `/status` with `schema_version`, boolean `ok`, and `overall_state`; off-box consumers fail closed on opaque bodies.
- Cloud-mode FastAPI reports `service_state=reachable` (TCP/API only; no Docker health). The nested classifier must treat `reachable` as healthy alongside `up`/`ok`/`healthy`, or a fully authenticated cloud gateway stays `overall_state=unknown`.
- Compatibility deploy gates validate schema field types only, never a healthy broker verdict. IB outage must not roll back app code.

### Control-plane bootstrap and deploy

- First-time bootstrap fails `systemd-analyze verify` when ExecStart helpers are not installed yet. Seed staged shell helpers at their final paths before unit validation; remove only those seeds if verification fails.
- Root demotion of `radon-ib-gateway-control` must land in a radon-readable cwd. Demoting while cwd is `/root` yields Compose `stat .: permission denied` and leaves durable transition journals that block later mutations.
- Deploy preflight runs as `radon`. `0440 root:root` sudoers under root-only directories are invisible to non-privileged `-f`/`-r` probes. Match release sources to the readiness manifest as radon; leave installed-target existence/hash/mode to the fixed no-argument root helper `verify-control-plane`.
- After any live hot-patch of an installed control-plane target, re-run bootstrap so the readiness manifest hashes match the next release, or deploy preflight will refuse "incompatible with scripts/…".
- Immutable runners are extracted with `chmod a-w`. Prune must restore write bits before `rmtree`, or a green deploy fails after success on runner retention.
- Post-gate backup cleanup of managed release trees is best-effort. Root-owned venv files (e.g. py-spy) must not turn a gated green release into a hard failure.
- Production VPS needs the exact Bun pin in PATH for radon (currently 1.3.14). Missing Bun fails staged builds after control-plane and env preflight already passed.

### Production env invariants (Hetzner monorepo)

| Key | Production value | Notes |
|---|---|---|
| `IB_GATEWAY_MODE` | `cloud` | FastAPI must not own Compose lifecycle; helper owns it. |
| `RADON_MODE` | `hetzner` | Monorepo host topology (not `local`, not the cloud-package label `cloud`). |
| `IB_GATEWAY_COMPOSE_DIR` | `/home/radon/radon/cloud` | Not `~/radon-cloud`. Wrong path yields `container_state=not_found` while the container is healthy. |
| Secrets file | `/home/radon/radon-cloud/.env` mode `0600` | Temporary stable path; do not delete during migration. |
| Host data excludes | `outputFileTracingExcludes` for disk-fallback API routes | Production data trees exceed the 128 MiB output-trace audit; local builds can pass while VPS deploy fails. |

### IBKR 2FA recovery (operator)

1. Freeze `radon-ib-watchdog.timer` during manual work.
2. `POST /ib/reset-backoff` to clear leases.
3. Lifecycle only via `/usr/local/bin/radon-ib-gateway-control` from a radon-readable cwd (never raw docker/systemctl if the helper is installed).
4. Approve the IBKR Mobile push immediately (~3 min expiry).
5. Confirm `auth_state=authenticated`, then ensure pool reconnect (API restart or recovery heartbeat).
6. Re-enable the watchdog timer.
7. Stuck transition journal at `/var/lib/radon/ib-gateway-transition.json` means complete the desired state or clear only after the daemon has converged — do not delete blindly mid-mutation.

### GitHub control plane

- Superseded 2026-07-15: the `Production` environment required-reviewer rule was removed at the operator's request. A green push to `main` now deploys automatically after all workflow `needs:` gates pass. Keep the environment binding and its separate main-only branch policy; deleting the environment or setting `deployment_branch_policy` to `null` would weaken unrelated controls.
- For mutable GitHub control-plane changes, do not add a repository regression test that cannot observe the changed setting. Verify the actual environment state through the GitHub API; keep source tests only for repository-owned behavior.
- Branch protection on `main`: block force-push and deletion. Do not require pre-existing status checks on direct push for a solo push-to-main model; that bricks deploy until a PR workflow exists. Tests still gate deploy via the workflow `needs:`.

### Gitleaks policy self-tests

- Policy fixtures that assert the TWS-credential regex must build positive strings at runtime (`"TWS_" + "PASSWORD" + "=" + ...`). Full-history scan treats literals in the test file as findings. Allowlist only immutable already-pushed commits that contained the old fixtures.

## 2026-06-27

- Portfolio sync endpoints must return the freshly reconstructed live IB payload directly; persistence to Turso is a separate best-effort step. Never make `/portfolio/sync` prove success by rereading Turso, because a DB read outage turns a successful IB sync into a dashboard blank.
- Superseded 2026-07-10: browser portfolio and orders GET paths must be snapshot-only and must never invoke live IB as a fallback. Live reconstruction belongs behind explicit POST/operator actions or server-owned schedules with single-flight and minimum-age caching; otherwise every open tab amplifies a DB outage into an IB client/subprocess storm. When a snapshot read falls back after an upstream error, keep serving the snapshot but preserve a visible degraded warning.

## 2026-06-25

- Same-action call+put combos must carry direction in the structure label. A short call plus short put is `Short Strangle`/`Short Straddle`, not `Combo (2 legs)`, and long call+put pairs should be `Long Strangle`/`Long Straddle`; keep portfolio sync and order-builder classifiers aligned.
- When modifying or displaying multi-leg combo orders, preserve the signed BAG net price end to end. Do not apply `Math.abs()` to risk-reversal bid/mid/ask/implied references, and validate combo limits as finite nonzero signed prices; negative `BUY` combo limits are valid credits.
- Option-chain metadata routes must fail with structured detail and bounded fallbacks, not opaque 502s. A provider timeout on `/api/options/expirations` should preserve upstream context and leave the chain UI with a diagnosable degraded/empty state instead of a centered timeout string.
- When all app routes lose live data but edge health says IB, relay, Next, and Gateway are up, check the shared Turso/libSQL read path before restarting IB. DB-backed fallback reads need hard per-source deadlines; a hung DB promise must never block a fresher disk fallback or leave the UI in a quiet empty state.
- Browser realtime sockets in production must default to same-origin `/ws` plus a short-lived FastAPI ticket. Never let authenticated production clients fall back to `ws://localhost:8765` or to an unauthenticated socket after ticket failure.
- A live-data outage should surface in the shared shell, not disappear into per-route placeholders. Portfolio, orders, and price-stream failures should render a visible degraded state so blank dashboards are diagnosable.

## 2026-06-24

- Scanner mode navigation should read as a tab strip, not as nested segmented pills. When adding scanner modes, avoid combining an outer bordered control with inner bordered active states; use one visual boundary and verify the tab surface in the actual scanner layout.
- Scanner pages need both broad-universe and operator-directed single-name workflows. When adding a new scanner, expose and test a specific ticker probe path in the first pass, not only preset/limit scans.
- For fallback option exposure, derive put delta from the same spot-minus-strike moneyness as call delta (`put = call - 1`); never flip moneyness for puts. An OTM short put should contribute a small positive delta, not dominate a bear put spread as nearly +1. Also verify signed compact money (`-$1.93M`) at the display layer when fixing signed exposure math.

## 2026-06-23

- Before reporting that `main` is fully synced after a push, run a final `git fetch origin && git status --short --branch`; parallel agents can advance `origin/main` moments after a successful push, and a stale local status makes the next turn start behind.

## 2026-06-13

Session: reliability roadmap (DUR-01..16) + a chain of trade-data bugs (CTA-01, MU P&L, JRN-01/02, SPX-01..06) + ops fixes.

- **A red watchdog alert can be STALE, not real — verify the underlying data before chasing the code.** The `journal-reconcile ×20` P2 ("3 fills missing from journal") was a latched `service_health` error row: the reconcile snapshot finished at 03:52:33Z, the JRN backfills wrote those exact 3 execIds at 03:53–04:58Z, and the daily handler doesn't re-run for ~24h, so the watchdog re-alerted a frozen row every cycle. `×N` on a daily handler = watchdog cycles since latch, NOT N failures. ALWAYS read the row's `last_attempt_finished_at` vs the data's `written_at`, and SELECT the journal (over `db_http.hrana_execute`, read-only) before touching anything. Durable gap (FIXED, JRN-03): a daily handler that latches `error` re-alerts for up to 24h after an out-of-band repair — `heal_journal_reconcile_if_recovered(db)` now re-runs the (IB-free) gap detection from the gap-closing paths (journal_sync after writes, the backfill script after `--execute`) and flips the row error→ok the moment zero gaps remain. Gated on the error state so the windowed re-scan only runs while an alert is actually latched (transient). Same heal-on-recovery shape as the IB-2FA heal.
- **Weekend false-positive staleness is a recurring class: a UNIFORM staleness window on a market-hours/scheduled writer alerts every Saturday.** cash-flow-sync (17:00 ET daily, skips weekends) + llm-token-index (06:30 UTC daily) both used 25h windows that trip ~18-24h after Friday's run though the next run is Monday (~72h). Fix: widen the `closed`/`extended` market-state window to cover the longest legit quiet period (4d = Fri→Mon + holiday); keep `open` tight to still catch a missed weekday run. NOTE: a `health=None` (never-written) row is stale REGARDLESS of window — a brand-new weekly handler (preset-rebalance, post-DUR-14) shows "no data" until its first run; that's a deploy artifact, not a window bug. Don't widen windows to mask a never-run service.
- **Playwright headless chromium on GitHub-hosted runners can't open LOOPBACK — and it's invisible until you add in-job diagnostics.** Symptom: every spec fails fast (~1s) with `net::ERR_NAME_NOT_RESOLVED at http://127.0.0.1:3000/...` — for the LITERAL IP, not just `localhost`. The server is fine: `ss -tlnp` shows `LISTEN *:3000`, `curl 127.0.0.1:3000` returns 200 from the same shell, `env | grep proxy` is empty. Only the browser process can't reach loopback. Flags that did NOT help: `--no-proxy-server`, `--proxy-bypass-list=*`, `--disable-features=AsyncDns`, `--no-sandbox`, `--disable-dev-shm-usage`, baseURL `localhost`→`127.0.0.1`. **Budget your CI iterations** (I spent 4) and the moment a required/visible job is provably blocked, REVERT to keep main green + document the evidence in-file — never leave a perpetually-red job or keep guessing browser-network flags on main. Most promising next lead (branch-only): the server also binds the runner's LAN IP (`Network: http://10.1.0.x:3000`) which the browser's non-loopback stack may reach, but that bypasses the loopback-scoped authless middleware bypass, so it needs an auth-bypass change or a containerized runner. Add the in-job diagnostic block (`env|grep proxy`, `ss -tlnp`, `getent hosts localhost`, `curl -w %{http_code}`) on the FIRST run, not the fourth.
- **Land a never-verified CI job NON-gating first.** The e2e-smoke job was added without `deploy.needs`, so its 4 red runs never once blocked a production deploy while I debugged. "A flaky/red required gate is worse than none" — promote to a gate only after a GREEN run is observed.

- **Fixing a swallowed-error / dead-data path can EXPOSE a latent downstream bug** that was (correctly) coping with the data's absence. CTA-01 fixed a crashing `journal_basis` lookup (it had been returning `{}` and `ib_sync` fell back to IB avgCost = correct); making it work surfaced MU-PNL — `ib_sync` then trusted an *incomplete* journal basis and reported MU $1050 P&L as −$60,876/−128% (true ~−$6,289/−6.2%). After un-suppressing an error, AUDIT every consumer that trusts the now-present data. e992f66/0e672d7 → 912a957.
- **Never override broker/exchange truth with derived/reconstructed data unless the derivation is COMPLETE.** `ib_sync.fetch_positions` must compare `abs(journal_net_qty) == abs(position_size)` before replacing IB avgCost with the journal lot-matched basis; on mismatch keep IB avgCost + log. A blast-radius scan found a 2nd live corruption (KWEB) + 9 dormant. 912a957.
- **libsql_experimental 0.0.55 cursors have NO `.rows` attribute (use `.fetchall()`), and rows come back as TUPLES, not dicts.** CTA-01 was two layers: `.rows` raised `AttributeError` (swallowed per-ticker → silent fallback to drifting avgCost), and under that, name-based row access read every tuple row as empty. Make test doubles driver-faithful (`fetchall()` only, tuple rows) or the bug ships green. e992f66/0e672d7.
- **Test fixtures with hardcoded past dates ROT across a rolling-window boundary** — green locally, red in CI on a later calendar day. A JRN-02 fixture `"2026-05-29"` sat exactly on the 14-day reconcile window edge: passed on 06-12, failed CI on 06-13 (0 upserts), blocking the deploy. Use `datetime.now() - timedelta(days=N)` for any fixture feeding window-filtered logic. Same family bit `latestPortfolioTargetDateET` (double-TZ-shift). dd3d6f9.
- **journal_sync swallowed a transient Hrana "stream not found" Turso upsert AND disk-dedup then blocked all retry → permanent silent journal drop** (JRN-01). The disk write succeeded first, so `seen_ids` skipped the exec on every later cycle. Fix decouples disk-dedup from DB-write success: `_reconcile_db_missing()` re-upserts disk rows absent from the journal every cycle (self-healing). d2fb6e6/f9746bd.
- **Monitor-daemon handlers have NO direct operator-notify primitive** — `from utils.notify import notify` referenced a nonexistent module and silently swallowed at import (the JRN-01 reconcile alert was dead). Surface findings through `service_health`: a truthy `result["error"]` from `execute()` is BaseHandler's documented swallowed-failure convention → state=error with detail → watchdog dispatch + /admin + DUR-11 history. A check handler reporting its own finding as error state is NOT a writer-state-vs-event-content violation. d2fb6e6.
- **Closed-position P&L % must derive from the realized-P&L identity (`pnl / |entry basis|`), not long-only `exit_notional − pnl`.** Both surfaces were wrong for a short-call buy-to-close: the orders cell subtracted the cover cost from the basis (+104.4%), the share card fuzzy-matched an unrelated live position (+69.00%); true was +95.9%. Unify both surfaces on ONE shared function; require the portfolio fuzzy-match to compare strikes, not just word overlap. ff89eae.
- **The nightly IB Gateway "JVM wedge" was the gateway's OWN auto-restart**, not load: `AUTO_RESTART_TIME` unset → IBC default 11:45 PM session-local = 23:45 UTC (jts.ini TZ Africa/Abidjan), and the watchdog killed it mid-relogin. Pin `AUTO_RESTART_TIME` to a quiet window + give the watchdog a quiet-window so it doesn't classify the scheduled relogin as a hang. NOTE: an invalid IBC time format (`"23:58 ET"`) is silently ignored → falls back to the default. radon-cloud 21d9391.
- **The gnzsnz IB Gateway image BLANKS `JAVA_TOOL_OPTIONS`** before starting the JVM (verified via `/proc/<pid>/environ`) — GC logging must go in the persistent `Jts/ibgateway/<ver>/ibgateway.vmoptions` file (under `### keep on update`), not the compose env.
- **Timer-driven oneshots firing faster than every 5 min trip the DUR-02 flap-brake** (`StartLimitBurst=5/IntervalSec=300`) even when every run SUCCEEDS — a 60s oneshot makes exactly 5 starts/300s and parks as `start-limit-hit`. Use `Burst=10` (matches ib-watchdog/portfolio-sync) or `StartLimitIntervalSec=0` (a timer oneshot can't crash-loop). Recover a parked unit with `systemctl reset-failed`. host-metrics hit this because DUR-12 created it after DUR-02's special-casing. The DUR-02 unit-watchdog alert was CORRECT — the unit's brake config was the bug.
- **IPO-day / hard-to-borrow shorts: Reg SHO 203(b) requires a locate that doesn't exist on day one** (unsettled shares, no lendable inventory). "Order Inactive" is IB's received-but-not-working status; the reason arrives as a SEPARATE async errorEvent (201 class). `ib_place_order` broke its confirm-poll before the 201 landed and returned a bare message — grace-wait ~1.5s (driving the event loop via `client.sleep`) for the matching error + fall back to `trade.log` errorCode entries, and log the detail server-side before the 502. c9bc47f.
- **IB shortability ticks 46 (difficulty) and 89 (shortable_shares) arrive INDEPENDENTLY in a streaming probe and only during market hours** (never snapshot — generic ticks break snapshots). tick 46 is often slower, so derive `shortable=true` from `shortable_shares>0` when difficulty is absent, else a borrowable name (AAPL 190M shares) renders a wrong "NO LOCATE". Off-hours the endpoint correctly returns `missing:true`. a3a9fcf/d74b90e.
- **CTA share staleness: the generator was pinned to glob-latest `data/menthorq_cache/*.json` with no freshness gate**, so between the 16:00 ET session roll and the 17:30 ET sync the modal rendered yesterday's data. Pick the date-max of {DB row, disk} and flag `data_date < latest_closed_trading_day` with a STALE banner; weekend-aware. ab97253.
- **Deploy gate must be layered and NOT depend on the component it most often fixes**: `systemctl is-active` on the restarted units + `/health/lite` (no IB probing) + advisory `:8330/status`; NEVER the full `/health` (IB auth state must not roll back a code deploy). Add a `RADON_DEPLOY_NO_GATE` escape hatch. `migrate.py` retries transport-class errors (Hrana/dns/timeout) so a DNS blip can't hard-fail startup. radon-cloud 624f19c / 3cb4e02.
- **Verify data-correctness fixes against Turso directly, never `data/*.json`** (fallback, frequently stale) — and after a CTA-01-class fix, restart `radon-monitor` so the long-lived daemon picks up the corrected import path (the deploy restarts it, but a stale daemon keeps logging the old error).
- **Production config drift is structural** (app repo + radon-cloud VPS working copy + hand-edited `/etc/caddy/Caddyfile`): bring live config under git + a daily drift-audit that writes a `service_health` row. The live Caddyfile had a hand-added beta block in no repo. d1db1e8 / radon-cloud.
- Process: for multi-step work use a Workflow and route grunt (mechanical edits-from-spec, suite runs, log greps, doc syncs, verification) to haiku/sonnet agents; reserve the top tier for RCA, design, adversarial verification, and prod-mutating ops. Keep irreversible prod writes (journal backfills, gateway/2FA, systemd recovery) under direct control, not delegated to an agent.

Test-suite audit (a "improve coverage" request that turned up a live auth hole):

- **Auth must FAIL CLOSED.** `server.py` auth_middleware returned `call_next()` unconditionally when `CLERK_JWKS_URL` was unset — one missing/typo'd VPS env var would make all 47 routes world-callable through the public Caddy proxy. Fixed: check trusted-local + API-key bypasses FIRST, then DENY (503) when JWKS is unconfigured; the only opt-out is an explicit dev-only `RADON_AUTH_DISABLED=1`. The bug was load-bearing: 3 route-test files were ABUSING the fail-open (`monkeypatch.delenv("CLERK_JWKS_URL")`) to disable auth — a test that disables a guard by unsetting an env var is a signal the guard fails open. Never disable auth in tests by removing config; use the explicit trusted-local stub.
- **A green test that imports no production code is worse than no test** — it's false confidence with a maintenance bill. Found: `share-pnl.test.ts` re-implemented a diverged 2-param copy of the real 4-param `positionGroupShareData` (so the share-card P&L bug site had 0% real coverage); `ws-server-*.test.ts` asserted hand-written mirrors of server constants; Kelly's 2.5% cap was re-derived in the test body instead of called; greeks cross-validated against a self-mirror. All survive mutation. Grep for test files with no import of the module they claim to test.
- **You cannot improve coverage you don't measure.** Python had ZERO coverage signal (no pytest-cov) while carrying 42K lines of Python tests; the TS "79%" excluded `web/components/` + `middleware.ts` from the include list, hiding the highest-consequence files. Measure (branch coverage) BEFORE gating; gate as a non-regressing ratchet set just below the real measured number (Python combined stmt+branch was 66.5%, not the 71% statement figure — gate at 64), never as a vanity target. Coverage-percentage chasing is the wrong goal; correctness-of-assertion on money-math + authz is the dominant risk — but "deprioritize coverage entirely" is also wrong: real 0%-covered modules (preset_rebalance, the per-cycle DB writers, vcg_scan at 18%) ship a schema-rename or signal regression green.
- **CI that names test dirs explicitly silently orphans the rest.** `ci.yml` ran `pytest scripts/tests scripts/api/tests`, dropping 29 `scripts/trade_blotter` tests that existed and passed — pythonpath includes the dir but pytest won't collect it unless named. After moving/adding a test dir, grep the CI invocation. 98 Playwright e2e specs were similarly never wired into CI (manual-only) and ~70% rotted >60 days.
- **Real `time.sleep`/`asyncio.sleep` in tests is pure waste** — ~14s across <15 tests here. Inject the clock/poll-interval as a param defaulting to the production value (`place_order(_clock=time.time)`, `pool_*(poll_interval=0.5)`) so production behavior is unchanged and tests pass a fake.
- **To test "does this failure exist without my changes," NEVER `git stash push -- <paths>` then `git stash pop` blindly.** `git stash push -- <pathspec>` does not stash untracked files (it warns "did you forget to git add" and may create NO stash), so the subsequent bare `git stash pop` pops whatever is on TOP of the stash stack — which on this repo was a months-old foreign WIP (a bundle-size experiment touching format.ts/positionUtils.ts) — producing `UU` merge conflicts in files I never touched. Recovery: `git checkout HEAD -- <conflicted files>` (they had no intended changes). The correct way to A/B a pre-existing test failure: `git stash push -u` (include untracked) to fully shelve the working tree, run, then `git stash pop` — and confirm the popped ref is yours. Even better: prove non-impact by `grep`-ing the failing test files' imports for your changed modules (if they import none, your change cannot be the cause) before touching the stash at all.
- **Some web tests fail locally but pass in CI — verify the env, don't chase them.** `db-writer-direct-cloud`, `ws-keepalive-client`, `ib-status-context` fail 13 tests on this laptop yet the same commit's CI "Vitest (full config)" reports 3180 passed / 0 failed. They import none of a given feature's modules, so they're pre-existing env-specific (timer/WS/libsql) flakes. Confirm a green CI vitest job rather than trying to make them pass locally.
- **A two-branch label that should be three: `'Long' if BUY else 'Closed'` mislabeled every sold-to-open short as 'Closed Call'.** Options structure naming needs the full ACTION, not a BUY/SELL collapse: BUY*→Long, SELL_TO_OPEN→Short, SELL_OPTION/CLOSED→Closed. Fix lived in three twins kept in sync by comment (`journal_sync.py`, `journal_rehydrate.py`, `web/lib/journalSync.ts`) — change all three. The blast-radius UPDATE proved the fix: 155 SELL_TO_OPEN rows were Closed→Short while 149 genuine SELL_OPTION/CLOSED closes correctly stayed Closed.
- **"Still getting the push" = Pushover EMERGENCY (priority 2) re-alerts every 60s for a full HOUR until acknowledged — even after the condition recovers.** Watchdog P1 alerts use priority=2 (`retry=60`, `expire=3600`). A transient blip that self-heals in seconds still pages every minute for an hour because the emergency is never cancelled. Don't conflate this with "the alert keeps re-firing" — it's ONE alert retrying. Fixes: (a) tell the user to tap Acknowledge (stops it instantly) or wait for expire; an already-sent UNTAGGED emergency can't be cancelled remotely (no Pushover "list receipts" API). (b) Durable: stamp emergencies with a `tag` and `cancel_emergency` (POST `receipts/cancel_by_tag/<tag>.json`) the moment the condition recovers — track active emergencies in the cooldown table (last_outcome='notified' within the expire window), cancel + mark resolved on the recovery cycle. Cost is one cooldown read per quiet cycle, health fetched only when a grouped emergency is actually active.
- **Importing the same file under two module paths (`watchdog.x` vs `scripts.watchdog.x`) creates TWO module objects — `patch("watchdog.notify.fn")` won't touch code that did `from scripts.watchdog import notify`.** The prod entrypoint is `python -m scripts.watchdog` (so `scripts.watchdog.*`), tests do `from watchdog import` (scripts/ on path). Use RELATIVE imports (`from . import notify`) inside the package so both contexts resolve to the same instance — symptom was a mock list staying `[]` while the real function ran.
- **The watchdog turned a JVM-acceptor wedge into a 15× gateway-restart / 2FA-push loop by misreading `upstream_dead` as `awaiting_2fa`.** When the IB Gateway's Java API acceptor hangs (`upstream_dead=True`, port still open), a fresh radon-api pool can't connect and the cached `auth_state` can read `awaiting_2fa`. `is_stuck_awaiting_2fa` fired, and its "recovery" — restart the gateway to send a new 2FA push — does NOTHING for a dead upstream, so it looped ~every 12 min (each loop = a real 2FA re-auth + an hour-long Pushover emergency; the dead pool 502'd every route = "unresponsive"). FIX: `upstream_dead` must OVERRIDE `auth_state` — `upstream_dead=True` is ALWAYS the api-hang (one bounded restart), and `is_stuck_awaiting_2fa` requires `upstream_dead=False`. Genuine 2FA waits are container-healthy (`upstream_dead=False`) — VALIDATED live during recovery. Also cap stuck-2FA restarts (≤2/hour) and bounce radon-api once after a watchdog restart resolves auth (the pool stays disconnected otherwise). Commit 6af999d.
- **Manual 2FA recovery recipe (system down, gateway awaiting_2fa, pool disconnected):** (1) `systemctl stop radon-ib-watchdog.timer` to FREEZE the self-heal loop during manual work. (2) `curl -X POST :8321/ib/reset-backoff` to clear any held push lock. (3) `curl -X POST :8321/ib/restart` to fire ONE fresh push (it succeeds even though it may misreport `restarted:false` on a `MENTHORQ_PASS` `$`-interpolation warning — verify via `docker ps` Up/healthy + `/health` auth_state, not the response body). (4) operator approves the push FAST (IBKR pushes expire ~3 min; a stale push never authenticates). (5) poll `/health` for `auth_state=authenticated` (gateway log shows "Login has completed"). (6) `systemctl restart radon-api.service` to reconnect the stuck pool (`feedback_ib_pool_stuck_after_2fa`). (7) `systemctl start radon-ib-watchdog.timer` to re-enable. Confirm: pool 3/3 connected + `portfolio/sync -> 200`. Do NOT use the autoheal Docker sidecar (TCP healthcheck can't see a JVM wedge AND it bypasses the 2FA lock → stacking).
- **A deploy that restarts radon-api fires a false "IB Gateway awaiting 2FA" page.** The fresh api process reports `auth_state=awaiting_2fa` for ~10-20s during pool warmup before the recovery heartbeat confirms auth; if a 5-min watchdog cycle lands in that window (with 2+ IB services momentarily failing the sync), the grouped IB alert pages the operator for a 2FA that doesn't exist. Tell: the gateway log shows `awaiting_2fa -> authenticated; no reconnect needed` seconds later, and the api `ActiveEnterTimestamp` coincides with the alert. Fix: suppress the grouped page when `systemctl show radon-api -p ActiveEnterTimestamp` is <180s ago (absorb the transient IB failures, send no push); a REAL awaiting_2fa (api not freshly restarted) still pages promptly — don't add hysteresis to real 2FA alerts. Corollary: each deploy during market hours is a restart-blip risk; batching deploys / deploying off-hours reduces watchdog noise.
- **A market-hours-only service is falsely "stale at the open bell" — its wall-clock age spans the period it legitimately couldn't run.** cri-scan/vcg-scan (RTH-only, Mon-Fri timers) fired "silent for 2d — market open" every market morning: at 9:30 the watchdog compared the weekend-old write against the tight open window (35m) before the first session run landed. Fix is an open-bell GRACE: cap the effective age at `min(age, seconds_since_open)` for intraday services in the open state — it can't be "stale during the open" before the market has been open as long as its window. A genuinely silent scanner still trips once the session runs longer than the window (the grace is exactly one window). Separately, the closed window must cover the Fri→Mon weekend gap (3d, not 1d) for the premarket `extended`-state check. Watch out: tests that seed a scanner stale at "10:00 ET" (30m after the bell) now sit INSIDE the grace — move them past it (11:00 ET). The Python watchdog window VALUES are NOT locked to the TS by the contract test (only names + requires_ib are), so Python had drifted to 1d while the TS already had 3d.
- **The whole app showed ~15-min DELAYED quotes in prod — the relay defaulted to `reqMarketDataType(4)` (delayed-frozen).** Only the flag-gated depth path flipped to realtime (type 1). Signature of delayed data: the prior CLOSE matches a real-time reference exactly (settlement is fixed) but the LAST lags, and `marketDataType` reports 3, with delayed tick fields (66-76) instead of realtime (1-9). Confirm definitively with a standalone @stoqey/ib probe that logs the `marketDataType` event + tick fields — fresh tick timestamps do NOT prove realtime (IB stamps delayed ticks with the current time). Flipped to always-realtime; off-hours IB holds the last live values so closed-market reads still return last-known. A structural test pinned `reqMarketDataType(4)` — updating it to `(1)` was the red/green for the intentional flip. `reqMarketDataType` is per-CONNECTION (global), so you can't mix realtime futures + delayed stocks on one IB connection without a second connection.
- **Header centering is infeasible when one side cluster is much wider than the other.** With `justify-content: space-between` + 3 zones, the middle only dead-centers if the left/right clusters are equal width; they never are (a wide search+sync actions cluster vs a short status rail). Forcing equal `flex:1` made the actions overflow into the ticker (0px gaps). The legible fix: `gap` on the header (guaranteed min separation, flex items can't overlap), actions sized to content (`flex:0 0 auto`, not flex:1), the ticker flanked by hairline dividers as its own zone, non-shrinking (hide as a unit below a breakpoint rather than clip a cell), and shed redundant side content (the rail timestamp duplicated the actions "Last sample"). Symmetric gaps read as balanced even when not at the geometric center. Verify by measuring gaps at several widths via CDP viewport override, not by eyeballing one width.
- **Futures L1 quotes were gated behind the depth flag.** Subscribing a bare futures root ('ES') via the relay returns the equity of the same name (ES = Eversource Energy) unless DEPTH_FUTURES_SYMBOLS resolution runs — which was coupled to RADON_DEPTH_ENABLED. Decoupling front-month L1 resolution from that flag (keeping reqMktDepth gated separately) lets the header futures strip stream real ES/NQ/RTY without the L2 book. Cash indices (SPX/NDX/RUT) were NOT a substitute — they don't tick overnight when futures actually trade. Prior close for % change = IB tick 9 (already on PriceData.close).
- **Writing the test found the bug — twice, both live security holes.** The "improve test coverage" engagement's two highest-value outputs were not tests: (1) auth failed OPEN when `CLERK_JWKS_URL` was unset (one env var from world-callable); (2) ~64 FastAPI HTTPException sites + the Next user-write routes echoed raw upstream error text (a LibsqlError carrying the Turso URL + `auth_token`, or an IB account id) straight into client response bodies. Both surfaced because a test stream tried to ASSERT the safe behavior and discovered the unsafe one. Fix info-disclosure at the CHOKEPOINT, not N call sites: a single `StarletteHTTPException` handler scrubs every raised detail; `jsonApiError` scrubs message+detail once (plus any route that builds its error body inline). Scrub Turso URLs, `auth_token`/bearer/JWT, and `U#######` account ids. Pin leaks with strict-xfail so CI stays green AND the fix is forced to remove the marker.
- **Mutation testing found what coverage couldn't — the Kelly sizing had 0/20 mutation-kill despite being "covered."** A real mutation pass on the money math (sign flips, ×100/÷100, the 2.5% bankroll-cap constant, `min()`, denominator swaps) proved the assertions were faithless: kelly 0/20→18/20, vectorized greeks 4/11→11/11, ts P&L 18/27→22/27, order-risk 23/24→24/24. The Kelly Gate-3 bankroll cap could be flipped to 5% with NO test failing. Two recurring root causes: (1) tests asserting RELATIVE checks (`>10.0`, `>0`, direction-only) instead of the EXACT value; (2) the test re-deriving the expected value from the production function (`expected = kelly(...)` / `sum(result['leg_deltas'])`) so a formula mutation infects test and prod equally and cancels out. Pin EXACT values derived INDEPENDENTLY (hand math / a reference impl), never re-derived from the code under test. Safe pass mechanics: mutate prod → run targeted test → revert; only test files change; verify prod `git diff` empty + re-derive a sample of pins independently to avoid asserting-the-bug.

## 2026-06-11

- **A listening host port is not proof that IB Gateway's API is available.** In this topology Docker/socat can keep host `:4001` open while the Java Gateway has no internal `127.0.0.1:4001` listener; health must verify the internal listener or a fresh protocol handshake before reporting the relay/API pool connected. Never declare a relay outage fixed from process state or cached pool flags alone.

- NEVER run a synchronous/blocking libsql call (`db.execute`/`db.commit`) directly on the FastAPI event loop. On the single-worker uvicorn, a hung direct-cloud write freezes the WHOLE API: `/health` and `/health/lite` time out, every request stalls, while the relay/data plane stays fine. Diagnose with `py-spy dump --pid <uvicorn MainPID>` (install into the venv) — it shows the MainThread blocked in the offending call. Offload fire-and-forget DB side-effects (the dual-write mirror + service_health heartbeat) to a dedicated bounded background thread; use `asyncio.to_thread` for request-scoped blocking I/O. Commit c9e518a.
- A wedged `/health` can block its OWN fix from deploying: `deploy.sh`'s `wait_for_gateway_ready` gate curls `/health`, so if the API is frozen the gate fails and deploy.sh rolls back. For a Python-only fix, deploy manually: on the VPS `cd /home/radon/radon && sudo -u radon git fetch && git reset --hard origin/main && systemctl restart radon-api` (no Next.js rebuild needed for server-only changes).
- systemd `Type=oneshot` has NO start timeout by default — a hung oneshot cycle runs forever and, because oneshot can't overlap, permanently stalls its `OnCalendar` timer. Always set `TimeoutStartSec=` on oneshot units that do network I/O (the api-watchdog hung 6h this way). e841530.
- Every radon-* service MUST set `Environment=RADON_DB_NO_REPLICA=1`. The api-watchdog was the lone unit missing it, so `get_db()` took the embedded-replica branch and ran `conn.sync()` against a resurrected 1.36GB `data/replica.db` every cycle (hanging it + violating the no-replica contract). When a radon process intermittently hangs on a Turso write, check this flag + that `data/replica.db` does not exist FIRST. 59fd5f7.
- `radon-api` must `Wants=` (not `Requires=`) `radon-ib-gateway.service`. `Requires=` propagates the gateway's explicit stop to api, so a UI "Stop Gateway" killed the control plane (`/health`, `/admin/services`, the Start action). `Wants=` keeps api alive when the gateway is deliberately stopped (api already reports a down gateway via `upstream_dead`). radon-cloud 741caaf.
- A whole-stack `radon restart` does NOT hold the 2FA push lock, so the watchdog's stuck-2FA self-heal can stack a second push, and IBKR then rejects every approval ("unsuccessful"). When the user says "I keep approving 2FA but it fails," STOP having them tap: `POST /ib/reset-backoff` (clears the stale lock) then a single `POST /ib/restart` (acquires the lock, freezes the watchdog), approve ONE fresh push, then `systemctl restart radon-api`.
- Run the FULL test suite (`vitest run web/tests`, `pytest`) before pushing, not just the files you touched. A UNIT_DEPENDENTS change broke a lock test in `admin-reliability.test.ts` that I hadn't run; a section addition broke `data.test.ts`. Both failed CI after I'd "verified" the subset.
- For server-side screenshots/headless-browser work on the VPS, use Playwright (installed for the newsfeed at `/home/radon/.cache/ms-playwright`), NEVER the laptop-only `agent-browser` CLI. The Twitter share-card PNGs were empty because the generators shelled out to a tool that isn't on the VPS. 858d251.
- The relay's stale-tick recovery is a bounded ladder that ALERTS; it does NOT restart the gateway anymore: after K=3 reconnect cycles with no ticks during RTH it writes a `service_health` error row (`ib-realtime-relay`) for the watchdog to alert on. Gateway-side farm-down (gateway authenticated but relay gets zero ticks) needs a full `radon restart`, not a relay-only restart. 9cdcf3e.

## 2026-05-31

- For local Radon verification, use `scripts/local.sh` as the canonical launcher instead of hand-starting Next.js and FastAPI unless the task explicitly requires isolated services.
- When checking Unusual Whales access in Radon, load `UW_TOKEN` from `web/.env` before concluding provider access is unavailable; the repo-root `.env` may not carry UW credentials.
- When fixing mobile dashboard section layout, do not rely only on visual screenshots or implicit flex stretching. Add a geometry regression that compares left/right/width across every reordered dashboard section and across the actual bordered inner panels (`.snapshot-card` vs `.dashboard-news`) so one panel cannot silently shrink.

## 2026-05-29

- When a startup portfolio-derived summary names positions the user says are no longer held, treat the cache as guilty first. Any startup analyzer that reasons about current holdings must sync from Interactive Brokers or fail closed with an explicit "cannot verify" message instead of reading stale `data/portfolio.json` and presenting phantom positions.

## 2026-05-28

- When the user asks whether the work was committed and pushed, stop implementation churn and immediately verify `git status`, update task docs, commit the intended changes, push, and report the exact commit hash plus any verification blockers.
- For mobile lightbox controls, validate the actual coarse-pointer viewport. Narrow desktop CSS is not enough: touch-only affordances like hidden chevrons and fixed close buttons must be scoped with `(pointer: coarse)` so mouse users keep visible navigation.
- Desktop touchscreens and hybrid devices often match `any-pointer: coarse` while their primary `pointer` remains `fine`; touch target fixes for dropdowns must include `any-pointer: coarse`, not only `pointer: coarse` or mobile widths.

## 2026-03-29

- gnzsnz IB Gateway Docker image uses socat on port 4003 to proxy to Java Gateway on localhost:4001 inside the container. External port mapping must be `host:4001 → container:4003`, not `4001:4001`. The `4001:4001` mapping only works from localhost because Java binds to `127.0.0.1`.
- IB Gateway's "Allow connections from localhost only" GUI checkbox (`jts.ini` `TrustedIPs`) cannot be reliably overridden via config.ini or sed — the GUI setting wins. Must uncheck via VNC (Configure → API → Settings). Setting persists in Docker volume.
- FastAPI's `Depends(verify_clerk_jwt)` runs independently of middleware. Both need localhost bypass for server-to-server calls to work. Adding bypass to middleware alone is insufficient.
- WS relay ticket validation also needs its own localhost bypass, separate from FastAPI. The relay is a Node process with its own auth logic.
- `NEXT_PUBLIC_RADON_API_URL` (browser env var) doesn't work for cross-origin ws-ticket in local dev (browser on :3000, FastAPI on :8321). Solution: route through Next.js API proxy (`/api/ib/ws-ticket`) which calls FastAPI server-to-server.
- When switching gateway modes, `.env` is gitignored so each environment (Mac, VPS) manages its own. Code defaults to `docker` but VPS overrides via systemd `EnvironmentFile=`.

## 2026-03-24

- Do not default to `python3.13 -m pytest -q` for every scoped fix in this repo. Use an affected-file runner first so Python verification tracks only the changed surfaces, then escalate to broader pytest only when the change genuinely crosses many Python modules.
- When a user explicitly names a skill and asks for a separate agent, use that skill in a dedicated subagent and integrate its result instead of handling the work only in the main thread.
- For order-placement failures, never dump transport-layer strings like `Radon API 502: ...` into the UI. Preserve upstream status/detail at the Next route boundary, then convert broker prose into short operator-facing summary/detail copy in a shared banner.
- When UI verification guidance prefers `chrome-cdp`, check whether that skill is actually available in the current Codex runtime before planning around it. If it is unavailable, explicitly reuse any already-running dev server and fall back cleanly instead of trying to boot a duplicate app instance.
- IB Gateway CLOSE_WAIT (port listening but upstream IB session dead) causes `TimeoutError` on every API call but was NOT caught by the auto-recovery pattern matcher which only matched `ECONNREFUSED`. Fix: add `TimeoutError` and `API connection failed` to `_IB_CONN_REFUSED_PATTERNS`, add `_has_close_wait()` via `lsof` to detect dead upstream at startup, and have the restart script kill lingering IB/IBC Java processes (`pgrep -f 'ibgateway|IBC|ibcontroller'` + `kill -9`) before restarting.
- When modifying an IB order by re-submitting via `placeOrder`, the open-order snapshot may contain stale VOL fields (`volatility`, `volatilityType`) populated by IB. Re-submitting these on a LMT order causes IB error 321 ("VOL order requires non-negative floating point value for volatility"). Fix: reset both fields to IB sentinel values (`1.7976931348623157e+308` and `2147483647`) before `placeOrder` on non-VOL orders.
- IB's `cancelOrder` and `placeOrder` (modify) are BOTH scoped by clientId — only the clientId that placed the order can cancel or modify it. Master client (clientId=0) can SEE all orders via `reqAllOpenOrders()` but CANNOT cancel/modify them (Error 10147 for cancel, Error 103 for modify). Pool-based order management only works for orders placed by the pool's own clientId. Since orders are placed via subprocess (`ib_place_order.py`, auto range 20-49), cancel/modify must also use subprocess that reconnects as the original clientId.
- Auto-recovery that restarts IB Gateway on ANY subprocess connection error is too aggressive and causes cascade failures. A subprocess can fail for many reasons (client ID collision, VOL error, transient timeout) while the Gateway is perfectly healthy. ALWAYS verify Gateway health (`check_ib_gateway()` — port listening + no CLOSE_WAIT) before restarting. Only restart when the Gateway is genuinely unreachable. Similarly, the restart script must only force-kill pre-existing PIDs, not newly spawned processes.

## 2026-03-22

- When a route handler gates fresh data fetches behind `isMarketOpenNow()`, any data published after market close (UW end-of-day processing, overnight batch updates) will never be picked up until market reopens. Use a data-staleness check (compare latest data-point date to today) instead of a market-hours guard. This prevents stale charts across ~17.5 hrs/day of market-closed time plus weekends.

## 2026-03-21

- Never hardcode `"python3"` in subprocess calls. Use `sys.executable` so child processes inherit the same interpreter as the parent. On this machine, `python3` resolves to Homebrew Python 3.14 (missing `ib_insync`, incompatible asyncio) while the FastAPI server runs Xcode Python 3.9. The PATH order varies between shell, Node.js, and Codex environments.

## 2026-03-19

- For `/internals` skew history, treat the skew metric source and the expiry-discovery source as separate decisions. If the user explicitly chooses UW for the metric, do not keep IB in the path just because it can supply expiries; make the route semantics match the chosen provider.
- For `/internals` skew work, honor the market-data source order explicitly: check IB first for the option-chain/expiry surface before falling back to Unusual Whales for the skew metric itself. Do not assume IB is irrelevant just because the final skew value comes from UW.
- When a feature fix is green in focused tests but the repo-level gates are still red, stay on the blocker chain before switching to docs/commit work. Re-run the full suites, trace the remaining failures by boundary (backend seam, provider contract, frontend bridge), and clear those blockers first so the task can actually close.
- For IB cancel/modify bugs, do not trust the original `Trade` object or collapse provider errors at the bridge layers. Re-check refreshed open orders, treat disappearance after cancel as success, extract human-readable JSON script errors in FastAPI, and preserve upstream status/detail through the Next order routes so the browser sees the real failure.
- When a ticker-detail or quote-telemetry surface shows a synthetic multi-leg option quote, do not label a derived combo midpoint as `LAST` or build it from asynchronous leg trade prints. For spreads and risk reversals, use the live combo mark from bid/ask and label it clearly as a mark so it cannot be confused with the entry fill basis.
- When a combo or short-option surface already carries sign semantics from the portfolio model or combo quote math, do not normalize it with `Math.abs()` in a detail tab or order form; add regressions that assert both the displayed sign and the tone class survive into the input and leg table.
- When refactoring shared portfolio row math across `PositionRow` and `LegRow`, do not let a child row reference a parent-only variable by name; add a render-path regression for expanded legs so scope leaks fail in test instead of at runtime on `/portfolio`.
- When a user points to a specific bad position as the clue for a portfolio-wide P&L bug, pivot the reproduction to that exact live position first. Verify the cached portfolio snapshot, the live websocket quote payload, and the rendered row/card for that symbol before designing a generic fix, or you risk fixing the wrong layer.
- When a dashboard Day Move bug involves a same-day position, compare the rendered close-based move to `ib_daily_pnl` before trusting the math. `reqPnLSingle` uses fill basis for intraday adds, so prior-close calculations can show the opposite sign even when the provider data is already correct.
- When a user flags a second symbol as having the "same" pricing problem, check both the visible row path and any shared synthetic spread helpers. A row-level stale-last fix can leave shared combo/spread calculations still trusting raw option `last` prints outside the live market.
- When a cached panel auto-syncs on mount through a shared hook, guard the initial GET path against StrictMode-style double effects. Otherwise a second stale cache read can overwrite the fresh POST result and make the auto-refresh appear broken even though the network request succeeded.
- When a background refresh fails but cached data is still being shown, surface the upstream error instead of suppressing it. A stale table with no warning looks current and wastes debugging time; a degraded-but-explicit view makes the real provider boundary obvious.

## 2026-03-17

- IB Gateway's market data feed can go stale: the TCP connection stays alive (port 4001 listening, control plane responding to `qualifyContracts`) but the data plane stops delivering ticks. All `reqMktData` calls return nan indefinitely. The fix is restarting IB Gateway. This happens when the internal session expires overnight. The WS relay server should detect this condition (subscriptions active but zero ticks received for >30s during market hours) and auto-restart the gateway.
- Static IB client ID registries are fragile — they cause recurring collision bugs when persistent pool connections hold IDs that subprocess scripts also try to use. The durable fix is range-based allocation: partition the ID space (pool=0-9, relay=10-19, subprocess=20-49, scanners=50-69, daemons=70-89, CLI=90-99) and have on-demand scripts use `client_id="auto"` which picks a random ID from the range and retries on conflict.
- Python 3.9 does not support the `int | str` union type syntax (requires 3.10+). Use `Union[int, str]` from `typing` or omit the annotation. This caused `TypeError: unsupported operand type(s) for |` when subprocess scripts were spawned.
- IB executed orders should be grouped by position (opening/closing) rather than shown as flat fills. Group by underlying symbol + time bucket (60s). BAG fills are the combo envelope (quantity, net price); OPT fills are the legs (P&L, commission). P&L and share data belong on the closing position group, not individual fills.
- When computing P&L % for a position group share card, use the aggregated OPT leg notional (avgPrice × qty × multiplier per leg), not the BAG envelope price. BAG fills have commission=0 and realizedPNL=0 — they're metadata, not execution data.
- Combo net credit on open-order rows is quantity-aware: scale each leg by its effective ratio/size before summing quote values. Ignoring this turns 1x2 risk reversals (for example 25 short puts / 50 long calls) into incorrect net credit/debit values and can mislead execution/hedge decisions.
- For options-chain combo entry, treat leg quantities as the operator's absolute desired contract counts at the UI boundary, then normalize them to `combo quantity + per-leg ratio` before computing net quotes or building the IB payload. If the UI prices raw counts but submits hardcoded `ratio: 1`, the displayed net credit and the actual order structure diverge.
- Option expiries must be canonicalized at the shared contract layer, not ad hoc at individual call sites. If portfolio positions use `YYYYMMDD` but the chain page or websocket client keeps dashed expiries from `/api/options/expirations`, held option legs can miss quotes even though the same contract is already subscribed elsewhere in the app.
- For closed combo share cards, never source the entry basis from the first open BAG on the same symbol. Match opening fills to the closing combo's exact option contracts and quantities, then derive the signed basis from opening cash flow so net-credit risk reversals render negative entry prices and correct return percentages.

## 2026-03-18

- When the backend already preserves descriptive order metadata, do not let the open-order display model throw it away for single rows. Trace the full path first: IB open orders -> sync serialization -> cached JSON -> API -> renderer. If the API already has `secType/right/strike/expiry`, build a frontend summary from that contract instead of rendering only the bare ticker.
- For `/orders` modify flows, separate "mutate the existing order" from "change the structure of the order." Price and quantity can ride the IB modify path; combo leg edits cannot. If the operator needs to change BAG legs, the UI and API must switch to cancel-and-replace instead of pretending a price-only modify modal is sufficient.
- When a modal graduates from a single-field dialog to a structured order editor, do not keep it inside the default narrow shell or reuse summary-row flex styles for input rows. Give it a dedicated modal width and field-grid layout as soon as it contains multiple logical sections, or the UI will become technically functional but operationally unusable.
- When widening a modal for a new editor, do not stop at the shell width. Measure the usable width of each inner panel and compare it to the minimum width implied by the field grid. A modal can be “wide” and still clip badly if a nested grid keeps desktop-style column minimums that exceed the panel it lives in.
- For IB order modification, never treat an unchanged `Submitted` / `PreSubmitted` status as proof that the modify succeeded. An already-open order starts in `Submitted`, so success must be confirmed against a refreshed open-order snapshot that reflects the requested price or quantity; otherwise the API can report a false positive that the `/orders` UI later contradicts.

## 2026-03-16

- IB BAG (combo) orders use `ComboLeg.action` to define the spread structure and `Order.action` (BUY/SELL) to control direction. When `Order.action=SELL`, IB reverses all leg actions. Never flip `ComboLeg.action` based on the trade direction — that creates a double-reversal and triggers IB error 201 ("Riskless combination orders are not allowed"). Always: `LONG → BUY`, `SHORT → SELL` in `ComboLeg.action`.
- When reconstructing a portfolio equity curve from trade fills, snap weekend/holiday trade dates to the next valid calendar trading day. Trades on non-calendar dates get counted in the back-solve equation but never applied during simulation, causing the ending equity to diverge from IB net liquidation.
- The `formatExpiry()` function adds dashes (`"20260327"` → `"2026-03-27"`), but IB's `Option` constructor expects the raw `"20260327"` format. Never call `formatExpiry()` in order payloads sent to IB — it causes "Could not qualify contract" 502 errors.
- BAG orders in `orders.json` don't include combo leg contract details by default. To resolve BID/MID/ASK for spread modify modals, qualify each `ComboLeg.conId` during order sync to store full contract details (symbol, strike, right, expiry). Without this, the modify modal shows "No real-time market data available" for spreads.

## 2026-03-15

- IB Gateway's CLOSE tick on weekends returns the penultimate session's close, not the last trading day's close (e.g. Wednesday's $106.19 instead of Friday's $96.81). Never use IB's `close` field as a reliable "previous close" for stocks; only cash indexes (VIX, VVIX) report their actual value via the CLOSE tick. Use the previous-close API (UW/Yahoo) instead.
- The first FastAPI migration attempt (2026-03-12) failed because of three traps: dual-path spawn-fallback, file-writer scripts that don't fit JSON response model, and three-service startup fragility. The successful approach: no spawn fallback (serve cached on failure), subprocess execution for all IB scripts (avoids ib_insync event loop conflicts), and auto-restart IB Gateway on ECONNREFUSED.
- When `ib_insync.IB.connect()` is called from `asyncio.to_thread()`, the thread has no event loop. Fix: create and set a new event loop in the thread before connecting (`asyncio.set_event_loop(asyncio.new_event_loop())`).
- IB doesn't allow duplicate client IDs. When a connection pool holds client_ids 0/11/31, subprocess scripts must use different IDs (40/41) or they'll get ECONNREFUSED.
- When reverting an optimization campaign that renamed CSS classes across 60+ files, identify which files were NEW features built during the campaign (keep them) vs. existing files that were modified (restore from pre-optimization). Use `git ls-tree` to check file existence at the target commit.

## 2026-03-13

- Before a ship/push step, confirm whether the current checkout is already on `main`; do not talk about merge cleanup as if a worktree branch exists when the active checkout is already the main branch.
- When a scoped commit leaves behind a dirty regression file that encodes a stronger route contract, do not just explain why it was excluded; either revert it immediately or ship the stronger contract as the next scoped change so the worktree does not stay in a half-promised state.
- When a derived page like `/performance` depends on the portfolio sync lifecycle, do not give it an isolated long-poll loop and assume the cache route will save you; if the shell can advance `portfolio.last_sync` first, the dependent page must react to that fresher timestamp and revalidate immediately.

## 2026-03-12 (Cloud Migration — Reverted)

- Before refactoring a route from `spawn()` to `fetch()`, read the original wrapper code to understand output format (stdout JSON vs file write vs human text). `ib_sync.py` writes to `portfolio.json` and prints human-readable text — the FastAPI endpoint and fallback both assumed JSON stdout.
- When adding a new service dependency (FastAPI), the app must work identically without it. The "fetch failed" error surfaced in production because the fallback wasn't tested.
- Don't report refactored routes as "complete" without `curl`-testing both GET and POST. The portfolio POST was broken but reported as done.
- Three-service startup via `concurrently` (Next.js + IB WS + uvicorn) is fragile — port conflicts cause silent failures. Prefer two-service until the third is proven stable.
- When shelving incomplete work, save all code to a tmp directory with documentation before reverting, so it can be resumed later.

## 2026-03-12

- When a post-close dashboard depends on a cache-backed market scan, do not treat the first `market_open=false` payload as final unless its session date matches today; if the close-transition scan still returns the prior session, fix the scan to synthesize today's closing snapshot from quote sources instead of relying on the UI to keep retrying.
- When a dashboard route already encodes a one-minute freshness contract in its GET path, do not poll that surface every five minutes with POST rescans from the browser; use GET polling at the route cadence so scheduled close updates can land in the open page automatically.
- When a chart point encodes the current classified regime, do not hardcode its highlight color independently of the shared classifier; the marker, summary label, and state key must all derive from the same quadrant state or the UI will contradict itself.
- When adding a new shell-level alert surface for an actionable broker issue, do not broaden it into a generic connection-status banner; keep the new banner scoped to the actionable state and leave generic reconnect/disconnect notices on the existing toast channel.
- When a repo-owned dev service binds a fixed local port, do not let `EADDRINUSE` crash the whole startup workflow; detect the port conflict and either reuse the existing listener intentionally or fail with an explicit, non-catastrophic message.
- When a stacked telemetry row still wastes width after collapsing to one line, do not just center the cluster; anchor the primary label/value on the left and use the remaining width for an intentional meta rail.
- When a telemetry strip collapses to single-column rows, do not just preserve desktop stacking inside each cell; use the full row width with a compact horizontal presentation so the operator does not waste half the viewport on empty space.
- When a telemetry strip collapses to single-column mobile cells, do not keep the desktop `1fr auto` change-row layout; the delta text and arrow should stay left aligned as one inline signal, not split across the cell width.
- When an operator asks for a 3 x 2 telemetry strip, removing the empty slot is not enough; the second-row cards must still read as intentional peers, which means equal-width bottom-row cards rather than one narrow card plus one stretched card.
- When an odd-number telemetry strip collapses into a multi-column grid, do not leave the final slot empty and let the strip background read as a fake card; make the last real card span the remaining columns or collapse the strip further.
- When an operator asks for a diagnostic chart to behave like an existing analytical time-series chart, match the interaction affordance directly; static lines are not enough when the comparison depends on point-in-time values.
- When an operator asks how to make a regime tooltip actionable, do not stop at metric definition; include the concrete portfolio posture and trade response for both sides of the signal.
- When a dashboard row contains only two dense diagnostic panels, do not leave it as a permanent `1fr 1fr` split on narrow widths; stack the panels earlier and remove inline layout rules so the responsive contract lives in CSS and tests.
- When five telemetry cards cannot fit cleanly, do not hold the strip on a single row past readability; abbreviations can help, but the layout still needs an earlier balanced collapse breakpoint before cards clip or truncate.
- When a dense strip only breaks at narrower desktop widths, prefer a semantic abbreviation and tighter spacing before dropping to a wrapped multi-row grid; preserve the operator’s scan rhythm until the viewport is genuinely too small.
- When a dense telemetry strip starts collapsing on narrower desktop widths, do not keep forcing a fixed five-column grid; give the cards a responsive grid with real minimum widths and let supporting lines wrap or stack before values overlap.
- When an operator says the spread is “10c, not $500,” treat the quote ladder as quote quality telemetry, not order-size cost; render raw spread width and percentage, and do not multiply by quantity or contract multiplier on that surface.
- When an operator narrows a process-cleanup request from "all processes" to only dev/test processes, verify the live process list again and target just the repo-scoped dev servers and Playwright runners instead of unloading background product services.
- When an operator narrows a scheduled service window, update the launch schedule source, installer/status text, docs, and the live loaded LaunchAgent together; changing only the plist on disk is not enough.
- When the modify-order modal surfaces spread telemetry without displaying quantity, do not scale the dollar figure by `order.totalQuantity`; use the quote-level execution friction the operator can act on, not hidden order-size notional.
- When a quote ladder is shared across non-order tabs like `Company` and `Position`, do not scale spread notional by the held position size; reserve quantity-sized spread friction for explicit order-entry and modify flows.
- When an operator corrects quote presentation on an order ticket, identify the actual shared telemetry component from the screenshot before patching the nearest modal; quote-order bugs can live in `PriceBar` or another shared display layer rather than the modify form you first suspect.
- For order-ticket spread telemetry, render the quote ladder in market convention order (`BID`, `MID`, `ASK`) and show spread width in both dollars and midpoint-based basis points so fill-quality context is visible without manual conversion.
- When an operator says the spread notional on an order ticket is wrong, verify whether the display should scale by displayed quantity as well as contract multiplier; per-contract option points are not the same thing as order-level notional friction.
- `border-collapse: collapse` on a `<table>` breaks `position: sticky` on `<th>` elements in all major browsers; always use `border-collapse: separate; border-spacing: 0` when sticky headers are needed.
- When a helper like `computeNetPrice()` already multiplies by `leg.quantity`, the display layer must not multiply by quantity again; always trace the data flow to verify what's already baked into the value before adding multipliers.
- When IB returns error code 200 ("No security definition"), handle it like code 354 — silently clean up the subscription instead of logging red errors that flood the console for every invalid option strike.
- IB Gateway holds stale client sessions in CLOSED socket state even after the connecting process dies; the relay server cannot simply retry with the same client ID. Implement a client ID pool (e.g. [100, 101, 102]) with automatic rotation on "client id already in use" errors, and extract all `ib.on(...)` handlers into a reusable `wireIBEvents()` function so they can be reattached to the new IB instance after rotation.
- When multiple processes (`concurrently` dev server + standalone `nohup` relay) compete for the same IB client ID, the second connection silently fails or evicts the first. Ensure only one relay instance runs per client ID pool.

## 2026-03-11

- When a user reports stale scheduled market data, verify the live scheduler state, last successful run timestamp, and recent service logs before assuming the route cache is wrong; freshness bugs are often orchestration failures, not rendering bugs.
- For MenthorQ service debugging in this repo, treat the project root `.env` as the credential source of truth before checking `web/.env`; the launch wrappers explicitly source root `.env` for CTA auth.
- When a scheduled workflow depends on Python packages in this repo, do not trust the first `python3` on PATH; resolve an interpreter that actually has the required runtime modules before declaring the service healthy.
- When an external login fails at one timestamp but succeeds later, report it as an observed transient rejection tied to that run; do not overstate it as a permanent credential/account failure without a fresh re-test.
- When a marketing-site card uses split metric tiles with large mono values, treat each tile as a constrained layout container: add `min-w-0` and wrapping rules up front so long tokens cannot bleed across the grid boundary at desktop widths.
- When closing a refactor roadmap, do not leave concrete residual chart-contract gaps only in the final prose; capture them immediately as follow-up tasks with explicit verification targets so the next pass starts from an auditable backlog instead of a narrative summary.
- When a new operator-facing regime label is added to `/regime`, document the state definitions and classification rule in the README and the relevant strategy docs in the same change; do not leave the meaning trapped in code or tooltips.
- When an operator is comparing two regime inputs for signal value, do not stop at parallel time-series lines; add a relationship-first view that makes spread, divergence, and quadrant state explicit.
- For dashboard charts that encode time or magnitude, always include visible axis/tick context or equivalent scale labels; a line-only chart is insufficient for operator interpretation.
- For index-style live feeds, do not assume the broker `close` field is the correct prior-session anchor for a day-over-day UI calculation; compare it against the cached official close from the authoritative daily source before using it in the strip.
- When shipping theme support, audit SVG/chart surfaces and their companion meta panels separately from the shell; hardcoded chart gradients can stay dark even after the page chrome switches to light mode.
- For UI tooltip copy, describe what the operator is seeing and why it matters; do not surface implementation details like charting libraries, websocket feeds, or transport mechanics unless the user explicitly asks for them.
- When a shared `section-header` uses `justify-content: space-between`, never leave a help icon as its own sibling between the title and a right-side status badge; wrap the title and icon into a left cluster.
- When a user asks for docs and a commit checkpoint after a fix, update the relevant docs and create the scoped commit before starting the next feature; do not roll the follow-up feature into the same uncommitted batch.
- When a user reports that a cache-backed bug fix still is not visible, verify the actual on-disk artifact and refresh path immediately; a correct code path is not sufficient if the generated cache never updated successfully.
- When a user reports that a cache-backed UI is still stale after a code fix, verify the on-disk artifact shape and the refresh path before declaring the bug closed; a correct code path is not enough if the live cache never refreshed successfully.
- When a user provides an authoritative upstream fallback source, replace the generic last-resort source in both code and docs immediately; do not keep Yahoo ahead of a vendor-native feed just because it already exists in the codebase.
- When a user provides an authoritative market-data fallback source, move that source ahead of generic last-resort providers in both code and docs immediately; do not leave Yahoo as the fallback if the product spec now requires a stronger primary source.
- When a user corrects a claim about live market-data availability, verify the actual subscription and render path before blaming the provider; the UI may simply be ignoring a live IB field that is already subscribed.
- When matching an existing metric-card pattern, copy the card’s information hierarchy, not just the data source: main value, then daily move line, then muted context subline.

## 2026-03-10

- When a user reports a performance-vs-portfolio mismatch, verify both route freshness windows and the exact ledger date format before trusting a reconstructed equity curve; stale caches and `YYYYMMDD` trade dates can invalidate the anchor logic.
- When documenting or automating IBC control in this repo, treat the canonical service as the secure machine-local wrappers under `~/ibc/bin/`, not as a repo-owned service.
- Repo scripts for IBC remote control must be described as convenience wrappers around `/Users/joemccann/ibc/bin/*secure-ibc-service.sh`, never as the primary service implementation.
- For market index integrations, do not assume a daily historical bar matches the authoritative displayed value on the exchange dashboard; verify the exact field semantics against the live source before wiring the signal.
- For repo changes that touch a monorepo with separate deploy targets, check whether deployment filters are needed so unrelated pushes do not rebuild unaffected apps.
- When a generated output directory appears in the worktree and should not be versioned, add an explicit root `.gitignore` entry immediately instead of leaving it as recurring untracked noise.
- When collaborating on parallel changes in this repo, treat any file the user says they already changed as reserved unless a direct integration change is unavoidable; design around the declared contract first to avoid stomping concurrent work.
- When fixing Codex skill manifests, validate the YAML frontmatter types directly; bracketed placeholder text after `description:` becomes a YAML sequence and the loader expects a plain string.
- When a user reports Codex's skill-description context-budget warning, diagnose the installed/enabled skill and plugin inventory first; do not treat it as an MCP server failure merely because MCP-backed plugins may contribute capabilities.

## 2026-03-18

- For IB combo entry from the options chain, do not derive the BAG `Order.action` from whether the combo is a debit or credit; the chain builder must keep the combo envelope on `BUY` for entry orders and let the leg actions define the structure, or IB will reverse the spread.
- When a chain order builder transitions from a single-leg state to a combo, do not preserve a stale manual net-price override; reset the top-level manual-price flag on structural leg changes so the limit field re-bases to the combo quote.
- For ticker-chain Playwright coverage, do not rely on ad hoc `ws-price` custom events to drive option quotes; use a mocked WebSocket subscription path when the page logic consumes live prices through `usePrices` rather than DOM events.
- When a user asks to harden process memory after a regression, propagate the rule to every instruction surface in the repo (`AGENTS.md`, `CLAUDE.md`, `.pi/AGENTS.md`, structured memory facts, and `tasks/lessons.md`) in the same change; do not stop at the lesson log alone.
- When porting a rule from Claude-specific docs into Codex docs, translate it into Codex-native instruction style and state any runtime-dependent capability explicitly; `chrome-cdp` is available in this repo session, but Codex docs must still define a Playwright fallback because skill availability is host-dependent.
- For `/orders` modify telemetry, never show raw IB bid/ask in isolation when a resting limit order is already working; overlay the live order onto the displayed best bid/ask so the modal reflects the actionable book the user is participating in.
- For `/orders` combo rows, do not disable modify just because the row is a frontend grouping; if IB delivered separate leg orders, synthesize a BAG-style edit target and cancel every underlying leg before placing the replacement combo.
- When a user asks for docs, commit, and push after a fix, verify whether repo-level unrelated failures still block the commit path before optimizing for push completion; if verification is still red, pivot immediately to resolving or explicitly triaging those failures instead of continuing toward commit.
- For sync-based client hooks, `active=false` must mean "no polling/background sync," not "never perform the initial cached read"; otherwise closed-market pages like `/internals` can hang on their loading state forever.
- When a route bug comes from a shared polling hook, audit every other hook that gates on the same `active` flag before stopping; `/internals` and `/portfolio` were the same class of closed-market cached-load failure split across `useSyncHook` and `usePortfolio`.
- When a route must remain implemented but hidden from operators, do not remove it from shared route metadata just to satisfy tests; keep the route entry and add an explicit UI-hidden flag so the sidebar and route-resolution code can diverge safely.

## 2026-06-17

- Do NOT read an anonymous `curl` 404 on a protected `app.radon.run` route as an outage; the auth perimeter returns 404 (the `__next_error__` not-found page) for unauthenticated requests by design (CI's "Perimeter smoke" asserts this). For liveness, curl a PUBLIC route (`/sign-in` → 200) and check CI-green + a real page render in the `radon-nextjs` log. I burned significant effort chasing a false "app is down."
- When Chrome Debug shows `ERR_SOCKET_NOT_CONNECTED`/`ERR_INVALID_HANDLE` but `curl` from the same laptop reaches the site, it is a local browser/VPN problem (VPN-on-VPN, wedged Chrome network service), not the app; stop driving the browser, rely on curl + unit/CI evidence, and ask the user to eyeball their own browser.
- A close-out (`closeOut` on `OrderRiskInput`) must be detected for BOTH directions: SELL-closes-LONG and BUY-closes-SHORT. A surface that re-invents single-leg detection inline (`OrderTab`) and only ports the long branch makes a buy-to-close-short show OPENING risk; mirror `lib/order/positionTrade.ts`'s signs (short close: proceeds negative, `entryCostDollars` negative = the credit).
- A VIX option's displayed underlying must come from `resolveUnderlyingSpot` (per-expiry forward `fwdCurve[expiry]` → `fwd` → cash), never `prices[ticker].last`; VIX options settle off the future for their own expiry, so spot makes the strikes look absurdly OTM. Only `VIX` is forward-priced (SPX/NDX/RUT are cash-settled).
- When fanning out parallel agents over the web app, `app/globals.css` is a single-owner bottleneck — give one agent exclusive ownership and have the rest report needed CSS for a serialized merge, or parallel edits clobber/duplicate. `WorkspaceSections.tsx` is similarly single-owner (it renders 7 surfaces inline).
- When validating an exposure-sign fix, verify both the parent aggregate row and the expanded leg rows with the live provider shape; a spread can net correctly by cancellation while individual LONG/SHORT put legs still display the wrong sign.
- When diagnosing an options-chain Greek display, do not phrase invalid provider fields as real market values; state explicitly whether the field is missing, placeholder/stale, or outside its valid domain (for example `abs(delta) > 1`), and keep IV-vs-delta conclusions separate.
- When an order-risk estimator cannot produce a numeric margin requirement for a resolved account baseline, never hide the margin row; show an explicit unavailable/IB what-if-required state so the operator sees margin impact was attempted and needs broker validation.

## 2026-06-28 — Security audit

- Any service behind Caddy sees `127.0.0.1` for every client (Caddy reverse-proxies over a fresh loopback socket). Trusting `socket.remoteAddress` / `client.host` alone = full auth bypass. The WS relay made exactly this mistake and silently disabled ticket auth for all production traffic. Trust = loopback AND no forwarding header (`x-forwarded-for`/`x-real-ip`/`forwarded`/`x-forwarded-host`), mirroring `is_trusted_local_request`. New shared core: `scripts/lib/wsTrust.js`.
- "Sandbox to a directory" (`startsWith(REPORTS_DIR)`) is NOT access control when that directory mixes public artifacts with private ones. The public `*/share/content` routes served the operator's portfolio/evaluation reports to anonymous callers. A public file route needs a positive filename allowlist + exact parent-dir pin, not a path-prefix check. New validator: `web/lib/shareReportPath.ts`.
- Adversarial verification earns its cost: it knocked the npm-audit "critical" `@clerk/nextjs` and "high" `next` middleware-bypass CVEs down to non-exploitable for THIS app (default-deny `if(!isPublicRoute)` pattern + the always-on `/api` matcher + ALLOWED_USER_IDS). Don't ship a framework bump to a live trading app just because a scanner flagged a version — verify the exploit applies to the actual architecture first, and gate the bump on a real build.
- Do NOT auto-push security fixes on this repo: `git push origin main` IS a production deploy (CI SSHes to Hetzner). Leave auth-perimeter/relay changes committed-but-unpushed and let the operator deploy + verify.
- When a security refactor moves logic out of a route, the route's source-pinning tests (`gex-share`, `regime-share`, `ib-realtime-restart-modes` asserted literal `REPORTS_DIR`/`remoteAddr ===` strings) will fail — update them to assert the NEW control (the validator/helper call), not revert.
- The web vitest suite has ~17 order-dependent failures (WS timing + DB-state) that pass in isolation; prove a "new" failure is yours by running the file alone before assuming your change caused it.

## 2026-07-03 — Destroy-storm incident + reliability sweep

- A self-heal that tears down SHARED infrastructure (undici Agent, connection pool, cached client) on EVERY failure from every caller converts one transient failure into a process-wide outage: `Agent.destroy()` aborts all in-flight requests, each rejection's catch re-triggers the heal, and the loop sustains itself until a restart quiesces traffic. Destructive self-heals need BOTH a rate limit (cooldown) and an evidence threshold (second failure within a cluster window) at the single chokepoint all callers funnel through — never at individual call sites.
- Test the reset-under-concurrent-traffic topology, not just single-caller resets. The destroy-storm regression shipped green because `db-timeout-self-heal.test.ts` only exercised one caller at a time; the incident shape (reset firing while siblings are in flight on the shared Agent) was unexpressible in the fixtures.
- Log `err.cause`, always. undici buries the discriminator (`UND_ERR_DESTROYED` = self-inflicted abort vs `ECONNRESET`/`ETIMEDOUT` = real network) in `error.cause`, and a bare "fetch failed" in journald is undiagnosable. `describeDbError()` in `web/lib/dbExecute.ts` is the canonical helper.
- Before tuning a connection pool, measure the upstream tail INDEPENDENTLY: a 35-min fresh-connection monitor on the VPS showed Turso p95 ~600ms / max ~1s off-pool, proving the ~hourly 3s timeouts were genuine latency-tail events, not socket rot. The right fix was damping the response to isolated timeouts, not pool parameters.
- A liveness probe that doesn't exercise the dependency is blind to in-process wedges: TCP-connect on :3000 stayed green while every Turso read failed for hours. Deep probes must traverse the real dependency AND judge the response body — `/api/service-health` returns HTTP 200 with a synthetic `turso-db` error row when its read fails, so status-code probes see nothing.
- Pair automated remediation with an upstream canary: the nextjs-db-watchdog only restarts when a Python-side Turso read SUCCEEDS while the Node app's fails (restart helps) and stands down when both fail (upstream outage — restarting just flaps).
- Market-hours staleness windows MUST consult the holiday calendar. `getMarketStateFromDate` ignored holidays, so an observed holiday read as "open" and applied tight RTH windows to writers that correctly skipped the day — 6-7 stale rows of footer noise. Static table SoT: `scripts/config/market_holidays.json`.
- Registering a new scheduled writer means FOUR places in the same change: `web/lib/serviceHealthWindows.ts`, `scripts/watchdog/services.py` SCHEDULED_SERVICES (CI contract test pins bidirectional parity), the right watchdog BUCKET, and a heartbeat on every run INCLUDING skip paths (`catalysts` holiday-skip aged silently).
- Vitest mock-module proxies THROW on access to an undefined export — `typeof maybeFn === "function"` does not protect against a wholesale `vi.mock("@/lib/db")` that lacks the new export; only try/catch around the access does.
- `git push origin main` no longer deploys by itself: the deploy job waits on the gated `Production` GitHub Environment (added 2026-07-03). Approve via Actions UI or `gh api -X POST .../pending_deployments`; a newer push cancels a run still waiting at the gate.

## 2026-07-03 — never `git stash pop` in the shared working tree
A red/green toggle via `git stash push -- <file>` + `git stash pop` popped ANOTHER
session's stash (stash is repo-global, branch-agnostic) when my push errored on a
malformed pathspec — merge conflicts in files I never touched. Rules:
- Toggle red/green with `git show HEAD:<file> > tmp` + swap, never stash, when other
  sessions may be active.
- If stash is unavoidable: `git stash push` → capture the printed stash ref → `git stash
  apply <ref>` + `git stash drop <ref>`, never bare `pop`.
- After any failed git plumbing, run `git status` BEFORE the next command.

## 2026-07-09 - nav icon specificity

- When adding a primary sidebar route, create or assign a dedicated semantic glyph in the Radon icon set; do not reuse an adjacent route's icon as a placeholder on a shipped page.

## 2026-07-11 - control-plane manifest completeness

- Manifest verification must be total: require every installed target to be a regular non-symlink file and exact-hash match. Targets intentionally unreadable to the deploy user, such as `0440 root:root` sudoers, need a fixed no-argument privileged verifier over the bootstrap's exact contract; never treat unreadability as optional verification.

## 2026-07-11 - retired subsystem health

- Disabling a retired writer is incomplete while its historical health row remains in alert or rendering catalogs. Gate alert evaluation, current-health APIs, and historical reliability scoring on the same concrete applicability signal, while retaining enabled-path tests for installations where the subsystem exists.
- Best-effort telemetry around native extensions must account for non-termination `BaseException` failures. Catch per telemetry item, continue independent writes, and explicitly re-raise `KeyboardInterrupt`, `SystemExit`, and `GeneratorExit`.

## 2026-07-12 - Turso read-stall RCA

- Before blaming Turso or the VPS network for a database timeout, time the exact production SQL and run `EXPLAIN QUERY PLAN`; a latest-row query that omits the leading column of a composite index can turn a nominal lookup into a multi-second full scan and temporary sort.
- Caller and transport deadlines must be ordered: the transport abort must fire before the caller timeout so a failed request releases its pool slot. A long transport timeout behind a short `Promise.race` creates invisible queued work and amplifies a single slow query.
- Do not infer original pool saturation from collateral failures after a shared-client reset. Measure running and queued counts before the reset and independently benchmark fresh Hrana/native reads to separate query cost, provider latency, and client amplification.
- Keepalive cadence is load, not free insurance. Use the slowest cadence that preserves the desired warm-socket window; a 3-second heartbeat multiplied incident traffic, while 30 seconds retained warm connections without constant dependency pressure.
- Shell refresh wrappers must capture the command's status directly. Reading `$?` after an `if` compound command can convert a failed curl into exit 0 and suppress the only reliable signal that a scheduled writer did not refresh its snapshot.
- A cache-only portfolio GET saying “live sync was not requested” is describing request policy, not root cause. Investigate the scheduled writer separately; in this incident the stale snapshot came from an IB connection timeout, while the visible Turso errors were collateral damage from the CRI query/reset loop.
- Source-contract tests must derive repository paths from the test file (`import.meta.url`), not `process.cwd()`, because local package scripts and root-level CI invoke Vitest from different working directories.
- When production drift blocks an immutable deploy, archive the divergent artifact before restoring the tested version. Runtime-enriched tracked data is evidence worth preserving even when it cannot be allowed to overwrite a reviewed release.

## 2026-07-12 - transient warning recovery

- A successful cache poll must clear a prior transport warning even when the snapshot identifier or `last_sync` is unchanged. Tying error recovery to new data makes one transient failure latch indefinitely during weekends, closed markets, or any quiet period.
- When a global degraded banner combines errors from multiple hooks, audit every producer in the fallback chain. Fixing portfolio alone is insufficient if orders uses the same same-snapshot latch.
- Separate backend recovery from frontend recovery explicitly: a green deep probe proves reads work now, but it does not prove an already-mounted client cleared warning state. Verify both state transitions before declaring the incident resolved.

## 2026-07-17 - MenthorQ dashboard authentication

- Do not infer that distinct `.com` and `.io` domains imply irreconcilable login systems. When a user identifies a redirect-mediated identity handoff, verify the complete dashboard → identity-provider → dashboard navigation before declaring the provider client blocked; preserve the server-only token boundary while adding that proven bootstrap path.

## 2026-07-17 - Options workspace entry state

- Do not use a fixed example ticker as an implicit live-data query in a reusable options workspace. Require explicit valid ticker submission, keep provider identity out of the operator display, and use the established spectral measurement loader only after a request begins.

## 2026-07-17 - Local UI artifacts

- Keep exploratory UI prototypes, mockups, and screenshot artifacts local by default. Add narrow ignore rules when they are generated so they do not appear as accidental source changes; do not ignore existing tracked report or mockup files without an explicit removal request.

## 2026-07-20 - Options measurement tables

- When an Options exposure table extends beyond the viewport, its measurement headers must be sticky, opaque, and layered above row content. Header centering alone does not preserve the column context while scanning a long strike ladder.

## 2026-07-17 - Reliability notification verification

- Do not conclude that a Pushover emergency is stopped from an application-side cancellation log alone. Confirm the operator's device delivery has ceased and, when it has not, re-open the live receipt/cancellation investigation immediately; application cooldown state is not proof of Pushover receipt state.

## 2026-07-18 - Documentation scope

- When updating ownership or deployment documentation, preserve still-current architecture, environment, authentication, service, and operator guidance. Make surgical replacements for stale authority paths and add canonical runbook links; do not replace a broad operational README with a terse pointer page unless explicitly asked.
# 2026-07-20 — Keyboard-launched overlays

- A keyboard-launched dialog must move focus to its primary text input on mount and retain a regression that verifies both focus and Escape dismissal.

## 2026-07-21 - Turso Hrana I/O bounding

- Three incidents, one week, one root cause: unbounded I/O over the direct-to-cloud Hrana pipeline. Paginate large reads on an id cursor; write bulk rows as chunked multi-row INSERT statements (executemany is one round-trip per row over HTTP); acquire a fresh connection per long-running phase so an idle-expired stream cannot poison later statements; treat "upstream forward failed" 502s and "stream not found" errors as this signature before suspecting the platform. Rules codified in scripts/CLAUDE.md "Turso Hrana I/O Bounding".

## 2026-07-19 - Host-local prune authority

- Sources backed by host-local files (gitignored reports, divergent trade_log copies) must never derive delete authority for a shared datastore from local presence or absence: a present-but-partial directory on one host deleted another host's corpus. Turso-backed sources every host can see keep prune rights; file-backed ones never do.

## 2026-07-21 - Live verification catches what suites cannot

- The rv-ratio cold backfill passed every unit, route, and e2e suite yet died on the production 240s subprocess timeout because write latency only exists against real Turso. Timeout-bounded subprocess paths need their worst-case I/O measured live once before "done"; suite-green is not evidence for wall-clock budgets.

## 2026-07-22 - SW test harnesses and workflow supervision

- Vitest fails CI on UNHANDLED REJECTIONS even with every test green, and the failure is timing-dependent (local runs can pass). Any harness that dispatches async handlers (service-worker fetch events, message handlers) must configure its mocks AND settle every dispatched promise before the test returns.
- Coverage/augmentation passes must consider ORDER-INTERNAL structure before reaching into the portfolio: a self-contained spread needs no external cover, and injecting held legs on top of an order's own cover manufactures phantom risk (SPCX bull-call-spread UNBOUNDED repro).
- Workflow implementer agents can stall mid-run leaving partial work in the tree. The tree state, not the agent's report, is the source of truth: run the verify phase manually, and check for the classic gap — a component written and tested but never mounted.
## 2026-08-11 - Synthetic combo market views

- When a user asks for a spread order book, distinguish an interpolated two-leg market from broker-published BAG depth, but do not substitute a single-leg book. If both leg books exist, offer the synthetic spread ladder with explicit implied/non-executable labeling and keep direct leg books available.

## 2026-08-13 - External sensitive-asset cleanup

- A source deletion does not complete a sensitive-asset remediation. Merge the clean source first, then purge immutable deployments/CDN caches, audit every branch/PR/fork ref, rewrite owned refs with exact leases, and explicitly track provider or fork-owner cleanup that cannot be completed from the origin repository.

## 2026-08-13 - Deployment-scoped rate limits

- A deployment-specific limiter must be gated by the resolved principal/deployment role, not by generic `NODE_ENV=production`. Validate every tier against the real client polling cadence and test the production environment contract where its credentials are intentionally absent.
- For Codex desktop startup defects, test the desktop host's bundled `Contents/Resources/codex`, not only the shell `codex`; verify app update state and run repeated headless starts through that exact binary. A larger optional-MCP timeout does not prevent first-turn capture races; use documented `required = true` when that server must always be present.

## 2026-08-25 - Never leave CDP state on the user's real browser

- `chrome-cdp` drives the operator's LIVE Chrome, not a disposable harness. An `Emulation.setDeviceMetricsOverride` left active after a measurement made the terminal lay out at 1800px inside a ~1367px window; the right ~430px rendered off-screen and the user reported a broken UI that was entirely my leftover state. Pair every Emulation override (device metrics, emulateMedia, throttling, geolocation) with its clear call in the SAME step, not "at the end" of a sequence. When a specific viewport is needed, use a local Playwright run against a built app instead. Also leave no modals open and no order tickets populated - it is a live trading account.
- Corollary for diagnosis: before chasing a "regression" in a UI the user screenshotted, first check whether the session itself mutated their browser. Measure `document.documentElement.scrollWidth vs clientWidth` and `window.innerWidth` against the real window; zero internal overflow plus a mismatched innerWidth means the harness, not the code.
- ResizeObserver notifications are throttled in background/occluded tabs. A responsive chart that "stops tracking resizes" under CDP is usually the tab not rendering, not a bug - foreground the tab (`Page.bringToFront`) and re-test before touching the code. Prove it by attaching an independent observer: if that one also never fires, it is throttling.

## 2026-08-29 - Terse closing messages are a hard format, not a preference

- The `Response Format` rule already existed at `CLAUDE.md:187` and was ignored on every turn of a long session: closing messages ran 200-350 words against a stated ~150 cap, with prose paragraphs, diagnosis tables, and "Worth knowing…" / "Correction to my earlier report…" openers the rule banned by name. Restating a guideline in prose does not change behaviour.
- The fix is structural: a literal `**Done**` / `**Next**` bullet template, a 100-word cap enforced by DELETING lines rather than compressing prose, and a banned-phrase table quoting the exact openers that shipped. Paired with a `UserPromptSubmit` hook in `.claude/settings.json` that re-injects the contract at generation time, because a rule 200 lines up in a long file loses to momentum.
- Causes belong in the commit message and PR body; chat carries state and next action only. Length creep started every time with narrating WHY something broke after the user had already been told WHAT broke.

## 2026-08-29 - Deploys must reconcile the root control plane themselves

- Every control-plane edit (helper, sudoers, polkit, control-plane unit, drop-in) and every root hot-patch of an installed drop-in blocked ALL deploys at preflight until a human ran `bootstrap-control-plane.sh` over root SSH. The durable fix is a privileged verb the deploy calls BEFORE `deploy.sh` holds the lock (`radon-deploy-root sync-control-plane`: extract `cloud/` at the GitHub main tip, run that tip's own bootstrap). Anything root must do "after a deploy" is a design gap, not an ops chore.
- A unit that looks fixed in a text assertion is not fixed: REL-138 restored `Type=notify` on containerised units and its test asserted env-var NAMES in the source, while systemd silently dropped every `READY=1` from the container cgroup. Verify supervision contracts live (`STATUS=` probe through the real socket path) before shipping them as control-plane.
- Test a background helper at the wire while its parent is still alive: the notify proxy passed 34 argv-level tests and died in 250ms in production because it was spawned inside `$(...)`.
- Before trusting a deploy on a busy day, check `df -h /` and that the pruner timer is `enabled`: a 4.8GB image pull per SHA on a 75G disk took production down with every gate failing at once, and `bootstrap` installs control-plane timers but never enables them.
- Other sessions were merging PRs and running root bootstraps on the same VPS at the same time (PR #151 reverted the same drop-ins I was fixing). Re-read `origin/main` and the installed manifest before every privileged step; never assume the tip is yours.

## 2026-08-29 - Done/Next format applies to background-job progress lines too

- Second correction in one day: a background-job session narrated every step in prose ("Waiting on the ingestion implementer…", "Full vitest green: …") because the harness asks for narration. The harness "narrate" instruction sets WHEN to speak, not the SHAPE: every emitted message is still `**Done**` / `**Next**` bullets under 100 words. Fold the pre-tool line into a `**Done**` bullet or omit it.
## 2026-08-29 - Every user-visible message is Done/Next, including mid-task and pre-tool-call lines

- The format rule was followed on closing messages and dropped on every other turn: one-line narration before a tool call ("Pre-existing duplicate keys... bypassing"), progress lines after arming a monitor ("Checks running on #180; I'll merge when green"), and status replies to `Status` prompts all shipped as prose. The user reads ALL of them as output.
- The rule at `CLAUDE.md` §Response Format says "Mid-task progress messages follow the same shape". A message that ends a turn while work continues in the background is a closing message. Use `**Done** / **Next**` there too; when nothing changed, one bullet.
- The harness "say in a line what you're about to do" instruction does not override the repo format: fold that line into a single bullet or emit nothing and let the tool call's description carry it.

## 2026-08-29 - Never round-trip a diff through `git diff > file` under the rtk hook

- `git diff HEAD -- f > patch` was rewritten to `rtk git diff`, which emits a token-compacted summary, not a patch. The follow-up `git checkout HEAD -- f` then discarded the only copy of the edit. Use `rtk proxy git diff` (or `git stash`) when the bytes matter, and never checkout-revert a file before confirming the saved patch applies (`git apply --check`).
