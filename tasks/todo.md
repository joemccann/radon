# MarketDesk model-ladder provider failures (2026-10-30)

- [x] T1 depends_on: [] Root-cause Anthropic unreadable replies, Codex 429, NVIDIA 400/404, Cerebras 413 on the shared ladder.
- [x] T2 depends_on: [T1] Fail over per rung, cool down Codex 429, skip oversized Cerebras, retry NVIDIA's documented model URL.
- [x] T3 depends_on: [T1] Keep Grok in the MarketDesk chain. Antigravity stays the documented agy CLI, not a new HTTP client.
- [ ] T4 depends_on: [T2, T3] Draft PR, focused tests green, CI on the head.

## Review

Pending CI on the draft PR. No production host, env file, or live provider call.
# Gateway exact release status identity (2026-10-31)

- [x] RI1 depends_on: [] Reproduce missing gateway release identity and reject stale/caller-controlled or mutable resolved-image identity with regressions.
- [x] RI2 depends_on: [RI1] Convey the already-resolved immutable gateway image SHA through a gateway-only fixed environment value; update owner documentation.
- [x] RI3 depends_on: [RI2] Run focused and full cloud gates and independent review; coordinate remaining full suites serially with the parent release owner.
- [ ] RI4 depends_on: [RI3] Publish the focused PR, verify exact-head strict/app-bound CI, notify once and administrator merge.
- [ ] RI5 depends_on: [RI4] Verify the committed live gateway reports its actual image SHA before native/publication and Mac activation proofs.

## Review

Actual release057 gateway startup verified model-cache assets and retained pacing, and all application units became healthy. Its status still reported version=unknown because the server reads RADON_DEPLOY_SHA while the managed runtime never supplies that field. No production environment patch or provider/authentication change is authorized by this source repair. The release owner retains deployment and live verification. Regression baseline: six failures and four controls passed. The focused gateway image/runtime replay passed 41 tests; no live environment hotpatch was made. Required owner documentation passed 195 checks after adding the mapped operations note. Full canonical cloud: 3,792 passed, 16 skipped in 146.79s. Parent independent review found no actionable P1/P2; git diff --check is clean. Canonical web passed 11,592 tests with 21 skipped; TypeScript exited 0. Two full Python runs each passed 19,734 tests, 12 skipped and 63 subtests, with the same unchanged credential-cancellation latency test failing at approximately 0.351 seconds against a 0.2-second assertion. Its complete module passed separately (11 tests). Independent audit did not establish a cause; no Python source, assertion or timeout was changed. Exact-head CI and live identity verification remain pending.

# Gateway disk headroom admission (2026-10-31)

- [x] DS1 depends_on: [] Verify actual UID1000 byte exhaustion, failed gateway migration and blocked recovery without changing broker or provider clocks.
- [x] DS2 depends_on: [DS1] Add failing regressions and minimal pre-teardown space admission for client promotion/runtime writes, with bounded migration error diagnostics and temporary-file cleanup.
- [x] DS3 depends_on: [DS2] Run focused/full gates, independent review, exact-head PR CI, one accepted green notification and administrator merge.
- [ ] DS4 depends_on: [DS3] Verify successful deployment, real gateway/model/pipeline behavior and the remaining Mac activation boundary.

## Review

DBC deployment failed with zero UID1000-available bytes despite root-reserved free blocks. Both model migrations failed before server startup; the preceding notification repair was not the cause. Automatic recovery could not reset the previous checkout because writes had no space. Parent recovery backed up and reclaimed only obsolete public native caches, restored the exact two prior Git scripts left empty by the failed reset, and completed normal recovery-only. Exact previous release 885f068ae is serving, all five managed app units are active, root verify-restored exits 0, both API health routes return HTTP 200, the transition journal is absent, and the original control-plane floor and broker remain unchanged. Signed-in dashboard reload renders account data and the feed. The fixed capacity/recovery changes passed 207 affected tests and independent review. The first full cloud replay exposed a noncanonical temporary-directory group mismatch and nine legacy fixture interface changes; the canonical strict-security replay passed 18 cases without weakening permission assertions. Final full cloud: 3,786 passed, 16 skipped; final optional-grant fixtures: 43 passed. Full Python: 19,735 passed, 12 skipped, 63 subtests passed. Full canonical web: 11,592 passed, 21 skipped; TypeScript exit 0. PR #1359 passed all 36 applicable checks and 28 app-bound required checks, received one accepted notification and was administrator-merged as 057b7d3b4. Main CI 41175064099 completed successfully on its second attempt; the first refused insufficient space before teardown. Normal exact-target image pruning recovered headroom without changing admission policy. Root committed-release verification and actual gateway/API/Next image references match 057b7d3b4; all six managed services are active and API health returns 200. Real Antigravity-first JSON, both embedding spaces and Chronos inference passed with unchanged runtime identities. Two new MarketDesk publications passed private source/media/copy checks (12 images; both scores 100); the SEB article also passed signed-in DOM headline/source/ordered-loaded-image comparison. Status identity, native requests, renewal and Mac activation remain tracked above. Obsolete uncommitted DBC public native-client artifacts were separately backed up and reclaimed after independent review, exact state/receipt, installed-unit and live-reference checks; application-writable space reached 11,336,683,520 bytes and both health routes and rollback verification stayed green. Protected wheel caches and shared model weights were retained.

# Gateway startup notification isolation (2026-10-31)

- [x] SN1 depends_on: [] Verify failed production cutover, process identity and automatic recovery without changing the broker or throttles.
- [x] SN2 depends_on: [SN1] Reproduce inherited-notification failure; isolate preparatory model containers from systemd notifications while preserving foreground supervision.
- [x] SN3 depends_on: [SN2] Run focused and full required gates; publish a focused PR, verify exact-head CI, notify once and administrator merge.
- [ ] SN4 depends_on: [SN3] Verify successful main deployment, exact gateway identity, native inference, model capabilities, renewal and a new live MarketDesk publication; retain Mac OS activation boundary.

## Review

Main release 705c4ca22 failed deploy run 41167569790. Actual disposable Ubuntu/systemd/Podman and cross-owner Git reproductions confirmed preparation MAINPID replacement and rollback metadata ownership failures. The notification/recovery combined gate passed 105 tests before the final CLI guard; the final rollback replay passed 48, including actual SIGKILL/fresh-process recovery under ordinary and restrictive umasks, malformed/null/ownership/mode/identity failures, legacy keeper capture and both capture entry-point exits. Independent review found no remaining P1/P2. Bounded operator recovery restored exact previous release 885f068ae, passed verify-restored and recovery-only journal cleanup, preserved the original floor and broker, and archived the buggy old receipt without changing its bytes. Signed-in dashboard reload and API HTTP 200 verified restored production. Full canonical web: 11,592 passed, 21 skipped; TypeScript exit 0. Initial full Python found only the two exact owner-doc omissions, now repaired. Fresh full infrastructure: 3,747 passed, 16 skipped. Final full Python: 19,735 passed, 12 skipped, 63 subtests passed. PR #1357 passed all applicable exact-head checks (36 success, eight expected skips), all 28 app-bound requirements with strict/admin protection intact, and a final PR-body replay. One accepted normal green-PR notification preceded the authorized administrator merge dbc6145f3. Main CI/deployment 41171095402 and actual live verification remain pending. Focused whole-module coverage is below the 95% target; no claim of target attainment is made.

# Unified standalone AI model gateway (2026-10-30)

- [x] MG1 depends_on: [] Inventory app/pipeline and coding/incident clients; establish native interfaces and pre-refactor full baselines.
- [x] MG2 depends_on: [MG1] Build private standalone gateway, normalized operations, capability routing, deadlines and credential lifecycle.
- [x] MG3 depends_on: [MG1] Migrate Python/Node consumers, preserving accept callbacks, specialized outputs and model-space invariants; prevent bypasses.
- [x] MG4 depends_on: [MG1] Migrate web chat/catalog/vision/Explain through the gateway without changing caller interfaces.
- [ ] MG5 depends_on: [MG1] Install isolated managed gateway, route keeper refresh through it, remove model grants from consumers, gate immutable upgrades and deployment health.
- [ ] MG6 depends_on: [MG2,MG3,MG4,MG5] Integration/full gates, PR, exact-head green, accepted Pushover, administrator merge and live production verification.

## Review

Mechanism-specific red/green regressions and independent reviews passed. Real pinned Linux and Darwin Claude/Codex/Grok clients preserve 11/10/26 native tools; final exported-helper canaries pass. Full tracked-source Python after CI repairs: 19,714 passed, 12 skipped, 63 subtests. Full cloud: 3,699 passed, 16 skipped. Full web after integration with main 885f068ae: 11,592 passed, 21 skipped; CI-contract replay 21 passed. TypeScript compiler exit 0. Gateway plus API route statement/branch coverage measured 74%; the 95% target has not been reached. Current-main integration is clean. Initial PR #1356 CI found four blockers; exact AST credential ownership, a pinned legacy helper fixture, narrow public-field scan annotations and real OpenSSH-compatible inference mounts passed their focused/full repairs. Real Ubuntu/OpenSSH validates read-only chroot mounts, reuse/rebind, dedicated UID and TCP/control denial; independent review is clean. The next CI pass exposed one legacy browser replay stub still using the vendor endpoint; its gateway migration passed the actual Playwright flow and visual screenshot, with exact two-call repair and PNG bytes retained. Full web replay: 11,592 passed, 21 skipped; compiler exit 0. The image runtime smoke then exposed package-mode startup and duplicate-engine imports; six clean-process regressions passed after entrypoint/bootstrap and relative backend repairs, and the Docker build now imports the actual entrypoint offline. The full Python run exposed a cached-session fixture leak; deterministic cold/warm job coverage passed after setup isolation, without production IV-rank changes. Final full Python: 19,715 passed, 12 skipped, 63 subtests. Final full cloud: 3,705 passed, 16 skipped. The lean Linux cloud runner then exposed an unrelated eager database dependency in pure catalog discovery. Database imports are now local to the three optional legacy read/persistence helpers; seven clean-process cases include denied native-DB imports, real discovery and engine identity, read-cache fallback and failed writes. Focused catalog/service cases: 131 passed. Full cloud: 3,706 passed, 16 skipped. Full Python: 19,715 passed, 12 skipped, 63 subtests. Actual immutable gateway image package/forecast smoke is green. The remaining pointer-fade CI test reproduced delayed native transition observation at opacity 1. The test harness now observes/deduplicates actual CSSTransition creation before asynchronous event delivery; product UI is unchanged. All original opacity/duration/curve/property, geometry, inert, keyboard and reduced-motion assertions remain. Delayed-event RED then 12 browser cases and six curation checks passed, with 10%/50%/settled screenshots inspected. Final canonical Node Vitest: 11,592 passed, 21 skipped; TypeScript exit 0. Published a8644865 passed all 38 completed applicable checks plus eight expected skips, including the real gateway image. Actual Mac root-runner admission exposed the preexisting post-deploy release-tag failure after history rewriting. Seventeen release-identity regressions failed before repair; 110 release/CI and 239 runner/documentation checks passed. Image/tag selection now validates the exact full checkout SHA and shares the existing package fallback for missing or disconnected original history. The original baseline, tag immutability, all 28 app-bound required checks, administrator enforcement and whole-main-CI/deployment updater admission remain unchanged. This does not restore monotonic semver. Full cloud repair replay: 3,706 passed, 16 skipped. Bot-grant quarantine remains an explicit separate step after real canonical/native transport proof; the installer does not automate it. Final repair full Python: 19,735 passed, 12 skipped, 63 subtests. Final canonical Node web replay using the validated CI label: 11,592 passed, 21 skipped; TypeScript exit 0. Final exact-head CI/production verification remain pending. One-time root Mac installation requires local administrator authentication; personal interactive grants remain untouched. Do not count canaries as deployed recovery or skip publication, Humanizer, quota, trading, native schema/tool or embedding-space gates.

# Antigravity-first shared model ladder (2026-10-30)

- [x] AL1 depends_on: [] Verify live provider wiring and reproduce default order / exhaustion fallback with red tests.
- [x] AL2 depends_on: [AL1] Put Antigravity first, retain remaining order and all accuracy, subscription, image and publication gates.
- [ ] AL3 depends_on: [AL2] Focused/full tests, owner documentation, exact-head CI and green notification.
- [ ] AL4 depends_on: [AL3] Administrator merge, verify deployment and live model fallback / MarketDesk cycle.

## Review

Pending. Live Grok402 is not by itself full-ladder exhaustion; preserve independent factual verification. Antigravity passed managed and isolated-grant canaries; the MarketDesk worker needs a read-only seed and disposable CLI state. Images remain attached and proceed to a capable provider.

# MarketDesk requested PDF backlog priority (2026-10-28)

- [x] MP1 depends_on: [] Freeze the requested 32 IDs; verify live retry-time ordering and current policy separately.
- [x] MP2 depends_on: [MP1] Red/green durable priority, strict cohort precedence, restart, throttle, lease and policy regressions.
- [ ] MP3 depends_on: [MP2] Focused/full gates, owner docs, exact-head CI, accepted green notification and administrator merge.
- [ ] MP4 depends_on: [MP3] Deploy, set the frozen cohort through the managed application container, verify next admitted PDF and monitor exact cohort until cleared.

## Review

Fourteen regressions failed before implementation. Final focused/replay: 111 passed; touched store coverage 95%. Full cloud: 3,533 passed / 24 skipped. Full web: 11,679 passed / 8 skipped. Full Python: 19,363 passed / 12 skipped / 63 subtests; three initial failures replayed green after the owner-doc and local dependency-layout repairs, with the untouched UW-client mock case green in isolation. Priority is durable, token/clock preserving and fail-closed on publication/current PDF policy. PR CI and production activation remain pending; unrelated primary-checkout edits remain untouched.

# Equity research newsfeed admission repair (2026-10-28)

- [x] EQ1 depends_on: [] Reproduce against recent published articles and fresh portfolio/watchlist; freeze an eligibility audit.
- [x] EQ2 depends_on: [EQ1] Red/green regressions for off-book issuer research, in-book subjects, incidental mentions, unavailable book, and preserved macro/FX admission.
- [x] EQ3 depends_on: [EQ2] Surgical admission/replay repair; focused/full gates and independent Sol/Luna code review.
- [x] EQ4 depends_on: [EQ1] Reversibly remove verified ineligible publications with originals and audit retained; verify served results and unaffected posts.
- [ ] EQ5 depends_on: [EQ3, EQ4] Exact-head green PR and accepted notification; operator merge, production verification and managed intake resumption.

## Review

Live audit frozen at 368 MarketDesk posts against the canonical watchlist and IB-verified current portfolio. Initial independent Sol and Luna review produced 114 withdrawals, 12 holds and 242 retained posts. A full rendered-caption addendum reviewed 24 of those retained posts and approved nine further holds; all 135 final mutations have exact live transaction readbacks, original row/source/ledger/outcome backups and hashed source assets. Final scope: 114 withdrawn, 21 held, 233 retained. Both reviewers independently verified every prepared and applied item; all 233 retained whole rows match the fresh pre-addendum snapshot exactly. Authenticated browser verifies every served title, body and caption against database digest cb48afbc47a1c8efb369ebd97ee02e4756e8061d169b6898e819ad359642d9ae. Temporary maintenance containers and credentials are removed; private Linux recovery remains. MarketDesk timer/service remain stopped pending release. Independent Sol and Luna code review is approved against frozen classifier/publication hashes. Final classifier/book/filter: 471 passed, classifier coverage 98.1%; intake: 154 passed. Full Python: 18,982 passed / 12 skipped / 40 subtests; cloud: 3,533 passed / 24 skipped; web: 11,666 passed / 21 skipped. Code release remains separate from completed production cleanup; operator merge, production verification and managed intake resumption remain open under EQ5.

# MarketDesk filtered, account-throttled PDF archive (2026-10-27)

Dependency graph: MD1 -> MD2 -> MD3 -> MD4 -> MD5.

- [x] MD1 depends_on: [] Freeze 206 pending / 4 downloaded rows. Trace authenticated standalone/blob flow and account refusal; user confirms account rate limit and requests slow filtered downloads, not an unverified alternate endpoint.
- [x] MD2 depends_on: [MD1] Reproduce unfiltered discovery/archive admission and fast fixed retries with failing database and worker regressions; preserve article independence and saved assets.
- [x] MD3 depends_on: [MD2] Gate PDFs on published matching-source reports plus stricter portfolio/watchlist/relevance selection; enforce 30-minute global admission and durable 6/12/24-hour account-limit backoff, with no catch-up burst or manual clock reset. Update owner documentation.
- [ ] MD4 depends_on: [MD3] Run focused and full suites sequentially, review, exact-head CI, accepted notification and admin merge.
- [ ] MD5 depends_on: [MD4] Verify production eligibility, persisted clocks and backlog receipts; distinguish archived, filtered and account-deferred items honestly.

## Review

Red/green: 20 failing regressions reproduced before repair across filtering, pacing, strict book policy, stale ownership and ambiguous company earnings. Final focused/broader verification on latest main: 580 passed, including 3 actual SIGKILL receipt/WAL/ack tests. Combined touched archive coverage 96% (policy 98%, worker/store 96%). Selection is cached in batches of 100 and invalidates with book/rules or published post edits; no model or PDF request is used for filtering. Provider failures share durable 6/12/24-hour account cooldown, with atomic acknowledgement/reset and stale-owner protection. Latest-main full local gates: Python 18,743 passed / 12 skipped / 63 subtests; cloud 3,527 passed / 24 skipped; Vitest 11,661 passed / 8 skipped. Web uses the calculated release-version build override (0.48.10); no timeout/retry gate changes. Reviewed SQL selection invalidation, account/lease CAS and atomic backoff/reset. PR CI and production verification pending. Existing account allowance cannot be restored by pacing or relabeling viewer requests; do not claim the backlog is archived from retry scheduling.

# MarketDesk all suitable reports (2026-10-27)

Dependency graph: MS1 -> MS2 -> MS3 -> MS4 -> MS5.

- [x] MS1 depends_on: [] Pin FX/off-book admission and no model relevance veto; retain publisher/source/duplicate safety with red tests.
- [x] MS2 depends_on: [MS1] Apply MarketDesk-only all-suitable admission before existing grounded copy and final Humanizer certification.
- [x] MS3 depends_on: [MS2] Replay only retired relevance reasons with bounded idempotent ledger updates; preserve unrelated holds/failures and Dropbox policy.
- [x] MS4 depends_on: [MS3] Focused 830 passed; feedback-order regression red/green 130 passed. Sol repaired-case re-review clean; Luna reviewed admission/replay. Final full suites: Python 18,626 passed / 12 skipped / 63 subtests; cloud 3,525 passed / 24 skipped; Vitest 11,595 passed / 21 skipped.
- [ ] MS5 depends_on: [MS4] Exact-head CI, accepted green-PR notification, admin merge, production deployment and bounded replay/feed receipts.

## Review

Four initial red regressions reproduced the old FX/off-book veto, missing source guard and absent replay. Affected runner: 129 passed. Broader verification: 830 passed across all MarketDesk tests and unchanged Dropbox intake/triage, Humanizer, feedback and publication contracts. Live pending attempts, failed/published rows, identity/duplicate/source holds, metadata, PDFs/outbox and operator notes are preserved. All-suitable reports enter the existing grounded copy and exact-final-copy certification; model relevance is no longer consulted. Sol identified a same-cycle upvote ordering issue; feedback now runs before automatic replay, with exact-note and repeat-cycle coverage. Final full suites and independent reviews are green; release and live replay remain MS5.

# Bun 1.4.4 security pin (2026-10-22)

Dependency graph: B1 -> B2 -> B3.

- [x] B1 depends_on: [] Red tests for the 1.4.4 image digest, workflow `bun-version` pins, and host `BUN_VERSION`.
- [x] B2 depends_on: [B1] Pin `oven/bun:1.4.4` by registry index digest, CI `bun-version`, and `deploy.sh` / `setup-vps.sh`. Frozen lockfiles unchanged.
- [ ] B3 depends_on: [B2] Draft PR, local web and Python suites, GitHub CI green. No host changes. No merge.

## Review

Pending CI.

# DeepSec dependency floors (2026-10-22)

Dependency graph: D1 -> D2 -> D3.

- [x] D1 depends_on: [] Pin Pillow 13.0.0, vite 8.3.5, vitest/@vitest/mocker 4.1.12. Braces stays 3.0.3: npm has no later release.
- [x] D2 depends_on: [D1] Pillow 13 usage check and focused research/vitest runs.
- [ ] D3 depends_on: [D2] Draft PR, exact-head CI, no merge and no host action.

## Review

Pillow 13 call sites are Image.open/new/paste/convert/split/point, ImageChops.lighter, getbbox, size, tobytes. No source change. Research pytest 72 passed with the wheel test. fastembed 0.8.0/0.9.0 cap pillow<13; the materializer rewrites only that cap on the official wheel. uv resolved pillow 13.0.0 with fastembed 0.8.0. Vitest 4.1.12: 11394 passed. The 55 local failures also fail on main vitest 4.1.2 here.

# NYSE common-stock High-Low Line (2026-10-20)

Dependency graph: H1 -> H2 -> [H3, H4, H5] -> H6 -> H7 -> H8 -> H9.

- [x] H1 depends_on: [] Verify exact common-stock-only NYSE 52-week high/low source, authorized transport, history, definition, cadence and storage/display rights. Record screenshot timeframe/baseline uncertainty and explicit calculation conventions.
- [x] H2 depends_on: [H1] Specify cumulative daily net highs, StochRSI(14), SAR parameters, weekly/daily treatment, freshness and data contract; record Python/API/panel red tests.
- [x] H3 depends_on: [H2] Implement source client, transform, history/snapshot persistence, migration and schedule with failing-first tests.
- [x] H4 depends_on: [H2] Implement read-only API, hook, contracts and shared health/schedule pins.
- [x] H5 depends_on: [H2] Implement branded Regime chart with cumulative line, SAR, synchronized StochRSI panel, honest source/baseline labels and browser coverage.
- [x] H6 depends_on: [H3, H4, H5] Integrate, independently verify formulae and actual source/Turso/API data, run focused/full gates and inspect production-build desktop/mobile screenshots.
- [x] H7 depends_on: [H6] Feature PR1222 exact-head CI green, accepted Pushover, admin merge191fd468b and automatic production deployment; installed timer, canary, Turso and four authenticated browser cases verified.
- [ ] H8 depends_on: [H7] Fix the final visual threshold-label overlap with red/green browser geometry coverage, a focused PR, green CI/admin merge and production recheck.
- [ ] H9 depends_on: [H8] Archive the four owned worktrees and record final verification.

## Review

User confirmed provider automation permission on 2026-10-21; this is their declaration, not independent inspection of the provider agreement. Exact !NEWHINYC/!NEWLONYC metadata and approved honest-HTTP capture provide 5,988 paired completed sessions (2003-01-02 through 2026-10-20), 1,243 weekly rows and 275 published fractional observations. Baseline is zero before the first source observation; screenshot anchor remains unknown. Weekly is the explicit default, with Daily available; forming-week values are provisional. No all-NYSE substitution or demo redistribution.

Recorded Python/API/panel missing-module red evidence. Author commits: ingestion20301ab23 plus287f448d8 synthetic-fixture repair (82 focused,97% combined coverage), API2064626a (421 focused), UI3a1ae54cf (195 focused,8 browser cases). Sol independently compared every daily/weekly cumulative, RSI, StochRSI and SAR/direction: no mismatches, maximum floating error2.30e-12. Sol's large-snapshot retention and forming-week natural-key findings are repaired; 2,368,759-byte prior snapshot reconstructs in20 bounded queries, maximum131,072bytes, zero duplicate periods. Luna independently passed312 focused and5 browser cases; no blocker.

Integration includes main241fea17e and the Node security update. Final full web:11,438 passed,21 skipped,86.26% lines. Full Python:17,312 passed,10 skipped,54 unchanged Mac Gateway TLS failures; the one new E2E inventory omission is fixed, with176 docs/curation and257 indicator/registration cases green. Full cloud:3,339 passed,10 skipped,12 unchanged Mac missing-tool failures; post-merge cloud/installer pins802 passed. Five unchanged TypeScript errors remain in newsfeed/sameOrigin, zero High-Low errors. Production compile and235-manifest trace audit pass;8 production-build browser cases pass across themes, phone/desktop, missing data and strict page inventory. The GitHub repository is public: checked-in fixtures are synthetic; captured provider history and independent real-history golden verification stay private. Local author history containing captures is squashed away before pushing; no captured-data commit has been published.

Actual Turso migration101 applied;5,988 daily plus1,243 weekly rows, zero duplicate weekly periods, healthy high-low-line heartbeat. Actual direct API invocation (existing test authorization seam, no DB/disk mocks) returns200/private cache and complete dataset in550ms. Current source-derived readings stay in private evidence and the operator panel. Evidence and stable screenshots: /Users/joemccann-mac-mini/.codex/state/jobs/high-low-line-20261020/. Feature PR1222 merged as191fd468b after37 applicable checks passed; production CI39658254589 passed. Installed timer and collector canary succeeded, source/Turso matched, and four authenticated real-data browser cases passed. Remaining: annotation follow-up delivery and owned-worktree archival. Primary checkout WIP preserved; no generated codemap files committed.


Final visual review found 345.625px² overlap between the oversold band label and its redundant numeric guide label. The geometry regression failed before the fix (324.140625px² overlap). Only the redundant right-edge0.2 text is suppressed; its guide line, the0.200 left-axis tick and OVERSOLD annotation remain. Full web11,438 passed/21 skipped; production build/235 trace manifests and8 browser cases passed. Independent Luna inspected both themes at393/1440px with no remaining collision. Follow-up exact-head CI/deploy pending.

# Taiwan/Korea export lead indicator (2026-10-19)

Dependency graph: E1 -> E2 -> [E3, E4, E5] -> E6 -> E7.

- [x] E1 depends_on: [] Verify official total-export sources, history, units, releases and reuse terms; capture fixtures. User authorized exports first, licensed MSCI EPS later.
- [x] E2 depends_on: [E1] Specify trailing 3-month mean of equal-weight monthly YoY export growth, six-calendar-month display shift and honest availability; record Python/API/UI red tests.
- [x] E3 depends_on: [E2] Ingestion worktree: robust source clients, Turso migration/writer, scheduled collection, watchdog/manifest pins and green tests.
- [x] E4 depends_on: [E2] API worktree: types, db-first missing/cache contracts, hook, freshness schedule and green tests.
- [x] E5 depends_on: [E2] UI worktree: Regime tab, strip, observed-versus-shifted chart, freshness rail, accessible mobile layout and green tests.
- [x] E6 depends_on: [E3, E4, E5] Integrate full suites/typecheck and real-source/Turso/browser verification; inspect screenshots and release-time semantics.
- [ ] E7 depends_on: [E6] PR exact-head green, accepted notification, admin merge, automatic production data/API/timer/browser verification and worktree cleanup.

## Review

Proposed route exports; service asia-export-lead; component AsiaExportLead; tab EXPORT LEAD; next free migration 0099 on base42c897c90. The image labels total Taiwan/Korea exports, not semiconductor-only exports. The six-month shift is a visualization of an unvalidated historical hypothesis, not a forward EPS forecast. The October15 article's no-downturn claim is historical context, never hardcoded current state. User chose official exports first with the licensed MSCI overlay unavailable. Both official sources fetched without credentials; reuse terms verified. Red: Python collection fails on missing collector; API six cases fail on missing route; panel collection fails on missing component. Evidence saved outside worktrees under asia-export-lead-20261019. Three implementation lanes merged. Independent Sol review reproduced source truncation and approved the correction before any writes; Luna passed 35 UI/API/hook cases. Final focused checks: 63 ingestion/registration, 121 API integration and 176 UI cases. Collector executable line coverage is 99.2%, client 100% (branches unmeasured). Full Vitest: 11,330 passed, 21 skipped. Full Python: 16,438 passed, 10 skipped, 54 unchanged local Gateway TLS-fixture failures. Full cloud: 3,264 passed, 10 skipped, 12 unchanged Mac Linux-tooling failures. Typecheck retains two unchanged ExplainPost content errors in DashboardNewsFeed/NewsfeedLightbox. Production compile and trace audit pass; six production-build browser cases pass, including resize navigation and readable tick spacing. Actual Turso schema 99 has 321 history rows, September composite 58.43498029405504%, healthy heartbeat; real Chrome screenshots cover both themes and phone/desktop with a timer-derived 09:40 UTC countdown. Preserve unrelated primary checkout WIP; no generated codemap files committed. Initial PR CI exposed a calendar-dependent legacy cache test and the exhaustive TesterArmy page inventory. The test now freezes session/write/prune clocks together and forces first-write pruning; Sol and Luna each passed all 26 file cases. Canonical Clear inventory, source-shaped missing fixture and SDK assertions now cover exports among 71 pages; strict comparison preserved and two focused SDK cases passed against a production build. Full repair reruns retain 11,330 Vitest passes and 16,438 Python passes with the same unrelated local baseline failures. Exact-head PR delivery and production verification follow.


# Web bun audit toolchain refresh (2026-10-20)

Dependency graph: A1 -> A2 -> A3 -> A4.

- [x] A1 depends_on: [] Record `web/` bun audit baseline and map each advisory to the lowest patched version.
- [x] A2 depends_on: [A1] Bump direct dev floors and add overrides for transitive-only fixes. No Next/React/Playwright/TypeScript major.
- [x] A3 depends_on: [A2] Regenerate `web/bun.lock`, re-audit, lint, typecheck, Vitest coverage ratchet, and the compile build.
- [ ] A4 depends_on: [A3] Draft PR, exact-head CI, no merge and no host action.

## Review

Baseline `bun audit` on `web/`: critical 2, high 33, moderate 20, low 3 (58). After floors and flat overrides: critical 0, high 1, moderate 0, low 0. The remaining high is braces GHSA-vfj7-8cjw-p6xm (`<=3.0.3`); npm latest is still 3.0.3 and GitHub has no patched release. Dev-only via Tailwind's micromatch and chokidar. Bun 1.3.14 ignores parent and version-selector overrides, so brace-expansion and picomatch were pinned inside the lock at the lowest patched line each parent already allowed. Majors that are not core frameworks: uuid 8.3.2 to 11.1.1 (exceljs, no patched 8.x), image-size 1.2.1 to 2.0.3 (pptxgenjs, no patched 1.x), postcss-selector-parser 6.1.2 to 7.1.6 (GHSA range ignores the 6.1.5 backport). No Next, React, Playwright, or TypeScript bump. Vitest moved 4.0.18 to 4.1.11 (minor). Production transitive pins required by advisories: sharp 0.35.5, baseline-browser-mapping 2.11.0, source-map-js 1.2.2, uuid, image-size.

# Same-origin mutation gate (2026-10-20)

Dependency graph: S1 -> S2 -> S3 -> S4.

- [x] S1 depends_on: [] Map requireRouteAccess, middleware, and non-browser mutating callers.
- [x] S2 depends_on: [S1] Failing same-origin unit tests.
- [x] S3 depends_on: [S2] Same-origin check, narrow exemptions, focused green.
- [ ] S4 depends_on: [S3] Draft PR, exact-head CI, one Pushover. Do not merge.

## Review

Security brief decision 8. `none` is rejected: a mutation is not a typed navigation. Focused same-origin, alerts, profile, share, route-access, and authless tests: 74 passed. Full web suite also showed 30 failures; the 403s were `http://x` fixtures now pointed at localhost. The rest (CTA calendar dates, discover cache age, sync-hook counts, demo open orders, fake-timer order idempotency) still fail with the gate disabled.

# Taiwan/Korea export lead indicator (2026-10-19)

Dependency graph: E1 -> E2 -> [E3, E4, E5] -> E6 -> E7.

- [x] E1 depends_on: [] Verify official total-export sources, history, units, releases and reuse terms; capture fixtures. User authorized exports first, licensed MSCI EPS later.
- [x] E2 depends_on: [E1] Specify trailing 3-month mean of equal-weight monthly YoY export growth, six-calendar-month display shift and honest availability; record Python/API/UI red tests.
- [x] E3 depends_on: [E2] Ingestion worktree: robust source clients, Turso migration/writer, scheduled collection, watchdog/manifest pins and green tests.
- [x] E4 depends_on: [E2] API worktree: types, db-first missing/cache contracts, hook, freshness schedule and green tests.
- [x] E5 depends_on: [E2] UI worktree: Regime tab, strip, observed-versus-shifted chart, freshness rail, accessible mobile layout and green tests.
- [x] E6 depends_on: [E3, E4, E5] Integrate full suites/typecheck and real-source/Turso/browser verification; inspect screenshots and release-time semantics.
- [ ] E7 depends_on: [E6] PR exact-head green, accepted notification, admin merge, automatic production data/API/timer/browser verification and worktree cleanup.

## Review

Proposed route exports; service asia-export-lead; component AsiaExportLead; tab EXPORT LEAD; next free migration 0099 on base42c897c90. The image labels total Taiwan/Korea exports, not semiconductor-only exports. The six-month shift is a visualization of an unvalidated historical hypothesis, not a forward EPS forecast. The October15 article's no-downturn claim is historical context, never hardcoded current state. User chose official exports first with the licensed MSCI overlay unavailable. Both official sources fetched without credentials; reuse terms verified. Red: Python collection fails on missing collector; API six cases fail on missing route; panel collection fails on missing component. Evidence saved outside worktrees under asia-export-lead-20261019. Three implementation lanes merged. Independent Sol review reproduced source truncation and approved the correction before any writes; Luna passed 35 UI/API/hook cases. Final focused checks: 63 ingestion/registration, 121 API integration and 176 UI cases. Collector executable line coverage is 99.2%, client 100% (branches unmeasured). Full Vitest: 11,330 passed, 21 skipped. Full Python: 16,438 passed, 10 skipped, 54 unchanged local Gateway TLS-fixture failures. Full cloud: 3,264 passed, 10 skipped, 12 unchanged Mac Linux-tooling failures. Typecheck retains two unchanged ExplainPost content errors in DashboardNewsFeed/NewsfeedLightbox. Production compile and trace audit pass; six production-build browser cases pass, including resize navigation and readable tick spacing. Actual Turso schema 99 has 321 history rows, September composite 58.43498029405504%, healthy heartbeat; real Chrome screenshots cover both themes and phone/desktop with a timer-derived 09:40 UTC countdown. Preserve unrelated primary checkout WIP; no generated codemap files committed. Initial PR CI exposed a calendar-dependent legacy cache test and the exhaustive TesterArmy page inventory. The test now freezes session/write/prune clocks together and forces first-write pruning; Sol and Luna each passed all 26 file cases. Canonical Clear inventory, source-shaped missing fixture and SDK assertions now cover exports among 71 pages; strict comparison preserved and two focused SDK cases passed against a production build. Full repair reruns retain 11,330 Vitest passes and 16,438 Python passes with the same unrelated local baseline failures. Exact-head PR delivery and production verification follow.

# PDF Humanizer resource bounds (2026-10-19)

Dependency graph: B1 -> B2 -> B3 -> B4.

- [x] B1 depends_on: [] Verify exact-quote expansion and carried-findings amplification with safe allocation measurements.
- [x] B2 depends_on: [B1] Add failing memory/budget regressions, deduplicate before expansion and enforce an aggregate finding budget with private fail-closed audits.
- [x] B3 depends_on: [B2] Obtain independent Sol/Luna approval and focused/full verification.
- [ ] B4 depends_on: [B3] Ship follow-up PR with exact-head green, one notification, admin merge and automatic production verification.

## Review

Red/green budget regressions passed: 85 core cases and 274/274 executable lines via stdlib trace (branches unmeasured). Sol independently approved 114 focused cases; Luna approved 355. Sol measured the same repeated-descriptor input at 1.41 MB versus 27.22 MB and 2,000 versus 64,000 spans; retained-term output fell from 40,000 dictionaries to 200. Budget exhaustion holds a full private draft with no partial score. Source facts and supplied scoring weights remain unchanged. Main CI 39360738644 also exposed a packed-Git-object assumption in two cloud tests. Red reproduced 2 failures; repaired file passes 75 cases and independently passes Luna's 8 root-store cases, requiring the exact committed-blob refusal. Final research/root: 1,623 passed, 23 subtests. Full Python: 16,380 passed, 10 skipped, the same 54 unrelated local TLS fixture failures. Full cloud: 3,258 passed, 10 skipped, the same 12 unrelated local Linux-tooling failures. Exact-head follow-up delivery and production verification are pending.

# PDF summary Humanizer gate (2026-10-19)

Dependency graph: H1 -> H2; H3 depends_on: [H2]; H4 depends_on: [H3].

- [x] H1 depends_on: [] Trace both PDF pipelines, fallback summaries, durable publication and the saved rubric.
- [x] H2 depends_on: [H1] Add failing tests, shared honest scoring, bounded fact-preserving rewrites and final publication admission.
- [x] H3 depends_on: [H2] Verify with independent Sol and Luna agents; run focused and full suites and document audit/retry behavior.
- [ ] H4 depends_on: [H3] Publish PR, confirm exact-head CI green, notify once, admin-merge and verify production delivery.

## Review

Sol approved with 99 focused tests; Luna approved with 337. Core: 74 passing cases and 218/218 executable lines via stdlib trace (branches unmeasured). Live configured model smoke: 85 -> rewrite -> factual verification -> 100, four calls, no feed publication. Full Vitest: 11,279 passed, 21 skipped. Full Python: 16,365 passed, 10 skipped; 54 pre-existing local TLS fixture failures in unchanged Gateway modules. Final research/root: 1,609 passed, 23 subtests; cloud: 3,256 passed, 10 skipped with 12 local Linux-tooling failures in unchanged modules. Exact-head delivery pending. Summary title/body are the scored surface; source captions, figures, hashes and provenance remain immutable. The 19 supplemental patterns are unweighted blockers. Provider outages retain durable parking; the primary checkout remains untouched.

# Dedicated responder SSH pickup safety (2026-10-18)

Dependency graph: GP1 -> GP2 -> GP3 -> GP4.

- [x] GP1 depends_on: [] Confirm the SSH upload-pack safety mismatch and isolate the change from production preparation and the dirty operator checkout.
- [x] GP2 depends_on: [GP1] Reproduce the actual Git ownership safety refusal with a temporary committed repository and isolated pickup configuration in cloud CI.
- [x] GP3 depends_on: [GP2] Register only the dedicated repository root and its exact `.git` path as the pickup account, document fresh SSH verification, and obtain focused cloud green.
- [ ] GP4 depends_on: [GP3] Complete independent review, publish the focused PR, watch exact-head CI, merge and verify normal deployment before retrying supported account preparation.

## Review

Cloud red reproduced the exact `.git` ownership refusal after production preparation: 1 failed and 54 passed. With the unchanged regression, focused cloud green passed all 55 account contracts with 0 failures and 0 skips. The fix registers only the root and `.git` paths as the pickup account. Runtime/test AST parsing and whitespace checks passed. Sol approved the source; Luna final review, full public CI and deployment are pending. No local suites or production changes. Native login, pickup handover and timers remain separate operator steps; no wildcard Git trust is permitted.

# Nightly phase status line (2026-10-18)

Dependency graph: T1 -> T2 -> T3; T4 and T5 depend_on: [T2]; T6 depends_on: [T3, T4, T5].

- [x] T1 depends_on: [] Map runner loops, exit codes, and dead-man posting.
- [x] T2 depends_on: [T1] Harness emits the radon-phase line from the process exit, including kill and missing hook status.
- [x] T3 depends_on: [T2] Security and DeepSec public audit comments are that line only.
- [x] T4 depends_on: [T2] Formatter, parser, and failure-path tests green.
- [x] T5 depends_on: [T2] Audit-context reader skips a non-zero machine line and keeps the legacy fallback.
- [ ] T6 depends_on: [T3, T4, T5] docs/runner.md, PR, CI green, ready.

## Review

Focused pytest: phase status, audit context, issue format, PR output, reporting doc, security hooks, run_loop, install trust: 191 + 243 passed. The runner is in this repo. joemccann/radon-factory was not found.

# Newsfeed Explain button (2026-10-18)

Dependency graph: T1 -> T2; T3 parallel to T2; T4 depends_on: [T2, T3].

- [x] T1 depends_on: [] Spec the Explain button, cache table, prompt, auth pin and UI states.
- [ ] T2 depends_on: [T1] Backend: route, LLM, image fetch, Turso cache, writer, failing-then-green tests.
- [ ] T3 depends_on: [T1] UI: Explain control, panel/sheet, desktop and mobile CSS, component tests.
- [ ] T4 depends_on: [T2, T3] Merge, Playwright plates, CI green, operator PR.

## Review

Pending swarm merge.

# X share-copy footer (2026-10-18)

Dependency graph: T1 -> T2 -> T3 -> T4.

- [x] T1 depends_on: [] Inventory every X copy builder, generated report preview and outbound clipboard/composer path.
- [x] T2 depends_on: [T1] Append the exact standalone footer once across P&L, newsfeed and all report builders, including verified CTA LLM copy.
- [x] T3 depends_on: [T2] Update existing copy assertions and verify focused/full suites plus actual browser copy/composer behavior.
- [ ] T4 depends_on: [T3] Publish a focused PR, watch all exact-head checks, notify once, admin-merge and verify normal production delivery.

## Review

Footer: @grok explain this for normies. Preserve existing copy, sanitization, cashtags, financial values and media rendering. No external post is submitted. The primary checkout has unrelated task/output changes and remains untouched. Verification: focused Vitest 207 passed; affected Python 110 passed; full Python 16,329 passed, 2 skipped and 23 subtests passed; full cloud 2,921 passed and 7 skipped; full Vitest 1,120 files and 10,996 tests passed, 21 skipped; Playwright 5 passed with mobile/desktop screenshots inspected. All four report builders and previews, CTA verified generated copy and fallback, and formatting idempotence passed smoke checks. Temporary local dependency-root overrides were restored; no runtime configuration change is included. React review found no new state, effects, fetching or dependency concerns. PR delivery pending.

# PR 1132 refresh and completion (2026-10-17)

Dependency graph: T1 -> T2 -> T3 -> T4.

- [x] T1 depends_on: [] Inspect exact PR head, review incoming security changes and isolate from the dirty operator checkout.
- [x] T2 depends_on: [T1] Merge current green main, preserve the native-auth fix, and repair verified readiness blockers with red/green regressions.
- [ ] T3 depends_on: [T2] Run focused/full verification and independent security review; update the existing PR and watch all exact-head checks.
- [ ] T4 depends_on: [T3] Confirm the single PR-green notification, admin-merge the exact green head, and verify normal production delivery and installed control planes.

## Review

Existing PR head 5e6dde78 was green but behind main. Integrated production-green c7471050, including the native Antigravity fix and research queue repair; task-ledger conflicts retained latest main plus this plan. Red/green fixes cover compatible durable private-release rejection feedback and complete topology persistence before teardown. Focused results: 203 controller tests, 60 gateway tests and seven final topology tests, including an attack hook at the actual exclusive-open boundary. Sol approved privileged/controller changes and Luna approved web and topology behavior. Refreshed full Python: 16,267 passed, two skipped and 23 subtests passed; full cloud: 2,921 passed, seven skipped; full Vitest: 1,120 files and 10,996 tests passed, 21 skipped. Exact-head delivery pending. Preserve unrelated user files and the existing security residuals; no CI or privilege gate is relaxed.

# Retire Foreman (2026-10-17)

Dependency graph: T1 -> T2 -> T3 -> T4.

- [x] T1 depends_on: [] Confirm owner retirement and inventory the exact Vercel project and repository references.
- [x] T2 depends_on: [T1] Remove retired intake/setup/contracts, active documentation and obsolete knowledge example; record the retirement and owner correction.
- [x] T3 depends_on: [T2] Run documentation/knowledge contracts, full Python/cloud verification and independent review.
- [ ] T4 depends_on: [T3] Publish a focused PR, watch exact-head CI, notify once, admin-merge, verify normal deployment and resolve the audit owner-input item.

## Review

Owner confirmed Foreman is retired. Scope is the identified radon-factory Vercel project and its Radon integration. Preserve unrelated repositories, shared CI/security rules and historical evidence. Focused docs/knowledge contracts: 194 passed. Full Python: 16,149 passed, 2 skipped, 23 subtests; 55 failed, including the docs owner-map contract fixed in this diff and TLS tests that reproduce on clean base 20d0843d as `CERTIFICATE_VERIFY_FAILED: Missing Authority Key Identifier`. Independent review found no blocker or live integration effect. Previous main run 39069344344 completed successfully at 20d0843d. Delivery and exact-head verification pending.

# Research database idle socket expiry (2026-10-17)

Dependency graph: T1 -> T2 -> T3 -> T4 -> T5.

- [x] T1 depends_on: [] Inspect both pooled transports, production idle-socket evidence and existing held-TTL contract.
- [x] T2 depends_on: [T1] Commit fake-clock transport regressions and demonstrate GitHub red for idle transport and held-clock transitions before source changes; no local suites.
- [x] T3 depends_on: [T2] Retire sockets idle for five seconds before sending any request, preserving conservative read replay and never replaying uncertain writes.
- [ ] T4 depends_on: [T3] Independent review and exact-head full GitHub CI green; record notification and deployment evidence.
- [ ] T5 depends_on: [T4] Verify canonical research mirrors and normal document outcomes without requeuing completed work.

## Review
Production read-only canary reproduced a stale socket after a 70-second idle; a subsequent fresh SELECT succeeded. Both transport pools currently retain sockets indefinitely. Existing held-row upserts preserve an expired row's old clock and expiry stamp. Approved transition-only plan has real SQLite regressions: fresh decisions clear expiry, re-entry to held starts a new clock, and repeated active-held writes retain their original clock. Regression-only f8daf7f4, CI 39052361901: pool job 117453393631 had 20 failed/704 passed; TTL job 117453393436 had 4 failed/2739 passed. Both pools now retire connections before sending at five idle seconds, reset clocks on close/connect and stamp after full HTTP success. Outcome upserts reset clocks only when entering a new held period and clear stale expiry stamps. Existing owner documentation updated. Python AST and diff checks passed; no local suites. Exact-head full CI and independent review pending.

# Research review provider timeout budget (2026-10-17)

Dependency graph: T1 -> T2 -> T3 -> T4 -> T5.

- [x] T1 depends_on: [] Inspect Reviewer, shared ladder timeout diagnostics and both pipeline document boundaries; preserve subscription, provider order and prepaid policy.
- [x] T2 depends_on: [T1] Commit synthetic regression-only coverage and demonstrate exact-head GitHub CI failures before source changes; no local suites.
- [x] T3 depends_on: [T2] Skip only proven timed-out provider attempts within the same document and request modality; reset at each document boundary.
- [ ] T4 depends_on: [T3] Independent Sol/Luna review and exact-head full GitHub CI green.
- [ ] T5 depends_on: [T4] Coordinated deployment after the preceding production release; verify normal recovered-document review outcomes.

## Streamed transport follow-up
- [x] T6 depends_on: [T3] Add production-shaped stream-body timeout and terminal response-error regression coverage after independent Sol review.
- [x] T7 depends_on: [T6] Confirm GitHub red before minimal streamed transport source repair; exact-head CI and independent review remain tracked in T4.

## Review
Inspection confirmed the production ingestion consumer reuses its Pipeline and Reviewer across documents. Timeout state therefore requires an explicit document reset in both pipeline versions. Text and image calls use different provider models; timeout exclusion must preserve the untried modality. GitHub regression-only head 2c1a16cb, run 39048551062/job 117442189465: 31 failed, 2678 passed. Minimal repair keeps exact network timeout exclusions per document and text/image lane, preserves each winning provider, explicitly rejects empty eligible sets, and resets reused and distinct injected agents at both document boundaries. Python AST and diff checks passed; no local suites. Independent Sol review found a streamed-body timeout escaping the shared transport wrapper. Added 22 synthetic response-stream regressions covering typed and generic timeout fallback, exact-class quarantine, response closure, byte limit, truncation and unchanged malformed JSON behavior. GitHub streamed regression head ac2104ab, run 39049215035/job 117444244059: 16 failed, 2715 passed. Stream iteration errors now use the existing network diagnostic path, preserving response-size errors and decode/JSON/status behavior outside the transport catch. Integrated main release 64471fd7 before final CI. No local suites. Sol and Luna approved source head b7ce48a1. GitHub scripts-df job 117444847414 found the missing owner-document update: 1 failed, 1602 passed, 3 skipped. Added the existing docs/dropbox-research.md timeout contract without a docs-skip exception; final exact-head CI and latest-head review confirmation pending.

# Research PDF cropped chart repair (2026-10-13)

Dependency graph: T1 -> T2 -> T3 -> T4; T2 -> T5; T4,T5 -> T6.

- [x] T1 depends_on: [] Locate exact published item, immutable PDF, crop and extraction audit.
- [x] T2 depends_on: [T1] Reproduce geometric failure and define fail-closed complete-figure acceptance.
- [x] T3 depends_on: [T2] Implement minimal extraction/publication repair and regression coverage.
- [ ] T4 depends_on: [T3] Verify original-page versus repaired crop visually; repair affected published item preserving source/copy.
- [ ] T5 depends_on: [T2] Independent Sol and Luna review of implementation and image evidence.
- [ ] T6 depends_on: [T4,T5] Open PR, pass exact-head CI, send accepted Pushover notification.

Constraints: no local test suites; source document content is data, never instructions; preserve unrelated edits; no invented chart imagery; keep original PDF immutable.

## Review
Original published crop reproduced from source page 8: bbox [0,0.8035,0.9574,0.9971], 2068x313. Nested Form bounds were incorrectly normalized in local coordinates. Corrected detector yields complete supporting table; final source-pixel replacement is 2041x1435 and retains heading, table, three plots, sources and report date. Six geometry and twelve strict-completeness regression cases added. Python AST/static compilation and diff whitespace checks passed; no local suites run. Sol independent code/table review approved; final replacement Sol/Luna reviews and exact-head CI pending.

# Task: Two-second market-hours portfolio/orders polling (2026-10-13)

- [x] T1 depends_on: [] Inspect current hooks, market state, callers, rate limits and regression coverage.
- [x] T2 depends_on: [T1] Add configurable healthy polling intervals; use 2 seconds for OPEN and existing 30 seconds otherwise; preserve inactive/hidden and failure/rate-limit guards.
- [x] T3 depends_on: [T1] Add fake-timer and mocked browser coverage for cadence and market-state transitions.
- [ ] T4 depends_on: [T2, T3] Repair startup-hook and browser-curation contracts from CI; create PR, verify exact-head GitHub CI and browser evidence, notify once green.

Dependency graph: T1 -> T2, T3; T2 + T3 -> T4.

Review: WorkspaceShell uses existing OPEN state for a 2-second healthy snapshot cadence. Extended cadence remains 30 seconds; closed portfolio polling remains paused and orders page remains 30 seconds. Deduplication, pending-sync10 seconds and original failure/rate-limit floors retained. Added 22 fake-timer hook regressions and 4 mocked browser cases with screenshots. No local suites. CI38426522529 passed all three browser shards and the new hook cases, and identified startup-spy options and held-out browser curation expectations; align the spy and curate the modified existing spec. Final exact-head CI pending.

# Task: IR PR titles (2026-10-09)

- [x] T1 depends_on: [] Failing tests for page eeef0959 title dump.
- [x] T2 depends_on: [T1] Condense titles in format_ir_pr_title and rewrite the title inside ensure_pr.
- [x] T3 depends_on: [] Human PR template with Operator Next Steps.
- [ ] T4 depends_on: [T2, T3] PR, CI green, one Pushover. No merge.

Dependency graph: T1 -> T2; T3 parallel; T4 last.

# Task: SLM QLoRA lock isolation (2026-10-07)

- [x] T1 depends_on: [] Resolve LLaMA-Factory 0.9.5 ranges with uv. Isolate from the fleet requirements.
- [ ] T2 depends_on: [T1] Push to PR 945. CI green. No merge.

Review: uv 0.12.9 resolved llamafactory==0.9.5, transformers==5.6.0, torch==2.14.1, peft==0.18.1, bitsandbytes==0.50.2, accelerate==1.11.0, datasets==4.0.0, trl==0.24.0, tokenizers==0.22.2. Fleet stays tokenizers==0.23.1. Tests 18 + 64 + 43 passed.

# Task: Neutral Implied MV formatting (2026-10-07)

- [x] T1 depends_on: [] Inspect PositionTable, scoped instructions, existing regression and CI configuration.
- [x] T2 depends_on: [T1] Add regressions and format position/expanded-leg Implied MV like Initial Value; retain negative signs and unavailable placeholders.
- [x] T3 depends_on: [T1] Add Playwright coverage with computed neutral color and screenshot evidence; independent review.
- [ ] T4 depends_on: [T2, T3] Repair legacy rate-resolution and browser readiness expectations from CI; resolve newer-main task-log conflicts; create PR and verify all applicable GitHub CI checks at exact head; no local suites.
- [ ] T5 depends_on: [T4] Record review evidence and send accepted normal-priority Pushover notification.

Dependency graph: T1 -> T2, T3; T2 + T3 -> T4 -> T5.

Review: GitHub run 37621162918 / head 10ba235d reproduced the positive-plus and directional-color regressions. Existing signed currency helper preserves negative liabilities; row classes match Initial Value. Independent review passed. Dark/light browser cases and screenshot uploads are registered in CI; final exact-head results pending. CI 37621600997 passed new component regressions and identified a legacy rate-resolution expectation of positive plus signs; update only its expected currency formatting. Browser run 37622020570 passed 240 existing cases and the new light-theme case on retry; add an explicit visible-header wait before reading the initial-value column index. No local suites.

# Task: Nightly CI performance audit (2026-10-06)

- [x] T1 depends_on: [] Read open PRs/checkpoint, audit main changes, acquire Actions jobs/logs/cache/rules.
- [x] T2 depends_on: [T1] Classify comparable runs, reconstruct gate DAG, rank safe material candidates.
- [x] T3 depends_on: [T2] Surgical CIP-018 revert after its recorded >90s shard-wall trigger; 1 failed / 37 passed red, 159 passed green; cloud 58 passed.
- [ ] T4 depends_on: [T3] Publish substantive draft PR only, verify exact-head CI, post one rolling issue comment.

Review: No additional material optimization qualifies. CIP-018 organic run 37357797556 / job 111924636029: scripts-gh 93s wall exceeds its recorded 90s revert trigger. Restore only its shard membership and matching ownership contracts; preserve full inventory, coverage, gate and deployment guarantees.

## PR #934 conflict repair (2026-10-07)
- [x] T1 Inspect conflicts and preserve both branches' task and curation records. depends_on: []
- [x] T2 Merge current main, resolve conflicts, and statically verify the merged files. depends_on: [T1]
- [ ] T3 Push the repaired PR head and wait for all applicable GitHub checks to pass. depends_on: [T2]
Dependency graph: T1 -> T2 -> T3.

### Review
- PR https://github.com/joemccann/radon/pull/934; original head eac19327a60cf0ec710c59dc8345980591f16aec.
- Preserved both task logs and ledger receipt sections; 156 unique hold-outs + 48 curated specs = 204 classified, with no missing/stale/duplicate entries or overlap. Server AST and both documentation additions verified. No local suites. Prior PR green notification already accepted; do not duplicate it.


## PR #938 conflict repair (2026-10-07)
- [x] T1 Inspect conflicts and preserve both branches' audit, task, and training-owner documentation. depends_on: []
- [x] T2 Merge main, resolve conflicts, and statically verify record and training-contract preservation. depends_on: [T1]
- [ ] T3 Push the repaired head, wait for all applicable GitHub CI checks, and record green notification evidence. depends_on: [T2]
Dependency graph: T1 -> T2 -> T3.

### Review
- PR https://github.com/joemccann/radon/pull/938; original head 6334926dae407855aaa50dbdbb8878ba8518d4dc.
- Both audit/log branches preserved verbatim; task records retained. Training documentation preserves discovery isolation, exact environment allowlist, pinned model revision, and human-review gates. Merged training shell/Python syntax and resolved-file whitespace checks passed. No local suites/builds; GitHub runners own verification.
## PR #931 conflict repair (2026-10-07)
- [x] T1 Inspect conflicts and preserve both branches' task and lesson records. depends_on: []
- [x] T2 Merge current main, resolve conflicts, and statically verify record preservation. depends_on: [T1]
- [ ] T3 Push the repaired head and wait for all applicable exact-head GitHub checks. depends_on: [T2]
Dependency graph: T1 -> T2 -> T3.

### Review
- PR https://github.com/joemccann/radon/pull/931; original head d295ae937afe48bfeda792fb3a8355f74ff980df.
- Both task branches and all 835/839 nonblank lesson records preserved; conflict markers removed and diff whitespace check passed. No local suites. Prior PR green notification already accepted; do not duplicate it.


# Task: PDF extract error detail (2026-10-03)

- [x] T1 depends_on: [] Failing tests: rc + MemoryError, negative rc signal name, secret redaction, truncated stderr, prefix preserved, TimeoutExpired, persisted health/journal bound.
- [x] T2 depends_on: [T1] Pipeline.extract appends rc/signal + redacted stderr tail; child prints type: message; classify_error length bound.
- [ ] T3 depends_on: [T2] Focused pytest green. Draft PR vs main, CI green. No merge. No VPS.

# Task: Rubner always-publish (2026-10-03)

- [x] T1 depends_on: [] Red tests: Rubner detection variants, triage/novelty/TTL exemptions, empty-SELECT and VERIFY-fail fallback, idempotent post ids, outcome flags.
- [x] T2 depends_on: [T1] Desk in force_include.py, intake page-text plumbing, expire SELECT+skip, ALWAYS_PUBLISH_FALLBACK labels, docs.
- [x] T3 depends_on: [T2] EvidenceError message persist (separate commit) if the research-path classifier drops the extract reason.
- [ ] T4 depends_on: [T2, T3] Draft PR vs main, CI green. No merge. No VPS.

Review: Citadel Rubner GMI always reaches the feed. VERIFY stays hard per candidate. Zero published posts after VERIFY yields one grounded summary, never an unverified figure. Held TTL skips always_publish rows. EvidenceError on the host is classified with its message; do not mutate the VPS from this branch.

## DS-2026-10-05-05 trusted runtime isolation
- [x] T1 Inspect immutable-image service dependencies and runtime contracts. depends_on: []
- [x] T2 Containerize four secret-store jobs through root-owned runtime and protected credential staging. depends_on: [T1]
- [x] T3 Add regression coverage for isolation, unit wiring, commands, mounts, and cleanup. depends_on: [T1, T2]
- [x] T4 Document design and create PR. depends_on: [T2, T3]
- [ ] T5 Verify all exact-head GitHub checks and send the accepted green notification. depends_on: [T4]
Dependency graph: T1 -> T2 -> T3 -> T4 -> T5; T1 -> T3; T2 -> T4.

### Review
- PR: https://github.com/joemccann/radon/pull/943, branch codex/secrets-trusted-runtime.
- Four services and their timers are paired control-plane artifacts; immutable-image commands preserve existing cadence, timeout/exit behavior and persistent state.
- Subscription probes use locked vendor binaries and auth-only temporary homes, preserving token rotation without importing host hooks or configuration.
- Root directory preparation pins ancestor descriptors; the root subscription dispatcher does not re-own shared StateDirectory state.
- Runtime and image regression coverage added; static shell syntax, Python parsing and diff whitespace checks passed. No local suites or builds run.
- CI repair commits update deployment hashes, metadata cardinality, documentation contracts, auto-sync membership and health-writer discovery.
- Final static review found no additional actionable issues. Merged main while preserving both task records. Exact PR head 50c8dc0fcc4e88460abe412f103fa9a38add2659: 32 checks passed, 7 non-applicable skipped, 0 pending/failing; both native image builds passed.
- One normal-priority Pushover notification titled radon PR green was accepted (status=1). The PR notification has been delivered once; do not send a duplicate. A newer-main integration now requires exact-head CI again.

### Rebase onto #932 (2026-10-07)
- [x] T6 Merge origin/main (#932, 6198bd51); keep #932's drop-ins, broker, staging, manifest and tests. depends_on: [T5]
- [x] T7 Keep only additive hardening: pinned image CLIs, no host ~/.local/bin in radon-api, ancestor-pinned private dirs, fail-closed group check, extra runtime tests. depends_on: [T6]
- [ ] T8 Exact-head CI green. depends_on: [T7]
- Dropped as duplicate or superseded by #932: base-unit User=root rewrites, base unit/timer control-plane and auto-sync moves, containerized subscription-tokens and its auth-only CLI homes, duplicate runtime cases and command/parity tests.

# Task: DeepSec operator-only re-verify (2026-10-01)

- [x] T1 depends_on: [] Failing tests: prompt requires re-verify + open/closed/unverifiable; closed items stay out of Next; DS-2026-09-20-03 closed with #689 / e5c4e627 / setup-vps.sh L39-47.
- [x] T2 depends_on: [T1] Helper + committed closed ledger + prompt/docs. last-audited.json keeps closed_queue. No VPS.
- [ ] T3 depends_on: [T2] Focused pytest green. READY PR vs main, CI green. No merge.

# Task: Runner Pushover dotenv allowlist (2026-10-01)

- [x] T1 depends_on: [] Failing tests in scripts/tests/test_runner_pushover_env.py: dotenv pair, allowlist, no eval, values never logged, missing, half-pair, agents never see keys, dotenv-path file, plist/installer.
- [x] T2 depends_on: [T1] run_loop.sh allowlist parser + precedence; install.sh writes dotenv-path; notify logs curl status; docs/runner.md.
- [ ] T3 depends_on: [T2] Focused pytest green, READY PR vs main, CI green. No merge.

# Task: Grok setup hardening (2026-09-30)

- [x] T1 depends_on: [] Failing tests: promote touches only explicit paths (never Path.home()/.grok), candidate must live under scratch, real ~/.grok and ~/.local/bin untouched by any scripts test.
- [x] T2 depends_on: [T1] grok_upgrade explicit `--alias-bin`; unit passes it; hermetic HOME in upgrade tests.
- [x] T3 depends_on: [] Failing test: responder env rebuild keeps GROK_PAGE_* operator flags, drops old secrets; extract env builder.
- [ ] T4 depends_on: [T2, T3] Focused pytest + cloud grok tests green, docs, draft PR vs main, CI green. No merge, no VPS.

Review: promote_live_symlink relinked Path.home()/.grok/bin/grok whenever it existed, so the upgrade test run in the responder clone repointed the real entry at a pytest candidate. Upgrader now moves only --live-bin and an explicit --alias-bin under a --scratch containment check; tests fake HOME; conftest fails any test that touches the real grok entries. Setup step [2/5] keeps GROK_PAGE_* operator flags, secrets only from production.

# Task: RSI OVERSOLD breadth (2026-09-30)

- [ ] T1 depends_on: [] Failing tests: Wilder RSI, strict 30/10 boundaries, gates, migration 0092, writer, panel/API/lockstep.
- [ ] T2 depends_on: [T1] Mirror MA RATIO: scan, history table, API, `/regime/rsi-oversold`, 23:05 UTC timer.
- [ ] T3 depends_on: [T2] Focused suites green. Draft PR vs main, CI green, ready when green. No merge. Turso read-only.

# Task: HELD_EXPIRED honest PDF cut + hold timestamps (2026-09-29)

- [ ] T1 depends_on: [] Failing tests: cut-report buckets/labels (no "retries exhausted"), Sep 28 fixture counts, held_at on hold, expire sets expired_at and leaves updated_at, TTL uses held_at, migration 0091 backfill.
- [ ] T2 depends_on: [T1] cut_report.py + workflow_dispatch, publish.py sweep/record_outcome, migration 0091, docs/dropbox-research.md.
- [ ] T3 depends_on: [T2] Focused pytest green. Ready PR vs main. No merge.

# Task: IR PR descriptions + Grok pin (2026-09-28)

- [x] T1 depends_on: [] Validator + pickup/autopush refuse placeholders; tests on title/body.
- [x] T2 depends_on: [T1] Track-latest resolver, LKG state, responder `-m`/fallback/lock.
- [x] T3 depends_on: [T2] Daily enabled upgrade timer, smoke/promote/stay-fail, runbook.
- [ ] T4 depends_on: [T3] Draft PR vs main, CI green, no merge.

# Task: Knowledge golden eval v2 (2026-09-27)

- [x] T1 depends_on: [] Opt-in retrieve.py legs/recency/cap hooks; defaults identical.
- [x] T2 depends_on: [T1] eval_golden.py modes, graded metrics, baseline gate, backend_used.
- [x] T3 depends_on: [T2] golden_set.json v2 (>=100, draft, version) + candidates + baseline placeholder.
- [x] T4 depends_on: [T3] Offline tests, systemd units (not enabled), docs.
- [ ] T5 depends_on: [T4] Draft PR vs main, CI green, no merge.

# Task: grok-fix-pickup self-refresh (2026-09-27)

- [x] T1 depends_on: [] Failing tests: plist bash -c refresh before python exec, git -C not .gitdirs, fix/* survive, bash -n, plistlib.
- [x] T2 depends_on: [T1] Plist + docs install snippet + contract pins.
- [ ] T3 depends_on: [T2] Focused pytest green, non-draft PR, CI green. No merge.

# Task: DS-2026-09-25-06 Caddy media group (2026-09-26)

- [x] T1 depends_on: [] Failing tests: caddy not in radon, radon-media grant, env 0640.
- [x] T2 depends_on: [T1] setup-vps + deploy-root-helper migrate group/ACLs; wipe groupdel.
- [ ] T3 depends_on: [T2] Focused pytest, non-draft PR, CI green. No merge, no deploy.

# Task: Bound knowledge vector writes (2026-09-26)

- [x] T1 depends_on: [] Failing tests: oversized doc ingests, size error splits, one vector send, backfill byte bound, source errors.
- [x] T2 depends_on: [T1] Send each vector once as f32 blob; byte-budget batches; halve on Hrana size errors; byte-bound _load_existing.
- [ ] T3 depends_on: [T2] Focused pytest green, draft PR, CI green.

Review: vector JSON was bound twice (104,614 B/chunk). f32 blobs once are 13,032 B. 366 chunks of 4,000 chars fit under the 7 MiB write budget. Text+FTS+prune stay one transaction; vectors follow only when that payload does not fit.

# Task: Native security-audit repo root (2026-09-26)

- [x] T1 depends_on: [] Red tests: resolve order (RADON_REPO_ROOT, git toplevel, cwd), reject a non-radon tree, planted `/Users/` guard, wrappers export the env.
- [x] T2 depends_on: [T1] Resolver + both security-audit workflows + export from security nightly wrappers.
- [ ] T3 depends_on: [T2] Focused pytest green, non-draft PR, CI green, no merge.

# Task: Nemotron parse + embedding_v2 (2026-09-26)

- [x] T1 depends_on: [] Parser ladder, evidence shape, compare_parsers, embedding_v2 migration/backfill/retrieval fallback.
- [x] T2 depends_on: [T1] Focused pytest for parse fallback and 2048-d embed path.
- [x] T4 depends_on: [T2] Default backend nvidia, dual-write on, 384 until v2 coverage is complete.
- [ ] T3 depends_on: [T5] Draft PR, CI green, no merge.
- [x] T5 depends_on: [T4] Per-page v1 parse: local grounding, selective pages, circuit breaker, image-derived numbers.

# Task: Knowledge atomic receipt budget (2026-09-25)

- [x] T1: Diagnose normal-cycle receipt exhaustion; depends_on: [].
- [x] T2: Regression-first finite atomic receipt budget and safe document diagnostics; depends_on: [T1].
- [ ] T3: Exact-head GitHub CI, notification, coordinated deployment and real canonical cycle; depends_on: [T2].

Red evidence: GitHub CI36194951875, head caa7e96d: 3 expected failures / 1,436 passed / 7 skipped. Independent read-only review found no blockers.

Evidence: default cycle 21:59:25–22:03:04 failed after four 4-second atomic receipt timeouts. Atomic documents now combine hundreds of statements under the old per-statement allowance. Reads and cleanup retain 4 seconds; atomic receipts receive a distinct finite 30-second socket/elapsed allowance, with existing server-queued cleanup and whole-document retry intact. No local suites.

## 2026-09-25 Bound optional knowledge enrichment
- [x] T1 Static audit confirms missing summaries retry every keyed provider without a run budget. depends_on: []
- [x] T2 Regression-first GitHub CI for cumulative budget, exhaustion breaker, raw/vector preservation, and child cleanup. depends_on: [T1]
- [x] T3 Implement a knowledge-only 120-second cumulative subprocess enrichment budget shared across sources/retries; incremental four-chunk waves and explicit failed/deferred counts. Preserve normal summary backfill and raw/embedding ingestion. depends_on: [T2]
- [ ] T4 All applicable exact-head CI green, one notification, parent-coordinated deployment after catch-up lock release. depends_on: [T3]
Dependency graph: T1 -> T2 -> T3 -> T4.

Review: GitHub red CI36191911953/job108258835340 at83ffbaa9: six expected failures,1426passed,7skipped. Bounded cleanup adds at most two5s drain/reap allowances per timed-out worker. No local suites or provider calls. Subprocess process groups enforce the wall deadline and reap provider CLI descendants; thread cancellation alone cannot. Scope: knowledge ingest/distill and their tests. Stored summaries/vectors survive deferred enrichment of unchanged input.

# Task: Research force-include + 24h Held TTL

Joe via CoS: always include BofA Flow Show and DB positioning data; expire Held rows after 24h on `updated_at` PT.

## Dependency graph

- T1 depends_on: [] - Red tests: force_include predicates, identify series fixtures, intake empty→reselect, expire_stale_held, Held API 24h `updated_at`
- T2 depends_on: [T1] - force_include.py, intake hooks, publish.expire_stale_held, worker/ingestion call, Held GET filter, HELD_EXPIRED label
- T3 depends_on: [T2] - Focused pytest + vitest; draft PR; CI green; do not merge
# Task: Flex re-sync false coverage alarm

Sync Now re-ingests an already-applied trades file. `delivery_rows_present` requires every Flex exec id in the journal. The writer skips some on purpose (legacy key, superseded correction, empty bucket), so those ids never land and every re-sync returns `coverage_unverified`. No rows are missing.

## Dependency graph

- T1 depends_on: [] - Red test: a legacy-key-skipped META bucket verifies as duplicate, not coverage_unverified
- T2 depends_on: [T1] - Coverage uses `rehydrate_from_executions`; covered when it would import 0. Dropped rows and an unfinished journal walk stay unverified
- T3 depends_on: [T2] - Focused pytest green; PR; CI green

## Checklist

- [x] T1 Failing test (`coverage_unverified` on the META legacy-key bucket)
- [x] T2 Implementation (`rehydrate_from_executions` import count; disagreements stay unverified)
- [ ] T3 Verify and ship

## Review

- Exec-id presence stays a sufficient early pass. A finished journal walk whose writer would import nothing is covered, including a same-day META fill matched on (ticker, date, structure). Dropped rows, an unfinished walk, a positive import count, and an aggregate qty/notional disagreement stay unverified.

# Task: Option secdef singleflight

A VIX page spawned one `ib_option_chain.py` per expiry plus an unscoped `ib_chain.py`, filled the 3-slot interactive lane, and 502'd the book.

## Dependency graph

- T1 depends_on: [] - Red tests: one snapshot per symbol is shared by expirations and every expiry; failures are not cached; the index ticket loads expirations from `/api/options/expirations` and never calls the unscoped index chain
- T2 depends_on: [T1] - Snapshot builder, 60s singleflight cache, route slice, index form uses the equity expirations route
- T3 depends_on: [T2] - Focused pytest and vitest green, PR, CI

## Checklist

- [x] T1 Failing tests
- [x] T2 Implementation
- [ ] T3 Verify and ship
- [x] T3 Verify and ship

## Review

- One `--snapshot` read per symbol for 60s. Expirations and every expiry slice it. The index ticket loads expirations from `/api/options/expirations` and calls `ib_chain.py` only for the selected expiry. Focused pytest 38 passed, vitest 10 passed.

# Task: Split gitdirs (runner trust follow-up to R02-A)

Operator 2026-09-23: keep R02-A. The rung gets its own writable gitdir (`$WEEKEND_ROOT/.gitdirs-agent/<loop>.git`); host git keeps `$WEEKEND_ROOT/.gitdirs/<loop>.git` and never opens the agent gitdir.

## Dependency graph

- T1 depends_on: [] - Red tests: codex roots hold the agent gitdir and not the host gitdir; agent branch/commit/fetch/merge-tree/push with the host gitdir read-only; planted agent config never runs on the host path nor survives sanitize; prune skips gitfile clones; setup provisions the root
- T2 depends_on: [T1] - Wrapper helpers (sanitize/align/agent_git_ro), GIT_DIR pin for nightly_green_base, prune, setup_*, operations.md
- T3 depends_on: [T2] - Focused pytest green; draft PR; CI green; Mac mini setup_* reruns after merge

## Checklist

- [x] T1 Failing tests
- [x] T2 Implementation
- [ ] T3 Verify and ship

# Task: Epoch AI public CSV source (S1)

Public CC BY Epoch CSVs on the AI-infra pane. Research/context only. Not a trade signal.

## Dependency graph

- T1 depends_on: [] - Red tests + fixtures: 3 series, attribution, header drift, non-frontier FLOP ignored, exact two CSV URLs
- T2 depends_on: [T1] - source_id epoch, S1, collectors, snapshot experimental, no AA/chip-stock
- T3 depends_on: [T2] - S1 measure copy Source: Epoch AI; 18→19 / 17→18 pins
- T4 depends_on: [T3] - Focused pytest + vitest; draft PR; CI watch; do not merge

## Checklist

- [x] T1 Failing tests
- [x] T2 Implementation
- [x] T3 Frontend copy
- [ ] T4 Green suites and draft PR

# Task: Runner trust (R01-A / R02-A)

Operator 2026-09-22: R01-A (no host browser endpoint on the codex write rung), R02-A (host-owned gitdir the agent cannot write).

## Dependency graph

- T1 depends_on: [] - Red tests: codex env has no PW_TEST_CONNECT_WS_ENDPOINT; host git uses $WEEKEND_ROOT/.gitdirs/<loop>.git; codex writable_roots omit .git
- T2 depends_on: [T1] - Wrappers, plists, setup scripts, prune protection, operations.md
- T3 depends_on: [T2] - Focused pytest green; PR; Mac mini setup_* after merge

## Checklist

- [x] T1 Failing tests
- [x] T2 Implementation
- [ ] T3 Verify and ship

# Task: After-hours option last is last trade or last bid/offer, not previous close
# Task: Research Dropbox discovery lookback + empty-cursor footgun

Sep 18-style gaps: daemon today-only + 2-day `date_scopes` + cursor advance on zero eligible work.

## Dependency graph

- T1 depends_on: [] - Red tests: 7-day lookback, env override, no empty cursor advance, backlog gap helper, daemon current_only window
- T2 depends_on: [T1] - `date_scopes` lookback, discover/ingest persist rules, health/journal gap, docs
- T3 depends_on: [T2] - Green focused tests; draft PR; no merge

## Checklist

- [x] T1 Failing tests
- [x] T2 Implementation
- [ ] T3 Draft PR; hold merge

# Task: Held-tab feed rail footer layout

Joe via CoS: CAPTURE BASIS mid-label gap + SOURCE/CAPTURE overlap at ~1024 after PR #573 grid.

## Dependency graph

- T1 depends_on: [] - Red: CSS contract + e2e geometry (nowrap keys, even gaps, no overlap)
- T2 depends_on: [T1] - Flex + gap chips; drop 16ch / 3-col grid; mobile stack without space-between
- T3 depends_on: [T2] - Green focused suites; draft PR; CI; no merge

## Checklist

- [x] T1 Failing rail-layout contract
- [x] T2 `.dashboard-news__rail` flex chips
- [ ] T3 Draft PR; CI; hold merge for Joe

# Task: Held card hides excerpt, shows full Selector note

Joe mobile screenshot: raw `context.excerpt` markdown dump dominates the Held
review card; `context.selectorReason` dies under the tab bar. UI-only.

## Dependency graph

- T1 depends_on: [] - Red tests: excerpt not shown; full selector note shown / scrollable
- T2 depends_on: [T1] - Drop excerpt mount; `.reason` overflow-y auto + max-height
- T3 depends_on: [T2] - Focused green; draft PR; CI; no merge

## Checklist

- [x] T1 Failing tests on research-held-ui + e2e excerpt visibility
- [x] T2 ResearchHeldReview.tsx + module CSS
- [ ] T3 Draft PR; CI green on touched tests; no merge

# Task: Relax research PDF hold and invalidation gates

Implement plan #583 (`tasks/research-hold-gates-relax-plan.md`). Joe DoD:
remove length/8-page/caption/copy/NUMBER_NOT_ON_PAGE holds; keep shape, VERIFY,
TEXT_ONLY_WITH_FIGURES, triage. Separate implement PR from main. No merge.

## Dependency graph

- T1 depends_on: [] - Red tests for removed gates (publish, intake, pipeline, runtime, outcomes)
- T2 depends_on: [T1] - publish.py delete validate_rendered_copy; caption type-only; title/body non-empty
- T3 depends_on: [T2] - intake + pipeline validators, advisory grounding, prompts
- T4 depends_on: [T3] - docs/policy/ground; green tests; draft PR; CI; no merge

## Checklist

- [x] T1 Failing tests on main
- [x] T2 publish.py
- [x] T3 intake.py + pipeline.py
- [x] T4 Docs, focused green, draft PR #584; CI green on fa62035d; no merge
- [x] T5 Joe 2026-09-20: TEXT_ONLY_WITH_FIGURES is one re-SELECT, hold only after miss; amend #584; no merge
- [ ] T6 Joe 2026-09-20: humanize SELECT title/body rails (prompt-only); fold into #584; no merge

## Review

- Plan PR #583 is design only. Do not edit it.

# Task: PDF chart evidence not text-only (PR #574 plan)

Published research posts that cite PDF pages with charts must carry those
chart images. Implement P1-P3 from `docs/design-shots/pdf-chart-evidence/PLAN.md`.

## Dependency graph

- T1 depends_on: [] - Red tests: figures B/C/D/F/rotation/skipped_pages; intake TEXT_ONLY_WITH_FIGURES hold
- T2 depends_on: [T1] - P1 `figures.py` displayed-frame detect, raster clusters, kind, skipped_pages
- T3 depends_on: [T1] - P2 intake hold + P3 SELECT sentence + catalogue `kind` + reason-code label + docs
- T4 depends_on: [T2, T3] - Green focused suites; draft PR; CI

## Checklist

- [x] T1 Failing tests on main
- [x] T2 figures.py
- [x] T3 intake / SELECT / web / docs
- [ ] T4 Draft PR, CI green, no merge

## Review

- Live JPM PDF probes optional if the private research root is absent.

# Task: Kelly order-path warn-not-block (2026-09-19)

`RADON_KELLY_ENFORCE_ORDERS=1` must WARN, not BLOCK, place_order.

## Dependency graph

- T1 depends_on: [] - Red: oversized ticket + enforce on warns, IB still called
- T2 depends_on: [T1] - check_kelly_ticket logs WARNING; place_order continues; mode default warn
- T3 depends_on: [T2] - Docs D11/C.4 warn-only; draft PR; CI green

## Checklist

- [x] T1 Failing warn + wire tests
- [x] T2 Guard + place_order continue; attach `kelly_warning`
- [ ] T3 Spec, PR, CI

## Review

- Order path only. Evaluate M6 fail-closed unchanged. Fat-finger `order_limits` still blocks.

# Task: Fortune's Formula Kelly implement (2026-09-19)

Implement locked spec `docs/risk/kelly-fortunes-formula.md`. Draft PR vs main.

## Dependency graph

- T1 depends_on: [] - Library: default 0.5, ban full Kelly, scalar cap, hooks
- T2 depends_on: [T1] - M6 fail-closed + kelly_guard flag-off
- T3 depends_on: [T1] - TypeBox/wrapper/pi-tools + half-Kelly prose/site
- T4 depends_on: [T1, T2, T3] - Focused tests, draft PR vs main, CI

## Checklist

- [x] T1 `scripts/kelly.py` constants, config, ticket, ruin
- [x] T2 `evaluate.py` M6 + `kelly_guard.py` default off
- [x] T3 Schema/wrapper + SYSTEM/prompt/site half-Kelly default
- [ ] T4 Draft PR, CI green, prod enable still waits Joe

## Review

- Default fraction 0.5 (Joe-signed). Full Kelly rejected. Cap 2.5% on scalar.
- `RADON_KELLY_ENFORCE_ORDERS` unset on every unit.

# Task: Newsfeed share 502 (2026-09-19)

Production POST /api/newsfeed/share 502s with "Voice rewrite unavailable".
Journal: Missing Anthropic subscription. Next.js has no subscription binds.

## Dependency graph

- T1 depends_on: [] - Red: nextjs must bind grant dirs; relay must not
- T2 depends_on: [T1] - Bind nextjs; keep relay excluded; low-reasoning share; fence-strip voice JSON
- T3 depends_on: [T2] - Focused green, PR, CI

## Checklist

- [x] T1 Failing bind + share-call-shape tests
- [x] T2 Runtime allowlist + share route + parseVoiceCopy
- [ ] T3 PR, CI green

## Review

- Live: 2026-09-19 16:09:36Z radon-nextjs `[newsfeed/share] voice rewrite failed: Error: Missing Anthropic subscription`.
- 2aba1229 excluded nextjs from grant mounts. Share and assistant run in Next.js.

# Task: GEX45 GPU deployment tooling (2026-09-19)

## Dependency graph

- T1 depends_on: [] - Define isolated GPU-host bootstrap/runtime contracts and verify Blackwell-compatible upstream pins.
- T2 depends_on: [T1] - Implement Ubuntu 24.04 bootstrap, SSH/firewall/Tailscale preparation, and private vLLM service tooling.
- T3 depends_on: [T1] - Write host-free regression coverage and GPU-host runbook.
- T4 depends_on: [T2, T3] - Static review, publish PR, repair all exact-head CI failures, and send PR-green notification.

## Checklist

- [x] T1 Contracts and upstream verification.
- [x] T2 Deployment tooling.
- [x] T3 Regression coverage and runbook.
- [ ] T4 Exact-head CI green and notification.

## Review

- Isolated worktree /tmp/radon-slm-gpu-deployment, branch feat/slm-gpu-deployment from origin/main. No production activation, purchases, training, or local test suites.
- Static verification: Ruff, bash syntax, and diff whitespace checks pass. Live GPU acceptance remains gated on server provisioning.

# Task: Four-digit chain strikes [IMPLEMENTED; PR CI PENDING]

SNDK calls ladder clips `$1,737.00` to `$1,70...` because chain-first pins
`.chain-anchor-strike-col` at 72px and strike cells inherit ellipsis.

## Dependency graph

- T1 depends_on: [] - Red CSS + E2E: 4-digit formatted strike fully visible
- T2 depends_on: [T1] - Widen strike col; do not ellipsis strike identity
- T3 depends_on: [T2] - Focused green, PR, CI

## Checklist

- [x] T1 Failing strike-column contract + chain-first four-digit overflow assert
- [x] T2 CSS width 7.5rem + clip on `td.chain-strike`
- [ ] T3 PR, CI green

## Review

- [x] Vitest `chain-strike-column.test.ts` 3 passed. Playwright CSS
      measurement 1 passed (`$1,737.00` and `$12,345.00` unclipped).
# Task: Mixed-age combo Today P&L 2026-09-17 [IMPLEMENTED; PR CI PENDING]

SPY bull put spread: overnight long 740P + same-day short 760P. Today P&L
equals total P&L (-$162,085). Same-day identity must not apply to mixed-age
combos.

## Dependency graph

- T1 depends_on: [] - Red tests: SPY screenshot numbers, OCC journal ticker, partial fill_dates
- T2 depends_on: [T1] - Per-leg Today P&L + combo entry_date from overnight legs
- T3 depends_on: [T2] - Focused suites, Playwright, PR, CI green

## Checklist

- [x] T1 Failing web + ib_sync + journal-map tests
- [x] T2 getTodayPnlDollars per-leg; isSameDay false for mixed; blotter OCC + partial fills
- [ ] T3 Focused green, e2e, PR, CI, Pushover

---

# Task: Vol/Skew MR scanner mode 2026-09-16 [IN PROGRESS]

Scanner mode `vol-skew-mr` (UI: Vol/Skew MR, `/scanner?mode=vol-skew-mr`).
Lakha / Options Insight short-term top/bottom framing. Mirror strength/theta
family. Credit in help tooltip. Do not merge.

## Dependency graph

- T1 depends_on: [] - Failing gate tests (RSI/%B, IV path, skew, verdicts)
- T2 depends_on: [T1] - `vol_skew_mr_scanner.py` + disk/Turso snapshot
- T3 depends_on: [T2] - FastAPI + Next GET/POST, UI tab, comma ticker search
- T4 depends_on: [T3] - Route/UI/e2e tests, PR, CI green. No merge.

## Checklist

- [x] T1 Red gate tests
- [x] T2 Scanner + snapshot
- [x] T3 Routes + Vol/Skew MR tab
- [ ] T4 Focused green, PR, watch CI

## Review

- [ ] `/scanner?mode=vol-skew-mr` renders TOP_MR / BOTTOM_MR / continue

---

# Task: Contextual browser tab titles 2026-09-16 [IN PROGRESS]

Tabs currently all read "Radon Terminal". Every App Router page must export
a page-first title (`Orders · Radon`, `Positions · Radon`) so truncated tabs
stay distinct.

## Dependency graph

- T1 depends_on: [] - Title helper + failing contract that every page.tsx sets metadata
- T2 depends_on: [T1] - Root title template and routeMetadata/generateMetadata on every page
- T3 depends_on: [T2] - Focused Vitest green, PR, exact-head CI, Pushover. Do not merge.

## Checklist

- [x] T1 Red contract for missing per-page titles
- [x] T2 Template `%s · Radon`; static, ticker, regime, options, scanner titles
- [ ] T3 Focused tests, PR, CI green, Pushover

# Task: Order ticket shows full option contract 2026-09-16 [IN PROGRESS]

Docked 384px ticket ellipsizes `1x $strike Call` to `1x...`. The strike and
right must stay fully visible; wrap controls instead of clipping the contract.

## Dependency graph

- T1 depends_on: [] - Red CSS/source contract that the leg label cannot ellipsize
- T2 depends_on: [T1] - Stop shrinking the contract; wrap the row
- T3 depends_on: [T2] - Focused tests, PR, CI green. Do not merge.

## Checklist

- [x] T1 Failing layout contract
- [x] T2 Full `1x $strike Call/Put` in the rail
- [ ] T3 PR + CI

---

# Task: Newsfeed + distill on shared model ladder 2026-09-15 [IN PROGRESS]

Stop Cerebras-first text tagging and KB distillation. Both walk
`scripts/clients/model_ladder.py` (subscription -> nvidia -> cerebras last).

## Dependency graph

- T1 depends_on: [] - Red tests: Cerebras not first; soft-fail; 3-tag + distill JSON
- T2 depends_on: [T1] - `complete_text_json` + CLI; wire distill.py and tagger.js
- T3 depends_on: [T2] - Docs/allowlist that claimed Cerebras-first
- T4 depends_on: [T3] - Focused green, PR, watch CI. Do not merge.

## Checklist

- [x] T1 Failing tests for order, soft-fail, contracts
- [x] T2 Shared text JSON helper; JS tagger subprocess bridge
- [x] T3 Owner docs + newsfeed env allowlist
- [ ] T4 PR open, CI green, no merge

## Review

- [ ] JS tagger reaches the ladder via `scripts/clients/model_ladder_cli.py`

---

# Task: IR flow-refresh analysis timeout 2026-09-15 [IN PROGRESS]

P1 `radon-flow-refresh.service` Result=exit-code at 20:00Z. Journal:
capacity-shed retry then `flow-analysis FastAPI outcome indeterminate
(curl=0, http=502)` at +128s. Same ~120s kill on 2026-09-11 20:00Z.
`/flow-analysis` timeout=120 while wrapper SCAN_TIMEOUT default is 180
and `/discover` already uses 180.

## Dependency graph

- T1 depends_on: [] - Red regression: POST /flow-analysis timeout == wrapper budget
- T2 depends_on: [T1] - Raise endpoint timeout to 180; runbook case
- T3 depends_on: [T2] - Focused green, commit, push fix/*, ensure PR

## Checklist

- [x] T1 Failing test (120 vs 180 red)
- [x] T2 Fix + runbook (`flow-refresh-analysis-timeout`)
- [ ] T3 Ship branch/PR

## Review

- [x] Live journal: capacity shed then +128s indeterminate 502; health/lite authenticated; reproduce POST 200 after incident

---

# Task: PR #450 CI green leftover workflow tests + e2e ledger [IN PROGRESS]

Unblock `chore/remove-workflow-composer` CI. Do not merge.

## Dependency graph

- T1 depends_on: [] - Delete leftover `scripts/tests/test_workflow_*.py` that still import the removed package.
- T2 depends_on: [T1] - Stamp `# REVIEWED 2026-09-15 mobile-p2-polish.spec.ts` for the incidental hold-out edit.
- T3 depends_on: [T2] - Focused pytest, push to the same PR, watch CI.

## Checklist

- [ ] T1 Delete `test_workflow_order_preflight.py` and `test_workflow_scanner_source.py`.
- [ ] T2 Ledger annotation dated on or after the spec change.
- [ ] T3 Focused pytest green; push; CI green or only non-gating failures.

## Review

- [ ] Do not merge. Report what was fixed.

---

# Task: Assistant September P&L empty-final 2026-09-11

Grok 4.6 burned 49 tool calls on "analyze all my trades for september 2026 and tell me my P&L" then showed the canned max-rounds string.

## Dependency graph

- T1 depends_on: [] - Prove prefetch, tool_choice=none forced final, Grok reasoning_effort=low, P&L synthesis.
- T2 depends_on: [T1] - Prefetch get_realized_pnl, keep tools on cap-hit, unwrap fenced payloads.
- T3 depends_on: [T2] - Focused assistant/provider tests, PR, GitHub CI, Pushover.

## Checklist

- [x] T1 Screenshot prompt maps to 2026-09-01..2026-09-30; empty Grok final still renders dollars.
- [x] T2 Loop prefetch + tool_choice none + reasoning_effort low + 90s timeout + system-prompt routing.
- [ ] T3 PR, exact-head CI green, Pushover.

---

# Task: DeepSec sibling worker [IN PROGRESS]

Split DeepSec from the security-nightly 2h audit cap. Not a second
audit-remediate-deliver loop. Do not merge. Do not advance last-audited by hand.

## Dependency graph

- T1 depends_on: [] - Classify/harvest/queue-lock tests and `security_deepsec.py`
- T2 depends_on: [T1] - Worker, plist, wrapper harvest, setup, SKILL, ops
- T3 depends_on: [T2] - Focused tests green, draft PR from main (not #421)

## Checklist

- [x] T1 Red tests for still-running != TIMEOUT, harvest-when-ready, queue lock
- [x] T2 Sibling worker + harvest handoff + two dead-men
- [ ] T3 Green tests, PR against main, no merge

---

# Task: Daily Dark Pool History mobile overflow --- 2026-09-11 [IN PROGRESS]

Flow Analysis HISTORY table clips PRINTS on ~390px. Wrap in shared
`table-wrap` with sticky DATE. Layout/CSS only. Do not merge.

## Dependency graph

- T1 depends_on: [] - Red tests for table-wrap + sticky DATE + mobile overflow.
- T2 depends_on: [T1] - DailyTable wrap + globals.css sticky/nowrap.
- T3 depends_on: [T2] - Focused vitest/e2e, draft PR, no merge.

## Checklist

- [x] T1 Failing wrap / sticky / overflow tests.
- [x] T2 table-wrap + sticky DATE CSS.
- [ ] T3 Green tests, PR against main, no merge.

---

# Task: Option order-sheet High/Low/Volume --- 2026-09-11 [IN PROGRESS]

Positions → order sheet for a short single-leg option (SNDK $1750C 2026-09-25)
shows High/Low/Volume as `---` while Bid/Mid/Ask/Mark/Day% populate.
Fix relay drops of RT_VOLUME / tickSize volume; do not invent High/Low.
Do not merge.

## Dependency graph

- T1 depends_on: [] - Red tests for RT_VOLUME parse and tickSize broadcast.
- T2 depends_on: [T1] - parse RT_VOLUME; hydrateAndBroadcast on tickSize + tickString.
- T3 depends_on: [T2] - Focused vitest, draft PR, CI watch. No merge.

## Checklist

- [x] T1 Failing RT_VOLUME / broadcast / option-null-OHLV tests.
- [x] T2 Handler + relay wiring.
- [ ] T3 Green tests, PR against main, no merge.

---

# Task: TWR coverage-lag overwrite 2026-09-11 [IN PROGRESS]

Weekday radon-perf-twr must not clip Turso NAV to lagging mirrored-flow
covered_through and overwrite a newer published tape. Flex-pull unit must
keep ProtectHome=read-only and ReadWritePaths /home/radon/radon/data.
Do not merge.

## Dependency graph

- T1 depends_on: [] - Red tests for Sep-8 overwrite and persist no-clobber.
- T2 depends_on: [T1] - Keep newer NAV, skip unverified TWR sessions, persist guard.
- T3 depends_on: [T2] - Flex-pull ReadWritePaths, unit comments, focused pytest, draft PR.

## Checklist

- [x] T1 Failing overwrite / persist tests.
- [x] T2 Builder + persist + warning.
- [ ] T3 Flex-pull unit, hash pin, CI watch. No merge.

---

# Task: CTA vision cascade 2026-09-10 [IN PROGRESS]

Never hard-fail radon-cta-sync.service on Anthropic Vision credit exhaustion.
Joe's order: subscription (anthropic -> grok -> cursor -> codex -> gemini),
then NVIDIA, then Cerebras. Do not merge.

## Dependency graph

- T1 depends_on: [] - Red tests for credit/billing fallthrough and exact order.
- T2 depends_on: [T1] - Shared vision_cascade helper + MenthorQClient wiring.
- T3 depends_on: [T2] - Service comment, ops messaging, focused pytest, draft PR.

## Checklist

- [x] T1 Failing cascade tests.
- [x] T2 Helper + client + classifier.
- [ ] T3 Docs, CI watch, no merge.

---

# Task: Financial research workbench 2026-09-10 [IN PROGRESS]

## Scope and acceptance
Implement the 13 requested capabilities as integrated, source-grounded workflows, reusing existing research, indicators, order-risk and connector infrastructure. No fabricated provider access, no synthetic live financial data, no automatic orders. Imports must retain provenance; unavailable licensed data must be explicit. Valuation assumptions must be editable and auditable. Desktop and mobile must work. No local test suites; GitHub CI runs adversarial/unit/browser suites.

## Dependency graph
- T1 depends_on: [] - Verify announcement, inspect existing architecture and isolate clean branch.
- T2 depends_on: [T1] - Cited evidence domain, earnings/blackout, fundamentals normalization, transcript deltas, deals and AI-capex joins.
- T3 depends_on: [T1] - Research workbench UX, accessible desktop/mobile flows and focused browser regressions; integrate T2 contracts.
- T4 depends_on: [T1] - MCP evaluation/reliability, agent-readable research and trading-governance audit of existing chokepoints.
- T5 depends_on: [T2,T3] - Valuation/export artifacts, desk-note handoff, marketing wedge and production integration.
- T6 depends_on: [T2,T3,T4,T5] - Adversarial review, generated files, desktop/mobile browser evidence, exact-head green PR CI.
- T7 depends_on: [T6] - Show-me visual, review record and confirmed Pushover notification.

## Checklist
- [x] T1 Announcement and Astra model verified against official pages; clean worktree at /tmp/radon-financial-research from 9ef5fa8a.
- [x] T2 Evidence and research domain; canonical periods, financing roles and as-of boundaries pinned by adversarial regressions.
- [x] T3 Research workbench UI; preserved draft state and 44px disclosures after independent review.
- [x] T4 Reliability evaluator, readable PDF manifests/retrieval and operator governance export.
- [x] T5 Formula XLSX, PPTX, valuation/comps, dated portfolio exports, ticket checklist and marketing section.
- [ ] T6 Browser and exact-head CI green.
- [ ] T7 Visual walkthrough created at docs/research/show-me-financial-workbench.html; delivery/notification waits for green CI.

## Review
Implementation complete; exact-head CI pending on PR https://github.com/joemccann/radon/pull/398. Independent visual reviewer confirmed all original findings resolved. Preserve original workspace dirty files. No merge or deployment requested.

- No local test suites executed. TypeScript static checks, staged-source ESLint and secret scan passed for c0eb7728.
- Browser: illustrative source intake produced 1 source / 6 cited facts; valid XLSX downloaded; 1440px desktop and 393px mobile, zero page errors and app width 393/393.
- Independent reviews repaired fiscal-period substitution, financing-role smuggling, future-source prompts/exports, missing plaintext provenance, and Labs state loss.
- Marketing page exposed a pre-existing decorative WebGL crash; bounded fallback preserves content with regression coverage. New section fits mobile; existing plate-caption overflow remains outside the added section.
- Verification artifacts and show-me draft: /tmp/radon-financial-evidence/.
- CI repair cycle at 25a269c4: navigation and route registries, isolated decorative fallback regression, and source-tampering fixture corrected from runner logs. Both app images and Vercel previews passed. Full exact-head suites remain required after repair.

---

# Task: OpenDesign Arena collector stub 2026-09-10 [IN PROGRESS]

Thin LLM/model-quality collector for OpenDesign Arena on Demand `D6`.
No GPU scarcity mix, no preference/radar UI, no merge.

## Dependency graph

- T1 depends_on: [] - Red tests for HTML parse, broken markup, Demand snapshot and live transport.
- T2 depends_on: [T1] - Fixture, parser, registry, collect wiring, contract note.
- T3 depends_on: [T2] - Focused pytest, PR, CI watch. Do not merge.

## Checklist

- [x] T1 Failing parse/status/snapshot tests.
- [x] T2 Collector stub + captured fixture + D6 Demand registry.
- [ ] T3 Focused green and draft PR.

---

# Task: Historical trade fill prices 2026-09-10 [IMPLEMENTED; PR CI PENDING]

## Dependency graph

- T1 depends_on: [] - Trace journal fill prices and desktop/mobile historical rendering.
- T2 depends_on: [T1] - Red/green price derivation, missing-price and aggregate provenance tests; add sortable Avg Fill and mobile metric.
- T3 depends_on: [T2] - Focused tests, types/lint, browser verification and review.
- T4 depends_on: [T3] - Publish scoped PR, supervise exact-head GitHub CI through green, send confirmed Pushover. Do not merge.

## Checklist

- [x] T1 Journal fill_price reaches executions.price but neither historical surface renders it; rehydrated rows can blend both sides.
- [x] T2 Recorded quantity-weighted prices, signed/zero values, truthful unavailable and aggregate labels; desktop sorting and mobile parity.
- [x] T3 158 focused Vitest tests, TypeScript and scoped ESLint passed; helper coverage 100%; 31 clean-snapshot codemap tests and 6 curation tests passed.
- [ ] T4 Publish, repair and verify CI on GitHub, then notify.

## Review

- Preserve existing user changes in tasks/lessons.md, tasks/todo.md and docs/research/. No order execution, historical rewrites or production changes.
- Browser: 3/3 Playwright cases passed against exact historical component source, real hook and CSS in an isolated harness; 1280px/393px screenshots inspected, no page errors or document overflow. Full Next page verification remains blocked by local sign-in and single-dev-server constraints.
- Avg Fill displays recorded execution VWAP, not cost-basis-derived prices; collapsed or mixed-side records are visibly marked Aggregate and are not represented as separate entry/exit prices. Missing price remains null, signed/zero premiums and four-decimal precision are retained.
- Pre-PR local full suites: Vitest 9,303 passed / 1 failed (fixed historical table remained in the known-unwrapped exception list; removed that entry); Python 12,749 passed / 36 failed / 19 skipped, including environment and local-artifact failures. No full-local-green claim.
- Operator correction: all remaining test runs and CI repairs execute on GitHub, not locally. PR publication authorized; no merge, deployment, or trading action.

---

# Task: Flow analysis truncated dark-pool cache [IN PROGRESS]

SNDK `/flow-analysis` served 20-session history with ~900-print days
next to ~19k-print days. Discover's 2-page scoring walk was written as
schema-v2 complete and never re-fetched.

## Dependency graph

- T1 depends_on: [] - Failing regression: 976-print v2 row is a miss for flow consumers.
- T2 depends_on: [T1] - Persist `complete`; reject scoring-walk rows; discover writes incomplete.
- T3 depends_on: [T2] - Focused pytest green, commit, PR, CI.

## Checklist

- [x] T1 Red tests on SNDK-shaped 976-print cache + fetch_flow refetch + discover poison.
- [x] T2 Cache contract + discover/fetch_flow writers.
- [ ] T3 PR green.

## Review

- [x] Affected pytest 212 passed. Cache/flow/discover 98 passed.

---

# LLM historical coverage and source provenance (2026-09-08)

Backfill every AI-infrastructure metric to the earliest authoritative, technically
and legally available observation; keep snapshot-only sources honest; label every
rendered section with its originating provider and surface actual provider health.

## Dependency graph

- [x] T1 depends_on: [] - Inventory charts, collectors, provider contracts, existing production coverage, authoritative historical endpoints, date floors, access, rate, and redistribution limits.
- [x] T2 depends_on: [T1] - Add red regressions for earliest-window selection, resumable provider-specific backfill, source status completeness, and explicit UI provenance.
- [x] T3 depends_on: [T2] - Implement bounded historical collectors/backfill orchestration and preserve append-only vintages, checkpoints, rate limits, and snapshot-only semantics.
- [x] T4 depends_on: [T3] - Label every LLM section and chart with linked source providers; render availability, restriction, error, and observation coverage without converting missing data to zero.
- [x] T5 depends_on: [T3,T4] - Run focused Python/Vitest/Playwright and browser visual checks; verify every configured provider fetches or reports an exact actionable status.
- [ ] T6 depends_on: [T5] - Commit, open PR, supervise exact-head GitHub CI through green, notify, merge, deploy, execute production backfill, and verify live earliest dates/provider rendering.

Graph: T1 -> T2 -> T3 -> T4 -> T5 -> T6; T3 -> T5.

## Review

- Publisher floors and limits are documented from originating sources: SEC 2009, NOAA July 2018, EIA DOM January 2019, OpenRouter January 2025, Vercel October 2025, and GPU Rental Prices July 2026; current-only and unsupported histories remain explicit.
- Live bounded validation collected 10,446 SEC rows across ten issuers, 812 Vercel rows, 439 GPU history rows and 32 NOAA rows without production writes.
- Focused validation: 95 Python collector/core tests, 7 Vitest component tests, Ruff checks, codemap pre-commit, and desktop/mobile Playwright source-label visuals passed. Full suites move to GitHub CI per operator direction.

---

# Task: Reliability remediate 2026-09-08 [IN PROGRESS]

Complete the reduced-rung P0/P1 backlog without leaving evidence archives corruptible.

## Dependency graph

- T1 depends_on: [] - Reproduce REL-253 torn-evidence archive behavior.
- T2 depends_on: [T1] - Add crash-safe digest-verified archival at both write paths.
- T3 depends_on: [T2] - Run focused tests, record the result, commit, and push.

## Checklist

- [x] T1 Identify REL-253 as the sole reduced-rung un-DONE P0/P1 task.
- [x] T2 Add red/green fault-injection coverage and atomic archival.
- [x] T3 Record focused and permanent-drill verification, commit, and push.

## Review

- [x] RED: focused suite could not import missing `archive_raw`; GREEN: 84 passed in the runner venv.
- [x] Permanent drills: Python 94 passed; Vitest 12 passed.
- [ ] Detached full-gate stage has no `DONE` sentinel; 2026-09-08 retry also exited before `pytest_rc`; continuation must re-run all three gates.
- [ ] Detached full-gate stage has no `DONE` sentinel; continuation must re-run all three gates.

---

# Task: Reliability remediate 2026-09-09 [BLOCKED]

## Dependency graph

- T1 depends_on: [] - Resume the dated branch and reconcile reduced-rung P0/P1 backlog.
- T2 depends_on: [T1] - Re-run the permanent fault-injection drills with resolved paths.
- T3 depends_on: [T2] - Run three serial detached full gates and append durable results.

## Checklist

- [x] T1 REL-251 through REL-253 are source-DONE; reduced rung admits no new P0/P1 task.
- [ ] T2 BLOCKED: detached drill stage was terminated before its required sentinel.
- [ ] T3 BLOCKED: the third detached closing-gate attempt produced no `DONE` sentinel.

## Review

- [ ] Three genuine detached attempts across 2026-09-08/09 terminated before the stage sentinel; no gate count is claimed.

---

# Task: Reliability remediate 2026-09-07 [IN PROGRESS]

## Dependency graph

- T1 depends_on: [] - Verify P0/P1 backlog completion on the dated reliability branch.
- T2 depends_on: [T1] - Run permanent drills and three sequential full-gate baselines.
- T3 depends_on: [T2] - Append durable gate evidence and commit/push the phase record.

## Checklist

- [x] T1 Verify REL-251 and REL-252 completion.
- [ ] T2 Permanent drills green; full gates incomplete because the runner terminated the required stage.
- [x] T3 Record durable gate-stage evidence.

## Review

- [x] Reduced scope contains no un-DONE P0/P1 items.
- [x] Permanent drills: Python 94 passed; Vitest 12 passed.
- [ ] Full-gate stage has no DONE sentinel; continuation required.

# Task: Caddy site for mcp.radon.run (issue #232)

Vercel DNS already points mcp.radon.run at 5.78.148.38. Add a dedicated Caddy site that serves only MCP, and admit that Host on the FastMCP allowlist.

## Dependency graph

- T1 depends_on: [] - Failing Caddyfile + allowed-hosts tests for mcp.radon.run.
- T2 depends_on: [T1] - Caddy site, FastMCP Host/Origin allowlist, cloud-services runbook.
- T3 depends_on: [T2] - Focused tests, PR against main. Do not flip HOSTED_MCP_URL until live TLS.

## Checklist

- [x] T1 Failing tests
- [x] T2 Caddy site + allowlist
- [ ] T3 Verify and open PR

---

# Task: CI performance deliver before/after/% [IN PROGRESS]

Issue #196 deliver/report must cite before, after, and % change for time-saving fixes.

## Dependency graph

- T1 depends_on: [] - Failing contracts for the CI build-time table and skill wording.
- T2 depends_on: [T1] - Helper + CLI + skill/ledger report format. No loop redesign.
- T3 depends_on: [T2] - Focused pytest, PR against main. Do not merge.

## Checklist

- [x] T1 Failing contracts
- [x] T2 Helper + skill
- [ ] T3 Verify and open PR

---

# Task: Orders header WORKING vs QUEUED rows (2026-09-02) [WIP]

PR 236 comment: Overnight TIF is a separate IB venue from EXT `outsideRth`.
DAY+EXT sits Queued overnight and never routes Overnight. Separate honesty
bug: header WORKING 3 while every visible row is QUEUED.

- H1 depends_on: [] - lesson: Overnight != EXT
- H2 depends_on: [] - summarizeOpenOrders uses mapped row status + session
- H3 depends_on: [H2] - command-strip Working 0 when all rows Queued
- H4 depends_on: [H2, H3] - tests + PR

## Checklist
- [x] H1 lesson
- [ ] H2 summarizeOpenOrders
- [ ] H3 command strip
- [ ] H4 PR

---

# Task: SPOF host-split Phase 0/0B/1-prep (2026-08-29)

## Dependency graph

- S1 depends_on: [] - Combined-host-safe code: parameterized Tailscale bind, auto-sync strays, cut PartOf, host role, privileged sudoers
- S2 depends_on: [S1] - Focused cloud tests green
- S3 depends_on: [S2] - PR. Operator runbook for backups, snapshot, bootstrap, second VM

## Checklist

- [x] S1 Code on `feat/spof-host-split`
- [x] S2 Focused tests (333 passed / 3 skipped cloud subset; visudo OK)
- [ ] S3 PR + operator instructions

## Review

- Live Hetzner backups, new CX, helper uninstall, and 2FA are NOT in CI.

---

# Task: Flex sFTP remaining (steps 6-9)

Canonical operator board: `docs/show-me-flex-sftp-ops.html`
Plan: `docs/flex-sftp-cutover.md`

P1/P2 (steps 1-5 code) are on `main` (`26668ef8`). Timer not enabled. No SendRequest.

## Dependency graph

- S6 depends_on: [] - Written Flex grant from filedelivery@. Then VPS: pin host key, IPv4-only ssh_config, first list-dir. Do not ingest. Do not `accept-new`.
- S7 depends_on: [S6] - Portal: method sFTP only if step 2 inventory did not stop. Tick only 1442520 and 1422766. Period Last Business Day. XML. No `IB_FLEX_FLOWS_QUERY_ID`.
- S8 depends_on: [S6] - Code: OpenSSH IPv4 puller, PGP in-memory, classify, `flex-inbox`, stub-SSH tests. Enable `radon-flex-pull.timer` Tue-Sat 07:30 ET only after a file is on the remote. Empty dir: 08:30 list-dir retry. No SendRequest.
- S9 depends_on: [S7, S8] - Dual-run sFTP vs Portal Run `--from-file`. First SendRequest recon Sun after embargo (`raise_if_blocked()` first). Weekday SendRequest = 0.

## Checklist

- [x] S6 Pin host key + first list-dir (2026-08-28). Host `ftp2.interactivebrokers.com` `64.190.196.110` RSA SHA256:C3STmRsxF9UfYvQ8yeJ9vwvK7lMHQ2FcMHchtQd7/wg. Dir `outgoing` empty (first files 8/31).
- [ ] S7 Confirm Portal: only 1442520 + 1422766 ticked. IBKR already set Last Business Day XML. Stop if extra reports are on sFTP.
- [x] S8 Puller + timer (timer disabled until S6 file exists; `flex_sftp_pull.py` stub-SSH tests; not-installed ack)
- [ ] S9 Dual-run then demote

## Stops

- Grant is reporting-integration / master-sub → refuse. Email ingest.
- Human mailbox still needs a statement → do not flip method to sFTP.
- Host-key change, PGP fail, IPv6, 365-day nightly file, ambiguous XML → fail closed.
- `/orders` still POSTs rehydrate → do not enable the timer.

---

# Task: P2 host paths (2026-08-23)

## Dependency graph

- P2a depends_on: [] - Red tests for /etc/radon/env and /var/lib/radon/media
- P2b depends_on: [P2a] - Units, Caddy, media_backup, hashes, CI env
- P2c depends_on: [P2b] - VPS copy env + rsync media before push
- P2d depends_on: [P2c] - Push, deploy, publish-caddy, compatibility symlinks

## Checklist

- [x] P2a Red tests
- [x] P2b Repo cutover
- [x] P2c Host files (env hash match, 5475 media files)
- [ ] P2d CI deploy + publish-caddy + symlinks

## Constraints

- Do not change privileged control-plane scripts (drift_audit, helpers, sudoers).
- Do not restart Gateway.
- /etc/radon/env must exist as a regular file before units switch EnvironmentFile.

---

# Task: ThetaData support for long-history SPX skew (2026-08-09)

## Summary

UW greeks history floor is 2023-09-06, so SKEW / SKEW 2D only plot ~3y.
ThetaData has multi-year OPRA SPX chains + greeks and can backfill pre-UW
sessions for the same constant-maturity 25d put/call construction. Add a
client + optional backfill path; keep UW as live primary (repo priority #2).

## Dependency graph

- T1 depends_on: [] - Account + env: ThetaData plan that includes SPX index options + historical greeks; document `THETADATA_*` keys in `.env.example` (no secrets committed).
- T2 depends_on: [T1] - Research/probe: history depth for SPX monthly chains, auth, rate limits, EOD vs intraday endpoints; capture a small fixture under `scripts/tests/fixtures/`.
- T3 depends_on: [T2] - `scripts/clients/thetadata_client.py` (honest UA, retry, no browser impersonation) + unit tests against the fixture.
- T4 depends_on: [T3] - Wire into `fetch_skew.py` / `fetch_skew2d.py` (or a dedicated backfill mode): rehydrate pre-2023-09-06 ratios into `skew_history` / recompute 2d; document splice rules at the UW seam.
- T5 depends_on: [T4] - Backfill Turso, verify SKEW + SKEW 2D series length and stats, screenshot tabs, ship.

## Checklist

- [ ] T1 Credentials + env contract.
- [ ] T2 Endpoint research + fixture.
- [ ] T3 Client + tests.
- [ ] T4 Skew/skew2d backfill integration.
- [ ] T5 Prod backfill + verify.

## Notes

- **Purchase (thetadata.net/purchase, annual):** Options **Standard** + Indices **Value**; Stocks and Interest Rates off. FAQ: Options does not include index underlyings (SPX cash needs Indices). Pro tiers are live-trading overkill for daily backfill.
- Rejected free substitutes for this construction: Cboe SKEW index, Cboe RXM (strategy P&L).
- ORATS (2007+ summaries) remains a paid alternative if ThetaData depth or SPX coverage fails.

## Review

- Pending.

---

# Task: Repair Codex MCP startup (2026-08-09)

## Dependency graph

- T1 depends_on: [] - Inspect effective Codex configuration and MCP startup diagnostics.
- T2 depends_on: [T1] - Repair the `codex_apps` and `figma` initialization causes with minimal configuration changes.
- T3 depends_on: [T2] - Re-run MCP startup checks and record verification.

## Checklist

- [x] T1 Inspect configuration and diagnostics.
- [ ] T2 Repair affected MCP initialization.
- [ ] T3 Verify both servers initialize.

## Review

- Pending.

---

# Task: CURVE tab freshness follow-up (2026-08-03, same session)

## Plan
- [x] F1 Early timer pass Mon..Fri 20:45 UTC added to radon-yield-curve.timer (22:30 catch-all kept)
- [x] F2 Live estimate: IB probe → 2YY/10Y micro futures DELISTED, TNX no CBOE sub; Yahoo 2YY=F dead; pivot to 10Y-3M via live ^TNX-^IRX (no modeling)
- [x] F3 Red: 5 route + 3 panel failures → green 32/32; typecheck clean; cloud 705; e2e 3/3; live no-mock pass shows +0.99% AS OF 2:59 PM ET
- [ ] F4 Full gate → commit → push once → CI green → VPS timer reinstall + daemon-reload → verify

## Review
(pending)

---

# Task: Parallel TDD swarm for new market indicators (2026-08-03)

## Plan
- [x] Research: study breadth + margin-debt indicators end to end (3 Explore agents)
- [x] Write `.claude/skills/new-indicator/SKILL.md` — canonical new-indicator pattern doc
- [x] Write `.claude/skills/indicator/SKILL.md` — `/indicator <source> <name>` swarm command
- [ ] Demo: 10Y-2Y Treasury yield-curve spread regime tab (slug `curve`, service `yield-curve`, migration 0032)
  - [x] S1 research: Treasury per-year CSV (parse by header name, desc rows, 1990+, public domain); fixture `scripts/tests/fixtures/yield_curve_2026_sample.csv` (146 rows, 07/31 y2=4.28 y10=4.75 spread +0.47); Yahoo ^GSPC UA Mozilla/5.0 + explicit epochs (range=max degrades)
  - [x] S2 spec `docs/indicators/curve.md` + failing tests RED: pytest ModuleNotFoundError fetch_yield_curve; vitest 2 files / 5 tests failed
  - [x] S3 implementers green + committed: ingestion 3fb902bb (19 pytest + 705 cloud), api 7079435d (203 vitest), ui 5ac3e46d (57 vitest + clean tsc)
  - [x] S4 octopus merge b5b1b9a1 clean; full gate GREEN: pytest 4666, cloud 705, vitest 495 files/4694, tsc clean
  - [x] S5 DONE: migration 0032 applied; Turso verified (9151 history rows 1990→2026-07-31, snapshot 1.09MB, service_health yield-curve ok); e2e 3/3 green; live no-mock Playwright pass green; dark+light screenshots in scratchpad; e2e listener fix committed c838dca3 on ind/curve
  - [x] S6 shipped: 667fc09c + 3174bf49 pushed once; CI run 30837359632 all-green incl. Deploy to VPS; timer installed+enabled (next 22:32 UTC), VPS backfill 73s via transient unit, real service run took unchanged-day heartbeat path (9151 sessions, +0.47%); Turso health ok @17:43:53Z from VPS; anon prod API 401 (perimeter). Remaining: prod browser screenshot, worktree cleanup, review
  - [ ] S4 merge, full test suite, fix integration failures
  - [ ] S5 dev server + Playwright screenshot; chart renders, freshness copy honest
  - [ ] S6 commit, push, CI green, verify production
- [x] Screenshots (dark + light, scratchpad curve-tab-{dark,light}.png) + review

## Review

- Shipped: `667fc09c` (CURVE tab vertical slice) + `3174bf49` (skills docs), one push, CI run 30837359632 all-green incl. Deploy to VPS. Skills force-added past the `.claude/skills/` gitignore.
- Swarm mechanics held: 3 worktree implementers off the same base, near-disjoint ownership, clean octopus merge, zero integration test failures. The only cross-worktree coupling (watchdog TS-parity tests) resolved at merge exactly as the ingestion agent predicted.
- Integration fixes at merge level (orchestrator, not agents): e2e no-4xx listener scoped to /api/yield-curve (ambient /api/profile + /api/watchlist 401 without a Clerk session), and `upsert_yield_curve_rows` rewritten to chunked multi-row INSERTs per the Hrana I/O bounding rule before commit.
- Production: migration 0032 applied; Turso `yield_curve_history` 9,151 rows (1990-01-02..2026-07-31); VPS backfill via transient systemd unit (73s); `radon-yield-curve.timer` enabled (22:30 UTC daily incl. weekends so the 26h health window never widens); real service run took the unchanged-day heartbeat path; `service_health` yield-curve ok written from the VPS.
- Not done: authenticated app.radon.run browser screenshot (Chrome extension disconnected; operator MFA session required). Everything short of that is verified. scp to prod was denied by permissions; VPS regenerated its own cache instead.
- Known gaps carried forward: the VPS JSON cache (`data/yield_curve.json`) is the daily run's merge base; if it is ever lost, rerun `--backfill` once or the snapshot regresses to current-year-only. The regime strip renders 4 cells in a 5-col grid (same as MARGIN) — house look, left alone.

---

# Task: Options OrderBuilder layout pass (2026-07-09)

## Shipped
- Fixed leg grid (56px BUY/SELL chip)
- Removed redundant OrderLegPills
- Tappable OrderPriceStrip for combos; no duplicate chips
- Compact skew (4 metrics)
- Prefill chip, compact TIF, risk teaser, no em-dash limit label
- CSS `.order-builder-*`

## Verify
vitest: order-builder-layout, combo-skew-panel, chain-url-deeplink, order-unified-components green
tsc clean
# Task: Options ticker-gated measurement state (2026-07-17)

## Dependency graph

- T1 depends_on: [] - Inspect established ticker-entry and regime spectral-loading patterns and define the explicit Options entry-state contract.
- T2 depends_on: [T1] - Add red regressions for no default symbol, valid ticker submission, provider-label removal, and submitted-state spectral loading.
- T3 depends_on: [T2] - Implement the ticker-gated Options workspace and remove provider identity from the exposure header.
- T4 depends_on: [T3] - Run focused Vitest, Playwright desktop/mobile behavior coverage, visual verification, typecheck, lint, and static checks.
- T5 depends_on: [T4] - Run the full web suite and record review evidence.

## Checklist

- [x] T1 Entry and loading pattern confirmed: use the API-compatible symbol regex, Flow Analysis-style explicit form, and Regime `SpectralLoader` treatment.
- [x] T2 Red ticker-entry/loading regressions added and observed: no initial panel, invalid-symbol rejection, canonical normalized submission, provider-label removal, and spectral loading state.
- [x] T3 Ticker-gated workspace implemented: no MU fallback remains in options routes/navigation; a valid query deep link remains supported without bypassing the entry contract for first visits.
- [x] T4 Focused, visual, and static verification complete: 59 focused Vitest assertions, Playwright 4/4 including the submitted loading state, desktop/mobile screenshot inspection, typecheck, lint, and `git diff --check` passed. ESLint reported 11 pre-existing warnings and no errors.
- [x] T5 Full web Vitest suite passed: 437 files, 4,166 tests passed, 26 skipped (4,192 total); review evidence recorded.

## Review

- The Options workspace begins without a query or API request. It presents an operator-first ticker entry surface instead of preloading a sample instrument.
- A valid submitted ticker is uppercased, encoded into the canonical Net GEX URL, and then mounts the existing exposure panel. Valid bookmarked query URLs continue to work.
- Exposure loading now uses the shared `SpectralLoader` used by Regime, and the provider identity string is absent from the measurement telemetry.
- Verification: focused Vitest 7 files / 59 tests passed; Playwright 4/4 passed, including no pre-submit request, canonical submission, and visible loading state; typecheck passed; lint completed with no errors and 11 pre-existing warnings; full web Vitest passed (437 files, 4,166 passed, 26 skipped); `git diff --check` passed.
- [ ] T4 Focused, visual, and static verification complete.
- [ ] T5 Full verification and review documented.

# Task: Finalize dashboard-plate purge after history rewrite (2026-08-13)

## Dependency graph

- T1 depends_on: [] - Verify the rewritten owned refs, deleted deployments, CDN 404s, and the post-rewrite CI failure.
- T2 depends_on: [T1] - Remap commit-exact gitleaks baselines invalidated by the authorized history rewrite.
- T3 depends_on: [T2] - Run focused gitleaks policy tests and the full Python/cloud/web gates.
- T4 depends_on: [T3] - Commit and push the follow-up to `main`; verify CI, origin reachability, and branch protection.
- T5 depends_on: [T4] - Record the remaining immutable GitHub pull refs and third-party fork cleanup.

## Checklist

- [x] T1 Owned purge verified; post-rewrite gitleaks failure isolated.
- [x] T2 Gitleaks baseline commit IDs remapped.
- [x] T3 Focused and full verification green.
- [ ] T4 Follow-up pushed; CI verification pending.
- [x] T5 External cleanup documented.

## Review

- PR #21 merged. Vercel CDN purged; 221 affected immutable deployments deleted; all four public plate URLs returned 404 twice across five hosts.
- Thirteen owned branches were rewritten atomically with exact leases; three tags were unchanged. Fresh-mirror verification found zero affected paths or binary objects across owned heads/tags; force-push protection is restored.
- History rewriting remapped two vetted gitleaks baseline commits. Both original and mapped identities remain commit-exact while immutable pull refs coexist with rewritten origin history; no regex/path allowlist was widened.
- Verification: gitleaks 2,299 commits / zero findings; Python 6,142 passed, 1 skipped, 62.88% coverage; cloud 825 passed, 4 skipped; Vitest 589 files / 6,093 tests passed, 83.34% statement coverage; Playwright 43 passed; typecheck, lint, production build, output-trace audit, public-asset regressions, and diff hygiene passed.
- External reachability remains in GitHub pull refs for PRs #10-#19 and forks `Joshglobal/radon`, `pbeninte/radon`, and `mdotk/radon`. Fork owners must clean/delete those refs before GitHub Support can dereference pull refs and run server garbage collection.
- Show Me artifact: `tasks/artifacts/show-me-security-remediation.html`.

---

# DISPERSION indicator (2026-08-29)

Spec: `docs/indicators/dispersion.md`. Pattern: `/indicator` swarm, single shared worktree, disjoint ownership.

## Checklist

- [x] T1 Research: IB `10 Y` TRADES daily bars reach 2016-08-31 for S&P seed, sector SPDRs, VIX (XLC from 2018-06-19); migration 0061 free; fixture captured.
- [x] T2 Spec written and committed.
- [x] T3 Red tests recorded failing on missing modules (34b038e4).
- [x] T4 Implementers landed scoped commits: ingestion 94 pytest, api 259 vitest, ui 179 vitest.
- [x] T5 Full gates: vitest 814 files / 8,228 passed; pytest 8,631 passed + 24 pre-existing Flex-embargo failures; tsc 0 errors post-rebase.
- [x] T6 Backfill: IB 488/515 + Yahoo 27, 2,511 rows 2016-09-01..2026-08-28 verified in Turso; migration 61 applied.
- [x] T7 Playwright mocked spec 3 passed; live screenshots dark+light with 12 stroked paths, 0 NaN, 0 cadence claims.
- [ ] T8 Ship: branch pushed as ind/dispersion, PR open; merge deploys; then verify timer on the host.

## Review

- 2026-08-28 reading: stock +2.06, sector +2.02, VIX -0.32, BELOW THE SURFACE (gap +2.38), matching the Wellington chart.
- IB daily bars DO throttle on a 515-symbol 10 Y sweep (20 error-162 retries); the incremental 1 M run is expected to be lighter, watch the first timer fire.

# Task: Provision Chromium for the production radon-api runtime (2026-09-04)

## Dependency graph

- T1 depends_on: [] - Audit the current Python image, app-runtime mount contract, systemd API drop-in, production container, browser cache, and MenthorQ bootstrap requirements; choose the smallest durable fix.
- T2 depends_on: [T1] - Add a failing regression that proves the production API runtime can resolve and launch the Playwright Chromium revision required by the installed Python package.
- T3 depends_on: [T2] - Implement the minimal image or runtime provisioning change without adding mutable host coupling, exposing credentials, or broadening container privileges.
- T4 depends_on: [T3] - Run focused cloud/runtime tests, build the production Python image, and smoke the exact browser executable and sandboxed launch path.
- T5 depends_on: [T4] - Run full project Python verification, static checks, secret scan, and review the isolated diff before committing.
- T6 depends_on: [T5] - Commit, push, open a PR, verify exact-head CI, send the required green notification, merge, and supervise exact-main production deployment.
- T7 depends_on: [T6] - Verify the deployed API image contains the expected browser, rerun the local MenthorQ exposure request, and confirm HTTP 200 or isolate any remaining provider-auth requirement without touching IB Gateway/2FA.
- T8 depends_on: [T7] - Record production evidence, residual risks, and cleanup of only task-created worktree state.

## Checklist

- [x] T1 Establish the image/runtime and production baseline.
- [x] T2 Pin the missing-browser failure with a regression.
- [x] T3 Provision Chromium in the production API runtime.
- [ ] T4 Verify the exact production image and browser launch.
- [x] T5 Complete full verification and diff review.
- [ ] T6 Deliver through green PR CI and exact-main production deployment.
- [ ] T7 Verify the live MenthorQ exposure path.
- [ ] T8 Record final evidence and clean task artifacts.

## Review

- The Python image installs the Playwright 1.58-matched headless Chromium shell and Linux dependencies in an immutable shared path before application source is copied.
- Build-time and workflow smokes launch a page as uid 1000; the workflow also reproduces the production capability restrictions and refuses registry fallback.
- Focused cloud verification passed 132 tests with 2 declared skips after rebasing onto current main. The full Python suite passed 11,699 tests with 1 declared skip; two parallel-load timing failures passed serially.
- YAML, shell syntax, diff hygiene, and gitleaks checks passed. Docker was unavailable locally, so the exact linux/amd64 image build remains gated by PR CI.

---

# Research publication-date gate (2026-09-07)
- D1 depends_on: [] - Disable automatic publication and preserve/retract unsupported automatic rows.
- D2 depends_on: [] - Add source-literal publication-date evidence gate with red/green regressions for folder dates and copyright-only dates.
- D3 depends_on: [D2,D5] - Run focused/full verification, regenerate source map, and deliver through exact-head green CI/deployment.
- D4 depends_on: [D1,D3] - Revalidate pending output against corrected gate, resume watcher, and verify runtime.

- D5 depends_on: [] - Correct existing original-bank attribution and enforce the same rule for future generated copy and seeds; regression tests required.

- [x] D1 Automatic publication disabled; worker stopped and disabled. Unsupported automatic row backed up privately, retracted and cancelled in outbox; zero pending publication payloads.
- [x] D2 Literal source-date gate implemented; retained failing production audit now fails independently of model approval.
- [x] D5 Original-bank attribution corrected live and guarded for future rendered copy.
- [ ] D3 Full verification and release.
- [ ] D4 Corrected runtime reactivation.
- Focused verification: 233 passed, 100% pipeline/publisher/seed coverage; independent review eight probes passed after closing named-month ranges and Unicode format-character bypasses.

- Full Python follow-up: 12,273 passed, 11 failed, one skipped and 16 subtests passed. Failures are untouched process-timeout regressions; filing-forensics rerun passed unchanged (1/1). Wrapper baseline reruns are recorded in the release PR.
- Integrated current main d13f982d (marketing-only implementation changes); regenerated source map has 3,222 nodes and 6,502 edges. Research, codemap and updated public-asset checks passed 266/266; staged secret scan found no leaks.
- All ten wrapper failures passed serially against pristine d13f982d (10/10, 38.70s); five wrappers and their test module match the integrated worktree. Full-run timeout cause remains unknown; no implementation/test changes were needed.
- D3a depends_on: [D3] - Main advanced to e4eaa255 during green PR checks; integrate its independently tested loop changes, regenerate conflicting source-map artifacts, run integrated research/contract tests, and repeat exact-head CI before merge.
- D3a integrated validation passed 320/320; upstream e4eaa255 full CI and production deployment completed successfully. Research implementation is unchanged from the full-suite run.

# PR #337 conflict resolution
- C1 depends_on: [] - Merge current main in an isolated checkout; preserve research date/attribution gates and the PR's budget/freshness protections.
- C2 depends_on: [C1] - Regenerate source maps, review semantic integration, and run focused/full verification.
- C3 depends_on: [C2,C2b] - Push the resolution to PR #337 and supervise all exact-head checks to green.
- [x] C1 Resolve conflicts.
- [x] C2 Verify integrated behavior.
- [ ] C3 Push and verify CI.
- C2a depends_on: [C1] - Broader checks found nine standalone crop-review deadline failures; add a bounded lazy-initialization regression and fix before full verification.
- C2a red/green: the prior PR's standalone crop failures were deadline-initialization regressions, not unrelated fixture failures. Eleven crop cases failed before the fix, including two new budget controls; independent crop/runtime/date/publication checks passed 204/204 with no skips afterward.
- Generated source maps are fresh; focused codemap suite passed 31/31.
- C2b depends_on: [C2] - Main advanced to c8ae8fa8 during verification; integrate feed-sharing changes, regenerate combined artifacts, and verify the final web tree before pushing.
- Full Python verification passed: 12,299 tests, one skip, 16 subtests (1,262.32s). Full web: 8,529 passed and 16 timed out; unchanged affected-file/freshness rerun passed 411/411. Typecheck and staged secret scan passed.

# News sharing pull request (2026-09-07)

## Dependency graph
- T1 depends_on: [] - Verify branch, original work ownership, completed tests and CI requirements.
- T2 depends_on: [T1] - Isolate the sharing diff on current main, commit and publish a pull request.
- T3 depends_on: [T2] - Verify registered checks on the exact PR head, monitor to completion and repair failures.
- T4 depends_on: [T3] - Send the required Pushover notification, confirm acceptance and document final evidence.

## Checklist
- [x] T1 Branch and verification inspection.
- [x] T2 Focused commit and PR.
- [ ] T3 Latest-head CI green.
- [ ] T4 Notification and review.

## PR integration review
- PR https://github.com/joemccann/radon/pull/338 had 31 passing checks on f5c55ab6.
- Integrated source-date main update ff2b31f8; retained both task records and regenerated the combined code map. Sharing implementation files are unchanged. Latest merge-head checks and notification pending.

## PR #337 integration review
- Latest main c8ae8fa8 integrated; Python implementation is unchanged from the 12,299-test full run. Final combined checks passed 267 research/codemap tests and 171 sharing/freshness tests. Existing source-date and attribution guards remain intact.
- Latest main test gates passed but deployment rolled back to ff2b31f8 on research-worker stop timeout (run 34160677389); this PR conflict update does not constitute deployment.

# Repair production deployment stop deadline (2026-09-07)

## Dependency graph
- T1 depends_on: [] - Reproduce research stop timeout and inspect lifecycle budgets.
- T2 depends_on: [T1] - Add failing regression and align bounded shutdown wait with research service.
- T3 depends_on: [T2] - Run full cloud suite, shell checks, and independent review.
- T4 depends_on: [T3] - Publish repair PR, verify CI green, merge and verify production deployment.

## Checklist
- [x] T1 Run 34160677389 fails waiting for research inactive; recovery restores ff2b31f8.
- [x] T2 Regression and fix: red 3 failed / 9 passed; green 12/12.
- [x] T3 Verification: 12 focused passed, independent approval, full cloud 1821 passed / 44 failed / 7 skipped.
- [ ] T4 Production repair.

## Review
- Research-only production inactive wait now 150s, with unchanged 60s startup/other-unit waits and 180s outer mutation deadline. Canonical unit allows 120s shutdown plus cleanup.
- Independent review approved; 12 focused tests pass, including simulated 125s stop and persistent-stop exit 71. Shell syntax and diff checks pass.
- Full cloud suite completed: 1821 passed, 44 failed, 7 skipped in 435.39s. Failures are in unchanged Caddy/runtime/supervisor/Gateway test surfaces (missing local Caddy and subprocess/timing fixtures); representative supervisor TimeoutExpired(3s) reproduced on clean parent. Full log: /private/tmp/radon-deploy-final-cloud.log. Linux CI remains the authoritative release gate.

# PR #339–342 CI repair and merge queue
- T1 depends_on: [] - Inspect exact-head CI and prove cross-PR conflict dependencies.
- T2 depends_on: [T1] - Integrate current main into PR339, resolve conflicts, verify and bring its CI green.
- T3 depends_on: [T2] - Repair PR340 and verify its exact-head CI green.
- T4 depends_on: [T3] - Repair PR341 and verify its exact-head CI green.
- T5 depends_on: [T4] - Repair PR342 and verify its exact-head CI green.
- T6 depends_on: [T2,T3,T4,T5] - Merge339,340,341,342 in order; refresh each against preceding merges, regenerate source maps, repeat exact-head checks, notify once per PR, and supervise each main deployment.
- [x] T1 Four-way merge-tree audit; task-ledger and source-map conflicts form a sequential chain.
- [x] T2 PR339 preparation green.
- [x] T3 PR340 green and merged externally as 125b4655; deployment passed.
- [x] T4 PR341 preparation green: 31 successful checks.
- [x] T5 PR342 preparation green on f6092bb8.
- [ ] T6 All four merged and production verified.
- PR339 integration: both task histories retained; diff against current main remains two documentation files. CI gate/path/concurrency checks passed 87/87.

## Required-check repair replan

- T7 depends_on: [T2] - Ensure docs-only pull requests execute every branch-protected matrix check; add regression coverage without weakening gates.
- T8 depends_on: [T7] - Run focused and full suites, regenerate maps if necessary, publish and verify all required contexts before resuming T6.
- [x] T7 Missing matrix-check repair; 88 focused contracts pass, live protection unchanged.
- [ ] T8 Required-context validation and resumed merges.

Review: PR339 merge was rejected because skipped matrices emit template names rather than required shard names; no bypass attempted. PR340 notification accepted HTTP200; PR339 notification accepted before this missing-context issue was discovered, so do not send a duplicate.

Verification: full root Python 12,282 passed, 19 skipped, 16 subtests. Full cloud 1,860 passed, 7 skipped, 6 failures; five missing-Caddy failures reproduce on unchanged main, and the operator-concurrency timing test passed unchanged on serial rerun (also green on baseline).


# Large AI-cycle snapshot deadline hierarchy (2026-09-08)

- [x] T1 depends_on: [] - Measure the complete 79,078-observation production read/build and reproduce the API 503 at the old 18-second ceiling.
- [x] T2 depends_on: [T1] - Pin and implement bounded store, API, and Next deadlines with explicit headroom at each layer.
- [ ] T3 depends_on: [T2] - Run focused checks, ship through CI/deployment, and verify the full live payload returns HTTP 200.

Dependency graph: T1 -> T2 -> T3.
- [x] T4 depends_on: [] - Study verified @joemccann authored tweets and document an evidence-backed voice persona; establish whether X API access is required.
- [x] T5 depends_on: [T2, T4] - Integrate persona-based social copy rewriting with factual preservation and publisher exclusions, then verify.
Extended dependency graph: T1 -> T2 -> T3; T4 + T2 -> T5.


## Social share review
- Publisher exclusion regressions: red 7 failures, then focused 93 passing after voice integration and handle/link regression.
- Browser: 8/8 desktop/mobile cases passed; original PNG/MP4 decoding and lightbox checks retained; rewritten caption/X/canvas parity, pending controls, retries and one generation request per panel verified.
- Voice persona: docs/joe-mccann-social-voice.md, provisional evidence from 20 source items (18 older thread previews, two standalone posts). Public X profile blocked; recent authenticated timeline requested for improved calibration.
- Live generation: configured Anthropic provider returned a validated factual draft; output at /tmp/radon-voice-live-check.json. No content published.
- Full web suite coverage and TypeScript verification complete; PR creation requested and in progress.

- Concurrent workspace cleanup stashed original edits and removed registered worktrees during verification. Recovered task files from exact stash 33d9239d1344ae759c9efa9135cf0c46b91872e6 into an independent repository at /Users/joemccann/.radon/work/social-voice-20260907/repo; shared checkout and stash unchanged.
- Independent browser rerun: 8/8 passed; next typegen and tsc --noEmit passed; temporary dependency-root configuration restored.

## Social sharing PR plan
- [ ] T6 depends_on: [T3, T5] - Commit scoped changes from current main and open the PR.
- [ ] T7 depends_on: [T6] - Verify the exact PR head has its expected CI suite; monitor and repair until green.
- [ ] T8 depends_on: [T7] - Confirm normal-priority Pushover delivery and document the final PR evidence.
Dependency graph: T3 + T5 -> T6 -> T7 -> T8.

## Final local verification
- Full web coverage completed across disjoint batches: 867 files / 8,618 passing tests. Node 25 fork run and Node 24 continuation were partitioned because of workstation load; timezone-mutating suites ran separately in forks (31/31), since threads cannot change process timezone. Journal snapshot timing failure passed rerun (2/2). Manifest: /tmp/radon-social-complete-suite.json.
- Main integration: 128 focused tests passed. Browser: 8 desktop/mobile E2Es passed, real PNG/MP4 exports verified, rewritten card visually inspected. Next typegen, TypeScript, scoped ESLint and whitespace checks passed.
- Scoped changes restored to current main after unrelated workspace cleanup; independent recovery copy retained outside registered worktrees.

## AI infrastructure implementation (PR126 follow-up)
- [x] T1 Source contracts and reproducible retrieval verification. depends_on: []
- [x] T2 Versioned observations, durable storage, collectors and scheduling. depends_on: [T1]
- [x] T3 Comparable transforms, financial reconciliation, legacy preservation. depends_on: [T2]
- [x] T4 Four-view UI, source evidence, dashboard/ticker handoff. depends_on: [T3]
- [x] T5 Publication-time shadow evaluation and limitation report. depends_on: [T2,T3]
- [x] T6 Focused tests, full suites, browser verification, PR green, show-me handoff. depends_on: [T4,T5]
Graph: T1 -> T2 -> T3 -> T4 -> T6; T2 -> T5; T3 -> T5 -> T6.
Specification: docs/ai-infrastructure-plan.md; execution contract: docs/ai-infrastructure-contract.md.
Worktree: /Users/joemccann/dev/apps/finance/radon-ai-infrastructure, branch feat/ai-infrastructure-evidence.
Enabled observations must be reproducible; unentitled/experimental sources stay explicit unavailable and never fabricate values. Shadow validation ships as a reproducible evaluator; no invented history or claimed predictive validity.

### Review
- Implemented in the isolated worktree with collector, core and UI agents; all feature work reviewed and frozen.
- Verified 1,962 source observations, five issuer cash-flow mappings and 25 rolling TTM coverage points. Three of 15 indicator groups have available evidence; missing entitlements/cohorts remain explicit.
- Production build and typecheck pass; six Playwright scenarios and an actual-source capture sweep pass, with 15 screenshots and zero page errors or horizontal overflow.
- Full suites and exact-head PR CI remain release gates. No production collection, order execution, merge or deployment performed.
- Final full application Python: 12,392 passed, 19 skipped, 16 subtests passed across two disjoint partitions; all initial failures resolved and rerun green.
- Full cloud rerun: 1,873 passed, six skipped; deployment inventory/bootstrap and optional-env contracts repaired. Final focused AI/code-map/watchdog/order-worker contracts: 140 passed.
- Full root Vitest: 9,103 passed across 924 files; final TypeScript check passed. Exact-head PR checks remain pending before final handoff.
- PR346 integration: merged main00b6aa22, regenerated maps; updated application partition 11,759 passed plus one owner-doc failure, repaired with146/146 ownership/code-map/Equibles regressions. Combined application coverage now12,394 tests including the unchanged634-test partition.
- Release verification: PR346 head5fa75a9b has all applicable CI checks green, including coverage and Playwright; 15-image show-me artifact verified with seven selectors and desktop/mobile checks. Final release-head status is tracked on PR346 before notification.

### Final-head CI repair
- [x] T7 Fix the existing compose-validation producer/grep pipeline race with deterministic regression coverage. depends_on: [T6]
- [x] T8 Verify the repair, publish and supervise all latest-head checks before final notification. depends_on: [T7]
Graph extension: T6 -> T7 -> T8.
- Evidence: run34172973979 cloud al rejected the poisoned body with a spurious no-services diagnostic after printf received SIGPIPE. Preserve every compose safety gate and its three-copy parity.
- T7 red evidence: six deterministic large-body/small-pipe failures across all three Compose validators; trusted-body rejection and privileged-body acceptance both reproduced.
- T7 green evidence: 125 focused tests, nine large-body regressions, three-copy parity and shell syntax all passed. Full cloud rerun and latest-head CI remain T8 gates.
- T8 local verification: final full cloud suite 1,882 passed, six skipped after the Compose repair; latest-head CI is the remaining release gate.
- T8 release evidence: repaired head6fa1f028 passed all31 applicable checks, including both cloud shards, coverage and Playwright. All agents completed; worktree code is verified. Latest ledger-head status remains visible on PR346 and is checked before notification.
# Task: Reliability audit 2026-09-08 [COMPLETE]

# AI-cycle bounded-resume completion (2026-09-08)

- [x] T1 depends_on: [] - Reproduce the production SIGTERM and identify exhausted-budget status churn plus repeated successful empty windows.
- [x] T2 depends_on: [T1] - Add red regressions for checkpointing successful empty windows and stopping cleanly at the first exhausted budget.
- [x] T3 depends_on: [T2] - Implement clean bounded completion without changing provider/schema failure semantics.
- [ ] T4 depends_on: [T3] - Run focused checks, open the repair PR, and monitor exact-head CI.
- [ ] T5 depends_on: [T4] - Deploy, resume to terminal success, and verify final API/source coverage.

Dependency graph: T1 -> T2 -> T3 -> T4 -> T5.

## Review

- Red/green: two regressions failed before the repair and pass after it; 101 AI-cycle core/collector tests and Ruff checks pass.

## Review

- Inventory: 19 shared history routes plus eight independent regime chart implementations reviewed; all affected time and index axes use responsive tick spacing and inward endpoint anchors.
- Regression evidence: pre-fix irregular-session minimum gap was 19.8px; focused Vitest is 194/194 and TRIN Playwright is 4/4 at 1,440px and 390px.
- Full verification: 8,634/8,634 web tests, TypeScript, scoped ESLint, codemap freshness, diff check, Impeccable detection, and the held-out E2E evidence contract pass.

# Dropbox filename resilience repair (2026-09-08)
- [x] T1 depends_on: [] - Add red regressions and repair remote filename acceptance while preserving security boundaries.
- [x] T2 depends_on: [] - Add red regressions and isolate discovery failures from other scopes and validated queued work; retain actionable error health.
- [x] T3 depends_on: [T1,T2] - Independent review, focused/full Python verification and generated code map checks.
- [ ] T4 depends_on: [T3] - Commit, PR exact-head CI, merge and verify deployed queue recovery and canonical accepted output.
Dependency graph: T1 + T2 -> T3 -> T4.
User authorized fixing the live service; preserve source files, cursors, date/novelty/attribution/media gates and existing posts. No trading actions.

## Verification in progress
- Reader red/green: colon listing/download failed before repair; 38 reader/state tests passed afterward. Exact live blocked cursor validated all 69 entries with patched code in a transient read-only interpreter.
- Worker red/green: three isolation regressions and one CLI exit-code regression failed before repair; 75 runtime/pipeline tests pass. Independent review found no remaining blockers.
- Full Python suite running; no production changes before reviewed release.

## Local verification complete
- Full Python coverage: 12,406 unique tests passed, 19 skipped, 90 integration deselected, 23 subtests passed. Full loadfile run completed every other file (11,990 passed/19 skipped before interruption); remaining serial wrapper file was rerun in full using load scheduling: 634/634 passed. No failures in either batch. Logs: /tmp/radon-dropbox-full-pytest.log and /tmp/radon-dropbox-final-shard.log; collection: /tmp/radon-dropbox-all-collected.txt.
- Focused research: 263 passed, 18 skipped, 23 subtests. Touched coverage: Dropbox reader 100%, worker 97%. Independent review approved; whitespace and generated map checks pass.
- Integrated main 1090a84a (unrelated chart changes); research source unchanged from full verification. Regenerated combined source maps; 294 integrated research/codemap tests passed, 18 skipped, 23 subtests.
- Integrated main c9305ba8 (independently green loop no-op change); repair implementation remains unchanged. Combined research/codemap/new-loop regressions: 318 passed, 18 skipped, 23 subtests. Merge TypeScript, ESLint and secret scans passed on the prior chart integration.
- Integrated subsequent security release 1ab63ddf; no research implementation changed. Focused research reverified and combined maps regenerated; latest-head CI is the final integrated release gate.

# Dropbox release deployment unblock (2026-09-08)
Original repair PR355 is merged as 670dcae2 with 31 green checks. Preceding security release 1ab63ddf failed monitor startup (root-owned app tree lacks writable logs); rollback then failed stopping absent legacy watchdog timer. User-authorized live fix remains incomplete until deployment recovers.
- [x] U1 depends_on: [] - Restore narrowly writable monitor log directory in Python image, retaining immutable source ownership; red/green image regressions.
- [x] U2 depends_on: [] - Make deployment stop/recovery tolerate provably absent inactive units, with failure-closed state checks and regressions.
- [ ] U3 depends_on: [U1,U2] - Run full cloud suite and independent review; release through exact-head green CI and deployment.
- [ ] U4 depends_on: [U3] - Verify monitor and research runtime, advanced Dropbox cursor and queued work progress.
Dependency graph: U1 + U2 -> U3 -> U4.

## Review
- Independent review approved both changes and recovery plan. Use exact merged-main helper, atomically installed canonical root0755 after archiving old helper, run recovery as radon, then immediately sync canonical control-plane manifest. Preserve inventory/journal and gateway state.
- Docker regression red before log provisioning; 41 image tests passed. Actual non-root image smoke is mandatory in CI build; local Docker daemon unavailable.

- Full cloud run: 1,866 passed, 32 failed, seven skipped. Failed subset rerun on unchanged baseline: 26 passed, six failed; repaired tree: 27 passed, five failed, all requiring unavailable local Caddy. New regressions pass; Linux CI remains the integrated release gate. Logs: /tmp/radon-dropbox-full-cloud.log, /tmp/radon-dropbox-cloud-baseline.log, /tmp/radon-dropbox-cloud-confirm.log.
- Integrated unrelated ATS main release 37f18a9f; cloud and Docker implementation unchanged.

# AI-cycle production import repair (2026-09-08)

- [x] T1 depends_on: [] - Reproduce the deployed one-shot failure and confirm the encrypted Artificial Analysis key exists without reading its value.
- [x] T2 depends_on: [T1] - Add a subprocess regression for the real package invocation and correct secret-store import resolution.
- [ ] T3 depends_on: [T2] - Run focused checks, open the repair PR, and monitor exact-head GitHub CI until green.
- [ ] T4 depends_on: [T3] - Verify successful deployment, trigger the production one-shot, and confirm the Artificial Analysis result and timer health.

Dependency graph: T1 -> T2 -> T3 -> T4.

## Review
- Red: package invocation failed with `ModuleNotFoundError: secret_store`; green: 52 collector tests and the deployed-unit credential contract pass.

# Dropbox minute ingestion and publication repair (2026-09-08)
- [x] P1 depends_on: [] - Verify deployment host and current scheduling; design independent current-day discovery and prompt parsing.
- [x] P2 depends_on: [P1] - Implement 60-second current-day polling independent of review, durable parse staging and prompt review wake-up; regressions for slow review, retries and midnight rollover.
- [x] P3 depends_on: [] - Repair demonstrated numerical/source-format validation false negatives without weakening provenance, signs, units or date gates; regression tests.
- [ ] P4 depends_on: [P2,P3] - Independent review, full relevant suites, exact-head PR CI, deployment and live cadence/publication verification.
Dependency graph: P1 -> P2; P2 + P3 -> P4.
Scope: preserve private source/queue state and novelty gates. Use bounded ingestion resources; a slow model review must not block discovery or text parsing. Host migration, if needed, must preserve single-writer ownership.

## Minute-ingestion review
- Live host5.78.148.38 reports RADON_HOST_ROLE=app and IB_GATEWAY_HOST=10.0.0.4; Linux/Hetzner hostname remainsib-gateway. No host migration required.
- P3 red/green:97 numeric/date/pipeline tests pass, including25 new format/range/context cases. No newdependencies; preservegenuine unsupportedclaims/date/cropfailures.

- P2 frozen after87 ingestion/runtime tests passed; ingestion branch-aware coverage96%. Actualspawn integration demonstrates newarrivals parsed atvirtual0/60seconds while modelreviewremainsblocked. Independentreview resolvedcacheintegrity, processgroup cleanup, cancelledoutbox andaggregatehealth concerns.

- User changed verification policy: no test suites on this machine; use PR CI. Stopped local full suite at11,838 passed/19skipped withKeyboardInterrupt after906seconds; not a completed full-suite result. Integrated current maince4a9a56; preserved both task/lesson updates and regenerated maps. Final validation delegated to exact-head GitHub CI.

## 2026-09-08 — Vast.ai credential verification

### Dependency graph
- T1 depends_on: [] — Trace Profile credential save/verify path and reproduce Vast response safely.
- T2 depends_on: [T1] — Add a failing validator regression covering current Vast API behavior.
- T3 depends_on: [T2] — Implement the minimal robust Vast verification fix.
- T4 depends_on: [T3] — Run focused tests, regenerate codemap, and review the diff.
- T5 depends_on: [T4] — Commit, push, open PR, watch exact-head CI green, and send Pushover.

### Checklist
- [x] T1 Trace and reproduce without exposing secrets.
- [x] T2 Add red regression.
- [x] T3 Fix validator.
- [x] T4 Verify and document review.
- [ ] T5 PR and green CI.

### Review
- Production-safe probe confirmed Vast is reachable and returns HTTP 404 with `error=auth_error` for the stored key; no credential material was emitted.
- Validator now classifies only Vast's explicit `auth_error` payload as invalid; unrelated 404s and transport failures remain non-blocking errors.
- Focused verification: 56 pytest passed; Ruff check/format, codemap pre-commit, and diff check passed.
# Regime freshness rails everywhere (2026-09-08)

Audit: 30 regime tabs. FreshnessRail: ivrank, iv-spread, ma-ratio. Text "Next refresh": cot, ats, short. Lacking with a systemd writer (20): cri, vcg, gex, breadth, bpi, margin, straddle, cor, vixcor, vixts, dispersion, skew, skew2d, curve, credit, iei-hyg, trin, divyield, hyad, hhlev. Lacking with no scheduled writer (4, not applicable — nothing to derive from): grg (panel-triggered POST scan), streaks (computed per request), backtest, llm (AiInfrastructurePanel since #346, on-demand "Refresh snapshot"; LlmTokenIndexCard is no longer mounted).

- [x] T1 depends_on: [] - Generalise `web/lib/refreshSchedule.ts` to rule-based schedules (weekday sets, multi-slot, intraday windows, America/New_York timers); add one constant per lacking writer; `freshnessRail.ts` derives the interval from previous/next slot. Pin every constant to its OnCalendar lines by parsing the unit file in `tests/refresh-schedule.test.ts`.
- [x] T2 depends_on: [T1] - Workflow fan-out: wire `<FreshnessRail>` into each of the 20 panels with a render test asserting the rail and its countdown.
- [x] T3 depends_on: [T2] - Full vitest suite green; tsc + eslint clean. (9220 passed from web/; the 10 cwd-dependent lib/tools + scanner-scrub failures pass from repo root, 23/23.)
- [x] T4 depends_on: [T3] - Browser verification of every wired tab (chrome-cdp on local Next), screenshots. 19/20 rails rendered live with correct next-fire instants; TRIN shows its empty state locally (no samples in this environment), rail covered by its unit test.
- [ ] T5 depends_on: [T4] - PR, mergeable, CI green, Pushover `radon PR green`.
Dependency graph: T1 -> T2 -> T3 -> T4 -> T5.

# Task: Refresh the Artificial Analysis frontier basket daily [IN PROGRESS]

Keep the AI-cycle price cohort current as frontier models ship while preserving a last-known-good basket when the upstream catalog or selection contract is invalid.

## Dependency graph

- T1 depends_on: [] - Define deterministic frontier-provider selection and red regression tests from the current Artificial Analysis schema.
- T2 depends_on: [T1] - Implement catalog refresh, validation, atomic secret-store update, audit output, and idempotence.
- T3 depends_on: [T2] - Add a bounded daily systemd oneshot/timer ordered before AI-cycle collection and wire deployment inventory.
- T4 depends_on: [T2, T3] - Run focused tests, codemap generation, durability checks, and review the final diff.
- T5 depends_on: [T4] - Commit, open the PR, follow exact-head GitHub CI through green, and verify production deployment.

## Checklist

- [x] T1 Selection contract and failing regressions
- [x] T2 Refresh implementation and safe persistence
- [x] T3 Daily service/timer and deployment wiring
- [x] T4 Focused verification and review
- [ ] T5 PR, green CI, and production verification

## Review

- [x] Red import regression preceded implementation; 24 worker durability tests cover frontier selection, stable IDs, anti-churn, catalog truncation, state corruption, write failures, idempotence, and encrypted-store persistence.
- [x] Focused validation: 278 affected Python tests, 546 cloud/service topology tests, 133 service-health Vitest tests, and 31 codemap tests passed.
- [x] Live 644-row catalog resolved all eight providers deterministically without exposing credential material.

# AI-cycle Turso write retry (2026-09-08)

- [x] T1 depends_on: [] - Reproduce the production backfill crash and identify the timed-out Turso status write.
- [x] T2 depends_on: [T1] - Add red regressions for bounded transient retries, non-transient fail-fast behavior, and idempotent status writes.
- [x] T3 depends_on: [T2] - Implement bounded retries, idempotent status writes, and production-measured snapshot paging that remains below Hrana's response cap.
- [ ] T4 depends_on: [T3] - Run focused verification, open the repair PR, and monitor exact-head GitHub CI.
- [ ] T5 depends_on: [T4] - Verify deployment, resume the production backfill, and audit provider counts/ranges and the live payload.

Dependency graph: T1 -> T2 -> T3 -> T4 -> T5.

---
# Task: Dropbox research rich text [IN PROGRESS]

## Dependency graph

- T1 depends_on: [] - Trace research content, shared rendering, and CI coverage.
- T2 depends_on: [T1] - Render Dropbox Markdown in feed and lightbox, preserving source structure and existing plain-text posts.
- T3 depends_on: [T1] - Add component and browser regressions for formatting, source links, and safe rendering.
- T4 depends_on: [T2, T3] - Publish PR; wait for all exact-head CI checks, inspect browser screenshots, and send the required notification.

## Checklist

- [x] T1 Trace source metadata, normalization, feed, lightbox, and existing Markdown renderer.
- [x] T2 Implement scoped rich-text rendering with preserved Markdown whitespace in feed and lightbox.
- [x] T3 Add 6 component regressions and desktop/mobile Playwright assertions. Run suites only on GitHub runners.
- [ ] T4 Review changes and complete exact-head CI and visual verification.

## Review

- Static review found no blocking issues; research typography uses Clear tokens and the existing safe Markdown renderer.
- PR: https://github.com/joemccann/radon/pull/367. RED: 4 expected Markdown regressions failed on 0c7491ab2b1baf97dc21f7d4547dcbb3579bf287 (CI run 34282990355).
- GREEN: a6035206 passed 31 applicable checks, including all 6 component regressions, 67 browser checks, and the demo transport check (CI run 34283301911). Reviewed 4 desktop/mobile screenshots; added scoped italic synthesis after visual review. Final-head verification pending.
- Regenerate codemap from a clean index export so local generated sources do not enter the committed graph.

---
# Task: Research and social copy in Joe's voice [IN PROGRESS]

## Dependency graph

- T1 depends_on: [] - Inspect authored copy paths and documented @joemccann voice; isolate unrelated work.
- T2 depends_on: [T1] - Enforce human voice and no em dashes in research generation/publication.
- T3 depends_on: [T1] - Enforce no em dashes in existing research renderings and every social-share output; strengthen voice instructions.
- T4 depends_on: [T2, T3] - Add regressions, publish PR, verify exact-head CI and browser artifacts, and notify.

## Checklist

- [x] T1 Read voice profile and source samples; create isolated worktree.
- [x] T2 Research generation and publication.
- [x] T3 Web rendering and social exports.
- [ ] T4 GitHub-runner validation, visual verification, and delivery.

## Review

- No local test suites. Original PDF/chart evidence remains intact; changes apply to authored text. Preserve all financial grounding gates.
- PR: https://github.com/joemccann/radon/pull/378. RED: GitHub run 34291166890, head 3a07ebdb, reproduced four voice-output failures and additional research/share regressions before production changes.
- Added 63 unit regressions and browser assertions. Static review added nested URL-parentheses coverage; promoted social browser tests to CI and removed their held-out ledger entry.
- GitHub run 34291510793 exposed sentence commas being captured as part of numeric claims after punctuation normalization. Restrict grouping commas to those followed by a digit; retain changed-value/sign rejection.
- GREEN: 0067a67a passed all 31 applicable checks (run 34291738638), including 75 browser checks and the demo check. Reviewed research feed/lightbox and social export screenshots. Final merge-head verification pending after main advanced.

---

# Task: Radon AI conversation experience (2026-09-08)

## Approved anchored options chain (2026-09-15)

Plan and review: `tasks/options-chain-anchor.md`.
Dependency graph: T1 -> T2; T1 -> T3; T1 -> T4; T2 + T3 + T4 -> T5 -> T6.
- [x] T1 depends_on: [] - Approved design and isolated implementation checkout.
- [x] T2 depends_on: [T1] - Stable anchor and desktop implementation.
- [x] T3 depends_on: [T1] - Mobile implementation and unit regressions.
- [x] T4 depends_on: [T1] - Browser regression coverage and CI selection.
- [x] T5 depends_on: [T2,T3,T4] - Integration review and PR #449.
- [ ] T6 depends_on: [T5] - Exact-head CI, visual evidence and notification.

### Review
PR https://github.com/joemccann/radon/pull/449; initial runner screenshots reviewed, CI repair loop active. Details in tasks/options-chain-anchor.md. No local suites or production deployment.


## Specification
Replace the anonymous composer-only overlay with a readable Clear conversation workspace: explicit identity and close/new controls; task-specific editable starters; honest current-page context; multiline composer with visible attachment and model controls; cancellable generation, retry/edit recovery, retained session on close; readable transcript and inspectable tool evidence. Preserve order-risk approvals, quote provenance, auth and root socket ownership. Do not persist sensitive conversation data to disk. Desktop is a spacious centered workspace, mobile fills the viewport with accessible controls. No local test suites; GitHub CI owns Vitest and Playwright execution.

## Dependency graph
- T1 depends_on: [] - Inspect existing lifecycle, constraints and authoritative AI UX guidance.
- T2 depends_on: [T1] - Implement composer improvements (parallel agent).
- T3 depends_on: [T1] - Implement conversation layout, lifecycle and scoped styles (primary).
- T4 depends_on: [T2,T3] - Regression tests and browser coverage; review semantics and order safety.
- T5 depends_on: [T4] - Generate codemap, publish PR, resolve conflicts, await exact-head CI, inspect browser artifacts and notify.

## Checklist
- [x] T1 Source and design audit; isolated worktree preserves existing staged changes.
- [x] T2 Composer implementation.
- [x] T3 Conversation implementation.
- [x] T4 Regression coverage and independent lifecycle review; all 8 Vitest shards and 10 browser scenarios pass on 0b54eecc.
- [x] T5 Exact-head CI and visual evidence, PR delivery.

## Review
PR #379: first GitHub run completed all 10 new browser scenarios; eight desktop/mobile light/dark screenshots inspected. Three unit assertions exposed missing whitespace across a heading line break; fixed the accessible text in the component. Remaining exact-head verification tracked in https://github.com/joemccann/radon/pull/379. No local suites executed. Sources: https://developer.apple.com/design/human-interface-guidelines/generative-ai and https://pair.withgoogle.com/guidebook-v2/chapter/explainability-trust/ . No system prompt or model-policy changes planned.

---

# NOAA signed-temperature repair (2026-09-08)

- [x] T1 depends_on: [] - Reproduce winter-window rejection against the authoritative NOAA daily-summary response.
- [x] T2 depends_on: [T1] - Add a negative-temperature regression and parse signed finite weather measurements.
- [ ] T3 depends_on: [T2] - Run focused checks, ship through exact-head CI/deployment, resume, and audit NOAA coverage.

Dependency graph: T1 -> T2 -> T3.


## Testing-weekend audit 2026-09-10 [COMPLETE]

Dependency graph: T1 -> T2 -> T3 -> T4.

- [x] T1 — Verify dedicated runner, clean branch, ledger base and toolchain. depends_on: []
- [x] T2 — Run serial full gates and delta determinism checks. depends_on: [T1]
- [x] T3 — Audit delta, standing candidates, skips, ratchets and CI reachability. depends_on: [T1,T2]
- [x] T4 — Append audit evidence, commit and push dated branch. depends_on: [T3]

### Review
- [x] No new finding; existing T-490 and T-488 reproduced in isolation.

## Testing-weekend remediate 2026-09-10 [COMPLETE]

Dependency graph: T1 -> T2 -> T3.

- [x] T1 — Reconcile all append-only audit sections and reduced-scope P0/P1 eligibility. depends_on: []
- [x] T2 — Reverified T-490 as blocked by `.codex` filesystem policy; T-488 remains operator-only. depends_on: [T1]
- [x] T3 — Append evidence, commit, and push; closing gates are inapplicable while T-490 remains deterministically red. depends_on: [T2]

### Review
- [x] Renderer write returned `PermissionError`; portable-render contract remains 52 passed / 21 failed.

## Ornn data diligence (2026-09-09)

Research only; preserve existing work. Deliver a comprehensive cited Markdown report for Radon's AI/LLM indicators and a purchase recommendation. No subscriptions, external messages, code changes, or local test suites.

Dependency graph: T1 -> T2; T1 -> T3; T2 + T3 -> T4 -> T5.

- [x] T1 — Read screenshot, repository instructions and code map; define scope. depends_on: []
- [x] T2 — Verify Ornn pricing, methodology, APIs, licensing, coverage and history against primary sources. depends_on: [T1]
- [x] T3 — Map current Radon LLM/AI indicators and compare alternative sources independently. depends_on: [T1]
- [ ] T4 — Synthesize use cases, limitations, purchase decision and pilot design into a cited research artifact. depends_on: [T2, T3]
- [ ] T5 — Review material claims, citations, recommendation and artifact completeness. depends_on: [T4]

### Review
Pending.

# Market Ear image source captions (2026-09-10)

Preserve explicit per-image provider attribution (for example Ramp) from Market Ear article HTML through storage/API and render it in the existing image caption/footer treatment. Do not invent attribution or replace the article publisher. Keep Dropbox provenance intact. Work in isolated `/tmp/radon-market-ear-sources`; no local test suites.

Dependency graph: T1 -> T2; T1 -> T3; T2 + T3 -> T4 -> T5.

- [x] T1 depends_on: [] - Inspect extraction, persistence, API and caption contracts.
- [x] T2 depends_on: [T1] - Add source extraction and persistence regression coverage and implementation.
- [x] T3 depends_on: [T1] - Add source rendering/API regression coverage and implementation.
- [ ] T4 depends_on: [T2, T3] - Review integration, regenerate maps, publish PR and verify exact-head CI and browser artifacts.
- [ ] T5 depends_on: [T4] - Record review and send confirmed green notification.

## Review
PR #393; regression head d233c7a9 after merging latest main. GitHub CI run 34493441591. No local suites. Per-image captions use explicit figure credit text, persisted with the exact public image URL. Additive migration 0073 preserves legacy/default empty metadata.

# Social Compose on X availability (2026-09-09)

## Specification
Compose on X must immediately open X's composer using the current sanitized caption, independent of voice rewriting, chart rendering, PNG preparation, and video export. When the rewrite finishes, subsequent clicks use the updated caption. Media exports retain their readiness checks. Opening a composer must not publish anything automatically.

## Dependency graph
- T1 depends_on: [] - Inspect loading state and record regression contract.
- T2 depends_on: [T1] - Add unit regressions for immediate Compose and asynchronous caption updates.
- T3 depends_on: [T1] - Add browser regression with delayed rewrite/preview and captured composer navigation (parallel agent).
- T4 depends_on: [T2,T3] - Publish failing regression head to GitHub CI, then apply minimal fix.
- T5 depends_on: [T4] - Await exact-head green CI, inspect browser screenshots, document evidence and notify.

## Checklist
- [x] T1 Source and user screenshot inspected; isolated worktree protects unrelated working changes.
- [x] T2 Unit regression.
- [x] T3 Browser regression.
- [x] T4 Red CI and minimal fix.
- [ ] T5 Green CI and visual verification.

## Review
No local test suites. Change only Compose availability; retain caption and media generation behavior.

- RED: GitHub CI run 34437337990, head da04fa6432459b320d36718b38b40c437d6be773, Vitest shard 4/8: 2 intended failures (missing Compose link), 1,332 passed. Both new loading-state assertions reproduce the missing href.
- Fix: remove the voice-loading condition from the X intent href. Existing buildXShareUrl continues to sanitize captions; media readiness and caption editing behavior remain intact. Browser tests capture real mocked popup navigation during voice/PNG waits at 1440px and 393px.

# PR 393 CI integration repair (2026-09-10)

Dependency graph: T1 -> T2 -> T3.
- [x] T1 depends_on: [] - Inspect current-head CI and check mergeability against main.
- [x] T2 depends_on: [T1] - Update source contracts for downloader options and retained share attribution.
- [ ] T3 depends_on: [T2] - Push and verify exact-head GitHub CI and browser artifacts; primary handles merge.

## Review
No local suites. CI 34494032484 reproduces outdated download signature matching. Main 1998fb77 merges cleanly.

# Form focus highlight audit (2026-09-10)

Specification: one visible, aligned keyboard focus indicator per field, around the complete visual control (including currency prefixes). Audit shared/composite controls across Clear light/dark and desktop/mobile. Preserve native semantics and all order behavior; use mocked browser data. No local suites.

Dependency graph: T1 -> T2; T1 -> T3; T2 + T3 -> T4 -> T5.
- [x] T1 depends_on: [] - Audit CSS cascade and composite control ownership; record scoped findings.
- [x] T2 depends_on: [T1] - Add browser regressions and screenshot artifacts on GitHub runners.
- [x] T3 depends_on: [T1] - Apply minimal shared CSS fix with focused contract coverage.
- [ ] T4 depends_on: [T2,T3] - Publish PR, capture red/green CI, resolve conflicts, await every exact-head check.
- [ ] T5 depends_on: [T4] - Inspect browser screenshots, record results, send confirmed green notification.

## Review
Isolated worktree /tmp/radon-focus-audit-20260910 protects existing user changes.
- RED: PR #397 head d1802ea6948a49979e4cb0a6d9c63a52464a00c2, GitHub CI 34504342646: all eight new browser cases reproduce inner outline solid instead of none; 88 existing browser cases passed. All eight Vitest shards and coverage checks passed.
- Repair: shared Clear selectors assign rings to six active composite wrappers; command palette ring stays inset. Instrument search inherits the standard outline. No financial behavior or local suites changed.
- CI 34505111914 at 43bbc58c passed the original ownership assertions and 88 existing browser cases; exposed palette inset precedence after production CSS grouped selectors into :is(), plus a modal animation changing offsetParent in the geometry test. Use a local offset custom property and compare settled parent-relative bounds.
- Exact-head green CI and browser screenshot review pending.

## PR #394 final integration and freshness repair (2026-09-10)
- [x] T1 (depends_on: []): Inspect collector ingestion, provenance, and upstream changes.
- [x] T2 (depends_on: [T1]): Resolve main integration and add CI regressions for default live collection and dated offline imports.
- [x] T3 (depends_on: [T2]): Repair freshness ownership and update source operations documentation.
- [ ] T4 (depends_on: [T3]): Verify every applicable exact-head GitHub check, then hand off merge to the sweep coordinator.
Dependency graph: T1 -> T2 -> T3 -> T4.

### PR #394 integration review
- Regression-first GitHub run `34507551064`, scripts-ac job `102973431318`: 4 failed / 2215 passed. Default fixture replay and undated imports reproduced; the prior snapshot test also depended on wall-clock time.
- Default collection now uses live HTML with no fixture fallback; explicit imports retain verified capture time and actual ingestion time separately. Snapshot tests use a fixed ingestion clock.
- All suite execution is delegated to GitHub runners. Final exact-head CI remains pending before coordinator merge.

---

# PR 395 final integration (2026-09-10)

Dependency graph: T1 -> T2 -> T3.
- [x] T1 depends_on: [] - Merge current main, preserve task histories, review signed and missing historical prices.
- [x] T2 depends_on: [T1] - Register route-level historical price regression and screenshot artifacts in CI.
- [ ] T3 depends_on: [T2] - Verify final integrated-head checks and inspect desktop/mobile screenshots before parent merges.

## Review
No local suites. Review confirms quantity-weighted prices preserve zero and signed premiums, reject incomplete executions, and label collapsed journal fills aggregate. Generated codemap conflicts are regenerated from the merged source.

- Integration verification: head 4d92842e passed every applicable check; GitHub CI 34507570000 ran 99 browser regressions plus one demo test. Desktop 1280px/mobile 393px full `/orders` screenshots reviewed (artifact 10164611383). Reintegrating main 722b1121 for the final merge gate; no local suites.

- PR #394 intermediate integration `892f24b1`: 31 applicable checks green, source shard 2225 passed, browser 96 passed plus one demo. Final integration includes historical-fill PR #395 at `aae5e1e5`; repeat exact-head checks before merge.

# Task: Testing remediation 2026-09-16 [COMPLETE]

Reduced-capability rung (`RADON_WEEKEND_REDUCED=1`): remediate verified P0/P1
findings only; T-495 is P2 and remains out of scope.

## Dependency graph

- T1 depends_on: [] - Reconcile this cycle's audit finding and every standing P0/P1 status.
- T2 depends_on: [T1] - Record the reduced-scope remediation decision and preserve full-gate evidence separately.

## Checklist

- [x] T1 T-495 is P2; no current source-actionable P0/P1 finding exists.
- [x] T2 T-488 remains operator-only after three genuine attempts; remediation decision is append-only.

## Review

- [x] No P2 substitute was implemented on the reduced rung.
- [x] Detached closing stage died without its `DONE` sentinel; no gate count is claimed.
## Chain-first desktop implementation B (2026-09-16)

User selected B and authorized implementation, PR, and CI verification. Work isolated from unrelated edits in /private/tmp/radon-chain-first-20260916.

Dependency graph: T1 -> {T2, T3, T4} -> T5 -> T6.

- [x] T1 depends_on: [] Inspect selected prototype, current source, instructions, and establish isolated branch.
- [x] T2 depends_on: [T1] Compact desktop shell to 48px and retain search, navigation, theme, freshness and sync.
- [x] T3 depends_on: [T1] Implement instrument sidebar and viewport-filling chain with readable 40px rows, compact toolbar, preserved builder and mobile.
- [x] T4 depends_on: [T1] Add focused behavior/layout regression coverage for density, navigation, builder and responsive themes.
- [x] T5 depends_on: [T2, T3, T4] Visually inspect real rendered application; inspect changes and regenerate codemap. No local test suites.
- [ ] T6 depends_on: [T5] Commit, create PR, watch all applicable GitHub checks on exact head until green, send required Pushover notification.

Acceptance: at least 12 full strike rows at 1280x720 and 14 at 1440x800 without a staged ticket; no document horizontal overflow; underlying and held spread quotes remain distinct; expiry, side, strike, recenter and instrument navigation work; staged ticket remains usable; mobile and both themes preserved.

### Review
Implementation B rendered with isolated broker/API fixtures. Browser geometry: 14 complete strikes at1440×800,12 at1280×720,first quotes y156,40px rows; no document horizontal overflow at1440,1280,1024 or393px. Staged ticket and mobile visually inspected; light/dark captures recorded locally. TypeScript and focused ESLint static checks pass. Added 8 browser cases and 8 sidebar unit regressions; execution pending GitHub CI. No local test suites run.

PR: https://github.com/joemccann/radon/pull/463. Initial head b04ff560 passed all 8 Vitest shards and all Python shards; browser checks still running. Additional direct browser review verified separate underlying120.00/held spread0.85, fixed the feed disclosure stacking above the chain, and added pointer hit-testing plus Sync actionability to browser coverage.

Initial browser run:114 passed, including all8 new chain cases. Demo assertion exposed duplicate canonical data-integrity markers on the new hidden disclosure trigger; use a disclosure-specific data-feed-integrity attribute so the existing demo regression remains unchanged. Latest exact-head verification continues in PR463.

## Consistent instrument workspace (2026-09-16)

Extend selected Chain-first B across the same instrument's Position, News, Ratings, Seasonality, Company, 13F, Filings, Commands and Book & trade. Keep the 48px global bar, persistent instrument context, full remaining desktop canvas and clear active navigation. Preserve mobile, calculations, quote provenance, order-risk gates and URL/keyboard navigation.

Dependency graph: T1 -> {T2,T3,T4} -> T5 -> T6.
- [x] T1 depends_on: [] Inspect merged B, instructions and codemap; isolate branch from unrelated work.
- [x] T2 depends_on: [T1] Generalize persistent desktop sidebar, active navigation and compact shell across instrument views.
- [x] T3 depends_on: [T1] Extend full-height main canvas and consistent deck toolbar/content spacing; retain usable Book & trade and mobile.
- [x] T4 depends_on: [T1] Add cross-view navigation/layout regressions and preserve existing chain, demo and order coverage. Eight isolated Playwright cases cover both themes, two laptop sizes, mobile, signed held spread quotes, actual position risk review, every reference deck, news empty/error/recovery, and persistent canvas geometry. CI selection and screenshot artifacts updated; syntax inspection passes, execution remains on GitHub runners.
- [x] T5 depends_on: [T2,T3,T4] Visually verify representative real rendered views in both themes, laptop and mobile; static checks and generated codemap.
- [ ] T6 depends_on: [T5] Commit, create PR, follow every applicable CI check on exact head through green and deliver Pushover notification.

Review: desktop Position, News and Book & trade visually verified at 1440x800 and 1280x720 in both themes; mobile Position/News verified at 393x852 in both themes. Persistent 208px sidebar, 48px header, 56px view toolbar and internal content scrolling; no document horizontal overflow. TypeScript and focused ESLint pass; codemap regenerated. Added 8 cross-view browser cases, active-navigation unit coverage and CI screenshot artifacts. No local test suites; GitHub runners execute suites. Browser verification uses isolated API/socket fixtures and never submits live orders. Exact-head CI and Pushover pending PR creation.

## Resolve and merge PR466 (2026-09-16)
Dependency graph: T1 -> {T2,T3} -> T4 -> T5.
- [x] T1 depends_on: [] Inspect PR and isolate head from unrelated local edits.
- [x] T2 depends_on: [T1] Merge current main and resolve conflicts preserving page-title behavior and current base changes.
- [x] T3 depends_on: [T1] Review title contract/integration risks and applicable validation.
- [ ] T4 depends_on: [T2,T3] Push resolution, verify all applicable CI on exact head green, send Pushover.
- [ ] T5 depends_on: [T4] Merge the verified head and confirm merged state.
Review: resolved tasks/todo conflicts by retaining both task records; retained main generated code maps under nightly ownership. Page-title route metadata preserved; added own-key registry and matching Options symbol validation with focused regressions. TypeScript passed before final helper hardening; commit hooks recheck final source. Suites execute only on GitHub. Exact-head CI and merge pending.

## Performance TWR regression (2026-09-16)

Dependency graph: T1 -> {T2,T3} -> T4 -> T5.
- [x] T1 depends_on: [] Isolate current main and record screenshot symptoms; preserve unrelated work.
- [x] T2 depends_on: [T1] Trace NAV/flow sources and recent builder changes against actual available incident data.
- [x] T3 depends_on: [T1] Inspect frontend null/degraded contract and regression coverage independently. Added isolated browser incident-to-repaired refresh coverage using actual historical flow amounts, full date window, positive TWR, return count, suppressed/drawn curve and warning removal; promoted payload spec to CI with screenshot artifacts. No frontend logic change or local suite execution.
- [x] T4 depends_on: [T2,T3] Implement minimal evidence-backed repair and regressions; inspect rendered recovery without relaxing financial integrity.
- [ ] T5 depends_on: [T4] Publish PR, verify all exact-head GitHub checks green, notify and record results.

Review: GitHub red phase9e517457 reproduced lost flow (expected80000,actual0) in test_nightly_statement_preserves_verified_historical_external_flows. Repair retains historical observed flows only with per-session coverage, statement overrides its interval including zeros, and unknown evidence remains gated. Added18Python cases plus a browser degraded-to-recovered refresh case; no production UI gating changes. Static compilation and diff checks pass. Operational no-fetch preview restored184returns/0suspects; production rebuild inprogress. Final exact-head CI and screenshot review pending. No local test suites; all suite execution on GitHub. Never infer deposits into canonical returns without source evidence.

Incident evidence: production disk payload generated2026-09-16T12:31:06.516Z, flex_from_file, nav_as_of2026-09-15, empty_verified flows,185NAV/184subperiods/182returns/two suspect sessions. Retained ledger has actual Jan13 deposit80007.13 and Feb6 transfer655497.16; residual candidates are not deposits. Latest nightly statement coversSep15only and has both flow sections withzeroentries. Initial regression commit9e517457 published inPR468 forGitHub red/green evidence.

## CI performance remediate 2026-09-17

Dependency graph: T1 -> T2 -> T3.

- [x] T1 depends_on: [] Resume `ci-performance/2026-09-17`, acquire the dedicated runner lock, and confirm the audit has no P0/P1 source-actionable CIP finding under `RADON_WEEKEND_REDUCED=1`.
- [x] T2 depends_on: [T1] Focused protected CI contracts passed (119); detached serial baseline had no DONE sentinel and is not counted as green.
- [x] T3 depends_on: [T2] Appended the reduced-rung remediation verdict for commit and push.

Review: no eligible P0/P1 source change; no protected CI or deploy rail changed.

## Testing audit 2026-09-17

- [x] T1 Audit delta `fe96fdac..HEAD`; depends_on: []
- [x] T2 Run CI-gated suite and determinism sweeps; depends_on: [T1]
- [x] T3 Append ledger and findings, then commit audit evidence; depends_on: [T1, T2]
- Review: one P2 filed as T-495; no production source changed.

## Testing remediate 2026-09-17

- [x] T1 Reconcile all verified P0/P1 findings; depends_on: []
- [x] T2 Reverify standing P1 T-490 and record operator action; depends_on: [T1]
- [x] T3 Append remediation result and commit it; depends_on: [T1, T2]
- Review: reduced scope excludes T-495 (P2); T-490 remains blocked by the
  runner's `.codex` filesystem policy and T-488 remains operator-only.

## AI industry B production implementation (2026-09-17)
Plan: `tasks/ai-industry-plan.md`. Dependency graph: T1 -> T2,T3,T4; T2+T3+T4 -> T5 -> T6. See plan for depends_on/checklist and review. Isolated worktree; no local suites.

## Substantive nightly publication (2026-09-17)

Only open nightly PRs for a net substantive change against the target base. Empty commits, audit logs, task bookkeeping and timestamp-only generated artifacts do not qualify. Preserve real documentation, tests, fixes and CI experiments, normal no-op reporting, audit checkpoints and resumable substantive PRs. All suites run on GitHub runners.

Dependency graph: T1 -> {T2,T3,T4} -> T5 -> T6.
- [x] T1 depends_on: [] Isolate current main, inspect shared delivery and inventory scheduled PR publishers.
- [x] T2 depends_on: [T1] Add deterministic substantive-diff gate and guarded publication with behavioral regression coverage.
- [x] T3 depends_on: [T1] Align all five canonical skills and generated provider prompts with valid no-op completion and guarded publication.
- [x] T4 depends_on: [T1] Cover additional scheduled publishers, including codemap semantic changes, and enforce shared policy at runner boundaries.
- [x] T5 depends_on: [T2,T3,T4] Review integration and failure handling; static validation and publish PR.
- [ ] T6 depends_on: [T5] Verify every applicable GitHub check on the latest head green, notify and record evidence.

Review: six nightly PR publishers covered by a shared net-diff classifier and guarded publisher; five agent loops also intercept direct gh creation. Known audit ledgers/tasks and timestamp-only codemap changes do not qualify; real docs/tests/config/source and graph changes remain eligible. Five canonical skills and 16 generated manuals accept explicit no-op completion. Rolling-issue checkpoints and open findings survive report pruning; security state remains private. Python/shell syntax, generated prompt synchronization and diff checks pass. Independent review completed. PR #479 initial head fe587084 passed the nightly regression shard (2109 passed, 1 skipped); CI ownership check required the operations runbook update, now included. Publisher error-path coverage expanded before final exact-head CI. Final CI and notification pending. No local test suites.

## Vol/Skew MR unknown skew investigation (2026-09-17)
Dependency graph: T1 -> T2 -> T3.
- [x] T1 depends_on: [] Trace live snapshot and upstream response against parser contract.
- [x] T2 depends_on: [T1] Correct verified root cause, preserve signal gates, add regressions.
- [ ] T3 depends_on: [T2] PR and exact-head GitHub verification; report cause and resolution.

Review: production snapshot at 2026-09-17T16:37:26Z had 90/90 unknown, no row errors. Unparameterized UW returned HTTP 200 empty history. Corrected expiry/delta request returned six sessions each for NVDA, AMAT, SNPS. Regression suites and UI screenshots delegated to GitHub CI; no local suites.

## Safe page errors (2026-09-17)
Dependency graph: T1 -> T2,T3,T4 -> T5 -> T6.
- [x] T1 depends_on: [] Inventory error sources/rendering and established error UI.
- [x] T2 depends_on: [T1] Central safe error presentation and transport parsing, regressions.
- [x] T3 depends_on: [T1,T2] Fix scanner actions/rendering; retain last good data; browser regressions.
- [x] T4 depends_on: [T1,T2] Audit all route families and remediate unsafe error surfaces.
- [x] T5 depends_on: [T2,T3,T4] Review coverage, visually verify browser error states in CI.
- [ ] T6 depends_on: [T5] PR, exact-head green checks and notification.

Review: PR #480; GitHub first run reproduced stale copy contracts and over-broad diagnostic filtering. Preserved known service retry guidance, admin healthy measurements, broker margin rejection details and financial numbers; updated intentional user-copy expectations. CI scanner recovery cases pass at 390px and 1440px; both screenshots reviewed with retained results and readable inline retry. No local suites. Final exact-head checks and notification pending.

## 2026-09-17 Vol/Skew header info bubbles
- [x] T1 Add concise calculation-aligned help to five existing SortTh headers. depends_on: []
- [x] T2 Add focused interaction and browser coverage, including sort isolation. depends_on: []
- [ ] T3 Publish PR, await exact-head CI, inspect browser screenshots. depends_on: [T1, T2]
Dependency graph: T1 + T2 -> T3. Existing Clear design and shared InfoTooltip; no calculation or layout changes.

## 2026-09-17 Toast-only error presentation
- [x] T1 depends_on: [] Review recent error commits and isolate from unrelated local work.
- [x] T2 depends_on: [T1] Implement shared toast error presentation, preserving safe copy, retry and stale-data guidance.
- [x] T3 depends_on: [T1] Audit/migrate scanner, research, dashboard and indicator errors.
- [x] T4 depends_on: [T1] Audit/migrate trading, admin, profile, setup and assistant errors.
- [x] T5 depends_on: [T2,T3,T4] Review full application inventory, document explicit exceptions, add regression/browser coverage.
- [ ] T6 depends_on: [T5] Create PR, verify exact-head GitHub CI, inspect visual artifacts, send green notification and open show-me report.
Dependency graph: T1 -> {T2,T3,T4} -> T5 -> T6.
Validation: no local test suites; GitHub runners execute unit and browser suites. Preserve trading gates, safe error formatting, existing retry actions and cached-data provenance. Fatal framework fallback pages and operational diagnostic records must be explicitly inventoried.

### Toast error audit review
- Three-agent component audit and independent shared-presenter review complete. Fatal framework fallback, financial safety and stored diagnostic exceptions documented in docs/reviews/2026-09-17-toast-errors.md.
- Regression coverage includes scanner retained-data retry, persistent dismissal/recovery, shared stacking, dialog keyboard access, order field validation, watchlist failures and AST prevention of inline error surfaces.
- Local suites intentionally not run; GitHub exact-head CI and screenshot review pending.

## AI Industry capitalization (2026-09-17)
- [x] T1 Audit all application labels; depends_on: []
- [x] T2 Normalize labels and existing regression expectations; depends_on: [T1]
- [ ] T3 Verify exact PR head in GitHub CI; depends_on: [T2]
Dependency graph: T1 -> T2 -> T3.
Review: Copy-only change; no local suites. CI includes AI Industry browser screenshots and navigation assertions.

## 2026-09-17 AI metric clarity and line history
Dependency graph: T1 -> {T2,T3,T3b} -> T4 -> T5.
- [x] T1 depends_on: [] Isolate latest main and inspect metric definitions and chart gap logic.
- [x] T2 depends_on: [T1] Replace ambiguous series labels and internal selector identifiers with descriptive names.
- [x] T3 depends_on: [T1] Render token history as a line while preserving meaningful data gaps; add regression coverage.
- [x] T3b depends_on: [T1] Refine observation card hover/focus and add per-metric info bubbles.
- [ ] T4 depends_on: [T2,T3,T3b] Integrate, static-check, create PR and run GitHub verification.
- [ ] T5 depends_on: [T4] Verify exact-head checks and screenshots, notify and record results.

Review: descriptive residual-model labels, display-only percentages, eight metric info bubbles and stable hover/focus surfaces. Sampled token histories connect at normal observation intervals and preserve larger gaps. TypeScript and Impeccable detector pass; suites and screenshots run on GitHub only.
# AI Industry source and chart audit (2026-09-17)

Dependency graph: T1 -> T2 -> T3 -> T4 -> T5.
- [x] T1 depends_on: [] Inventory every source, observation, transformation and rendered chart; inspect live coverage.
- [x] T2 depends_on: [T1] Verify primary sources and research longer historical archives; document source-by-source findings.
- [x] T3 depends_on: [T2] Add failing regressions, correct ingestion/presentation, backfill verified history and resolve empty market context.
- [x] T4 depends_on: [T3] Run focused/full verification and inspect desktop/mobile screenshots.
- [x] T5 depends_on: [T4] Publish PR #512; registered expected GitHub checks.
- [ ] T6 depends_on: [T5] Verify all applicable exact-head checks are green; final evidence recorded on PR #512.

Review: audited 16 registered sources, 18 indicators and empty legacy index;
356,644 baseline vintages / 3,342 comparable series. Production append-only
backfill: 301 vintages; initial 679,532-byte snapshot at 2026-09-18T05:21:05Z,
followed by four additional reviewed NVIDIA quarters back to January 2024.
Final production snapshot: 680,537 bytes at 2026-09-18T05:33:19Z.
134 focused Python tests including 12 final audit regressions, 515 systemd tests,
12 Playwright tests, TypeScript and scoped ESLint passed; desktop/mobile visuals
inspected. Full cloud suite interrupted after 177 passed / 22 skipped; full
Python run interrupted after 11,427 passed / 19 skipped / 15 unrelated failures
in model-ladder, research-runtime and vision-cascade tests (local subscription
discovery). Full Vitest: 979 files / 9,734 tests passed. Exact-head CI recorded
in PR before handoff.

## Testing remediation 2026-09-18 (reduced P0/P1)
- [x] T1 depends_on: [] Read rolling issue #83, verify runner markers and branch.
- [x] T2 depends_on: [T1] T-498: reproduce with synthetic subscription files; isolate three suites; verify both outer environments.
- [ ] T3 depends_on: [T1] T-496: red/green all-leg P&L measurement and mocked browser display.
- [x] T4 depends_on: [T2] Run serial full gates three times; preserve terminal results, including red gates.
- [ ] T4b depends_on: [T1] Finish legacy P0/P1 reconciliation and T-247 remediation.
- [ ] T5 depends_on: [T4] Persist complete carry-forward issue report and publication classification.

### Testing remediation review (2026-09-18)
- T-498: 15 red before isolation; 117 green with/without outer canary credentials.
- T-496: 6 mutation reds; 37 focused greens; final full Vitest 9,739 passed x3.
- Root pytest: 13,425 passed / 21 T-490 failures x3. Cloud: 1,870 passed / 4 T-488 failures x3. Both failure lists stable.
- All started stages finished. Browser sandbox refusal and unfinished legacy P0/P1 work keep the phase INCOMPLETE. Complete inventory retained on issue #83; audit cursor unchanged.

## Reliability remediate 2026-09-18 (reduced P0/P1 scope)
- [x] T1 depends_on: [] Verify runner and read complete issue #81 checkpoint before local ledgers.
- [x] T2 depends_on: [T1] Restore isolated dependencies and establish full baseline.
- [x] T3 depends_on: [T2] Red/green REL-258, REL-259, REL-260, REL-262 plus REL-151/152/226; reconcile inherited residuals. REL-260 visual verification BLOCKED after three browser launches.
- [x] T4 depends_on: [T3] Complete permanent drills and three sequential full gates; prepare complete durable issue report and guarded publication inputs.
Dependency graph: T1 -> T2 -> T3 -> T4.

Review (source preservation): seven P1 findings implemented in six root-cause commits; focused checks 388 Python / 162 cloud / 69 Vitest passed, cloud 2 skipped; permanent drills 132 Python / 12 Vitest passed. Four full-cloud failures reproduce unchanged on b33032b4. Final three full-gate runs and durable publication report remain pending.

Reliability closing review 2026-09-18: three full runs each 13505 Python passed / 9742 Vitest passed / 1881 cloud passed, 4 verified baseline failures, 76 cloud skipped; permanent drills 144 passed. REL-260 browser verification remains BLOCKED after three installed-browser launches. Complete inherited acceptance is preserved in the prepared issue #81 report; publication and notification receipts are tracked in durable reliability scratch.

## 2026-09-18 HY A-D S&P 500 history
- [x] T1 depends_on: [] Trace stored SPX coverage and HYAD snapshot assembly.
- [x] T2 depends_on: [T1] Repair historical overlay and add regression coverage without inventing observations.
- [x] T3 depends_on: [T1] Add browser coverage for early-history S&P values.
- [ ] T4 depends_on: [T2,T3] Publish PR, verify exact-head GitHub checks and browser artifacts, refresh durable data if needed, notify.
Dependency graph: T1 -> {T2,T3} -> T4.
Validation: suites run only on GitHub; inspect real stored coverage and screenshot artifacts. Preserve source priority and FINRA history.

Review: credit history has 269 closes from 2025-08-22; existing Cboe history restores 2,173 of 2,174 HYAD dates from 2018-01-22. The equity closure 2025-01-09 stays null. Python joins and browser early-date regressions added; local suites intentionally not run. Exact-head CI and screenshot review pending.

Operational review: repaired the existing Turso snapshot in place, preserving scan_time 2026-09-18T11:01:32+00:00 and all breadth values. Readback confirms SPX 269 -> 2,173 closes, first 2018-01-22, only 2025-01-09 null. Initial GitHub Python/Vitest feature checks pass; corrected docs-owner index and curated-browser ledger contracts. Recurring repair awaits PR deployment.

## Weekend browser host + stale lock reclaim (implement PR, 2026-09-20)
- [x] T1 depends_on: [] Read the rails: testing wrapper lock/launch/phase flow, setup script, sibling wrappers, weekend_prune locking_pid, skill + remediate prompt, TEST_LOG T-496, playwright.config.ts.
- [x] T2 depends_on: [T1] Root-cause the Mach denial (Seatbelt mach-register under codex / Claude sandboxes) and prove the run-server + PW_TEST_CONNECT_WS_ENDPOINT wiring with the runner holding no browser.
- [x] T3 depends_on: [T1] Design host-only stale-lock reclaim for plain-file locks and dead pids without weakening R-411.
- [x] T4 depends_on: [T2,T3] Write `tasks/weekend-browser-host-and-lock-reclaim-plan.md` (plan PR #576).
- [x] T5 depends_on: [T4] Implement PR (separate): wrapper browser host, lock helpers x6 (pid+start fingerprint, shared-parent sweep, setup checks; hard DoD L1-L5 per Joe 2026-09-20), skill rails, red/green tests, operations.md owner update.
Dependency graph: T1 -> {T2,T3} -> T4 -> T5.

Review: plan PR #576 was design only. This implement PR adds the host run-server, L1-L5 lock hygiene, skill rails, and regressions.
# Reliability remediate 2026-09-20 (reduced P0/P1)

- [x] T1 depends_on: [] - Establish full baseline; reconcile complete issue #81 checkpoint.
- [x] T2 depends_on: [T1] - REL-268 aggregate close-out quantities, red/green and full gates.
- [x] T3 depends_on: [T2] - REL-269 convexity decision gate, red/green and full gates.
- [x] T4 depends_on: [T3] - REL-274 aggregate/individual reconciliation, red/green and full gates.
- [ ] T5 depends_on: [T4] - Permanent drills, three full gates, durable complete issue report and guarded publication.

- [ ] T6 depends_on: [T2] - Fresh-snapshot SELL-envelope risk follow-up; restart final drills and three full gates.

## Review

- Pending verification. P2 findings and inherited operator-only acceptance stay open.

## Reliability remediate 2026-09-21 (reduced P0/P1)
- [x] T1 depends_on: [] Establish full-gate baseline and reconcile authoritative issue #81 handoff.
- [x] T2 depends_on: [T1] Integrate unpublished REL-268/269/274 commits, preserving source and recorded evidence.
- [x] T3 depends_on: [T2] REL-275 fault-injection red/green and surgical identity-bound cleanup.
- [ ] T4 depends_on: [T3] Permanent drills and three consecutive full gates; review diff and guarded publication.
- [ ] T5 depends_on: [T4] Persist complete dispositions, acceptance and operator actions on issue #81.
Dependency graph: T1 -> T2 -> T3 -> T4 -> T5.
Review: pending. P2 acceptance remains in the authoritative checkpoint, outside reduced scope.

## Reliability remediate 2026-09-22 (reduced P0/P1)
- [x] T1 depends_on: [] Reconcile issue checkpoint and establish full baseline.
- [x] T2 depends_on: [T1] REL-277 snapshot-bound repair red/green with transactional conflict tests.
- [x] T3 depends_on: [T2] Permanent drills and three full gates; review and commit substantive work.
- [ ] T4 depends_on: [T3] Persist complete checkpoint and guarded publication.
Review: 151 focused tests pass; 12 red regressions recorded. Complete snapshot comparison is intentionally conservative. 107 permanent drills passed. Three full gate pairs finished; Vitest 9993 passed each, Python retains 31 baseline process failures (one extra timing failure only in run 1, isolated green). Publication and durable final checkpoint pending. Inherited operator acceptance and all P2 findings remain open.
## Documentation remediate 2026-09-22 (reduced P0/P1)
- [x] T1 depends_on: [] Read complete issue #202 checkpoint and reverify DOC-121/122 against origin/main.
- [x] T2 depends_on: [T1] Reproduce DOC-122, consolidate gross rebuild recovery in existing Flex owner, extend ownership and safety contract.
- [x] T3 depends_on: [T2] Run focused and full gates, compare failures on clean main, review substantive diff.
- [x] T4 depends_on: [T3] Prepare complete finding checkpoint and formatter-generated publication handoff; publication receipts belong on issue #202.
Review: DOC-122 red/green demonstrated; 108 docs/path and 14 isolated rebuild tests pass. Full Python: 14827 passed, 31 failed, 2 skipped. Cloud: 2169 passed, 6 failed, 6 skipped initially; pinned-environment failure rerun 2 passed/4 failed. All remaining 31 Python and 4 cloud failures reproduce on clean 5bb91597, zero failure delta. Full Vitest: 9993 passed. Changed links/anchors (4), owners JSON, offline help, diff whitespace and secret scan pass. DOC-121 remains operator-only; DOC-110/120/123 retain their complete acceptance in issue #202 outside reduced scope. No live mutation or runtime behavior change. CI delivery belongs to the separate deliver phase.

## 2026-09-22 Remove Command Palette
Dependency graph: T1 -> {T2,T3} -> T4 -> T5.
- [x] T1 depends_on: [] Isolate latest main, inspect instructions and map palette dependencies.
- [x] T2 depends_on: [T1] Remove global palette, trigger, hotkey, styles and obsolete tests; preserve instrument search.
- [x] T3 depends_on: [T1] Remove instrument Commands deck and shortcut; update cockpit regressions.
- [x] T4 depends_on: [T2,T3] Review integration and browser regressions; publish PR.
- [ ] T5 depends_on: [T4] Verify exact-head GitHub checks and screenshots, send Pushover receipt.
Validation: no local test suites. GitHub owns unit, build and browser checks. Preserve unrelated workspace edits in original checkout.

Review: removed global dialog/trigger/recent-history storage and instrument Commands deck/colon handler; retained direct instrument search and Cmd/Ctrl+K focus. Unit and curated browser regressions cover search navigation, remaining decks, Escape and responsive absence. No local test suites. GitHub checks and visual artifacts pending.

Review update: PR #630 published; independent review preserved the shared asset empty-state style while deleting palette-specific styles. TypeScript and ESLint passed. Exact-head GitHub verification pending.

## 2026-09-22 PR628 delivery and saved-trade follow-through
Dependency graph: T1 -> T2 -> T3 -> T4 -> T5; T1 -> T1b -> T4.
- [x] T1 depends_on: [] Isolate PR branch and reconcile conflicts with current main, preserving central recovery documentation.
- [ ] T1b depends_on: [T1] Validate supplied saved XML and inspect runner/host acceptance prerequisites.
- [ ] T2 depends_on: [T1] Push conflict resolution and verify every applicable exact-head GitHub CI check.
- [ ] T3 depends_on: [T2] Notify green, merge, and verify production deployment.
- [ ] T4 depends_on: [T1b,T3] Execute saved-Flex rebuild and legacy cleanup dry-runs; perform safe runner/host acceptance steps and record blockers.
- [ ] T5 depends_on: [T4] Record operational evidence and remaining actions.
Review: pending. No local suites. PR next steps specify dry-runs; destructive cleanup and any unproven data repair remain gated by evidence. Original workspace edits untouched.

## Runner trust follow-up (2026-09-22)
- [x] T1 depends_on: []: Inspect merged #638 and private residual findings.
- [x] T2 depends_on: [T1]: Fail closed on runner refresh, retire shared host browsers for every provider, preserve separate DeepSec gitdirs on setup reruns.
- [ ] T3 depends_on: [T2]: Add behavioral regressions, publish PR, verify every applicable exact-head CI check.

Dependency graph: T1 -> T2 -> T3.

### Runner trust review
- #638 already isolates host git configuration, but refresh failure propagation and shared browser lifecycle required follow-up.
- #652 adds failure/idempotency/transition cases and all-provider endpoint-removal coverage.
- Static diff reviewed independently; local suites intentionally omitted. Exact-head GitHub CI is the completion gate.
# Task: Close SLM evaluation gaps with a private human-review workflow (2026-09-23)

## Dependency graph
- T1 depends_on: [] - Pin a fresh post-training time holdout because the original 2,420-row set is unavailable; record hash, extraction cutoff, and explicit non-equivalence. Set rare recall target to max(A - 5 pp, 50% pooled), with every rare label at support >=10 reaching >=30%; require C human acceptance >=A and paired 95% interval lower bound >=-5 pp on the 200-row blinded sample. Any missing gate fails closed.
- T2 depends_on: [T1] - Re-run saved checkpoint on the pinned holdout; persist ID-aligned predictions, validity, latency, and per-label TP/FP/FN/recall.
- T3 depends_on: [T1] - Implement a responsive human review web app for a blinded, stratified 200-post sample (at least 40 image posts); save only decisions and corrections.
- T4 depends_on: [T2, T3] - Collect human review, compare A/B/C on matching rows, calculate paired uncertainty, and evaluate every promotion gate.
- T5 depends_on: [T4] - Keep production rung off unless all gates pass; preserve budget reserve and stop temporary GPU only after artifacts and review are safely complete.

## Checklist
- [x] T1 Freeze new post-training time holdout; explicitly mark non-equivalent to missing original set.
- [ ] T2 Run saved checkpoint and publish per-label diagnostic artifacts.
- [x] T3 Build and verify private review UI on desktop and mobile; operator route only, responsive screenshots, no horizontal overflow, local decisions exclude source text and blind-arm key.
- [ ] T4 Finish blinded review and gate report.
- [ ] T5 Preserve reserve, close temporary access, and record final decision.

## Review
- Fresh export pinned 2026-09-23 02:33:11 UTC: N=2,414 (train 2,527; valid 1,064), test SHA-256 `4408d9a9a4ccb9f0e1afb425bd9004c14992837bab489407d3b2d0d163434660`; test start `2026-07-25T02:33:11Z`; prompt-contract SHA-256 `a174584718d22c7e350fd0e0e905cf0d49896d26355dc55a319ccf8567b94288`; taxonomy 908, rare labels 40. This new time split is post-training but is not the archived 2,420-row set and cannot reproduce the old .579 result.
- The same holdout has 2,190 image posts; source metadata export contains IDs/image metadata only and stays on the credentialed Mini until the stratified packet is built.
- Private operator-only review page and client-side local decisions are implemented; desktop (1440px) and mobile (390px) screenshots confirmed responsive, with no mobile horizontal overflow. Production compile, static-generation build, typecheck, and Python syntax checks pass. PR #656 head `375fa02b1b9d88a7c1ec0a358dd959b94788e9be` has all applicable CI green, including Vitest, pytest, image builds, perimeter smoke, and non-gating Playwright; Pushover accepted the green notification (HTTP 200). The PR is open and unmerged, so the operator page is not yet deployed to production. T2 is blocked on private pod access confirmation. The GPU pod remains active at $2.10/hour; RunPod showed $38.49 credit and the planned reserve is $12 at this checkpoint.

## Reliability remediate 2026-09-23 (reduced P0/P1)
- [x] T1 depends_on: [] Read authoritative issue #81 checkpoint and validate runner/branch.
- [x] T2 depends_on: [T1] Establish baseline, reproduce REL-280, fix measurement provenance and verify controls.
- [ ] T3 depends_on: [T2] Run full gates, permanent drills and three closing gates; commit substantive task.
- [ ] T4 depends_on: [T3] Guard publication and persist complete issue handoff.
Review: REL-280 red 10/green 52; first full gate matches 33 baseline Python failures, Vitest 10016 passed. Closing drills and two further gate pairs pending; inherited P2/operator acceptance retained.

## Testing remediation 2026-09-23 (reduced)
- [x] T1 depends_on: [] Read issue #83 handoff and restore dated branch to current main.
- [x] T2 depends_on: [T1] Implement current fixes and reconcile ALL legacy P0/P1 (T-121 browser acceptance pending): T-247, T-121, T-240, T-242, T-258, T-346 through T-352, T-380; T-498 verified upstream; retain P2/operator inventory.
- [ ] T3 depends_on: [T2] Run closing full gates three times and persist complete issue handoff.
- [ ] T4 depends_on: [T3] Review substantive diff and publication eligibility.
Review: pending; no live broker calls, browser launches, or runner-lock operations.

## Reliability remediation 2026-09-25

- [x] T1: Reproduce REL-284 account-switch stale-read and queued-write failures (depends_on: [])
- [x] T2: Implement identity-generation isolation and focused browser coverage (depends_on: [T1])
- [x] T3: Focused checks and permanent drills green; full gates/browser acceptance recorded BLOCKED after verified environment failures (depends_on: [T2])

## Review
- REL-284 source fix is narrow and red/green evidence is recorded in RELIABILITY_LOG.md. Full-gate and Playwright acceptance are blocked by missing installed dependencies and host process/browser permissions; no overall green is claimed.

# Task: Durable Flex Trade_History coverage (2026-09-25)
- [ ] T1 depends_on: [] Diagnose persisted delivery rejection using read-only production evidence.
- [ ] T2 depends_on: [T1] Add regression and minimal repair preserving missing-row integrity gates.
- [ ] T3 depends_on: [T2] Publish PR, verify every exact-head GitHub check, notify green.
Dependency graph: T1 -> T2 -> T3.
Review: no local suites; no speculative trade replay or journal writes.

## Flex review update
- T1 complete: eight rejected saved statements independently satisfy deployed read-only coverage; 2798 persisted journal rows, zero imports/drops/disagreements per statement.
- T2 complete through existing PR #698 (f3dd7921), merged 15:26 UTC and deployed 15:30 UTC after both morning failures; no duplicate source patch warranted.
- T3 not applicable: no substantive new diff or PR. Existing exact-head #698 GitHub checks complete green (applicable Python, Vitest, coverage, browser, images, security); parent owns post-deploy normal Flex retry.

# Task: Knowledge and Liquid Compute operational recovery (2026-09-25)
- [x] T1 depends_on: [] Inspect units, logs, deployed fix provenance, and runtime prerequisites without writes.
- [ ] T2 depends_on: [T1] Coordinate post-deploy activation/retry with root agent and observe actual completion.
- [ ] T3 depends_on: [T2] Record health evidence; add minimal source/regression repair only for a reproduced residual defect.
Dependency graph: T1 -> T2 -> T3.
Review: Liquid Compute 180s timeout predates deployed PR #693, which removes the expensive shared snapshot rebuild. Knowledge canonical hourly timer was disabled; setup already enables it, content-only deploy sync intentionally preserves activation state. Embedding, database, HTTP, dotenv packages and persistent model cache exist; required credential keys are present. No local suites or live starts.

# Task: Knowledge prepared-batch persistence recovery
- [x] T1 depends_on: [] Identify live SQLITE_BUSY after expensive enrichment and source-wide replay.
- [x] T2 depends_on: [T1] Add regressions for prepared-batch retries, bounded exhaustion without source replay/pruning, and competing-writer lock exclusion.
- [ ] T3 depends_on: [T2] Verify red on GitHub, implement bounded batch retries and early write lock, then exact-head green.
- [ ] T4 depends_on: [T3] Notify and coordinate deployment/live catch-up with root agent.
Dependency graph: T1 -> T2 -> T3 -> T4.
Review: local suites prohibited; no live trade/journal changes. Preserve atomic corpus/FTS writes, complete-document batches, authoritative pruning, and real failure heartbeat.

Review update: PR #710 red head `9ad53d8c`, GitHub run `36177477069`, scripts-jm: 4 new regressions failed, 1349 passed, 7 skipped. Failures prove immediate busy propagation, four complete enrichment passes after exhaustion, and competing writers acquiring both deferred transactions. Implemented prepared persistence retries with fresh handles, terminal exhaustion, rollback-error preservation, BEGIN IMMEDIATE, committed-batch progress, and 16-document embedding inference. Added prior-commit preservation, prune retry, schema-error no-retry, and 201-vector ordered-coverage cases. Independent review caught rollback cleanup masking the busy cause; strengthened real-store regression accordingly. No local suites.

Review update: GitHub head `6609314e` scripts-jm green: 1357 passed, 7 skipped. Main integration preserves all task sections; exact merged-head checks pending. Liquid Compute fresh health 18:56:11 UTC; parent verified Flex fresh health 18:57:04 UTC. Knowledge timer remains enabled but paused after committed id 10622 checkpoint and 4.3 GiB peak, pending bounded-memory deployment.

# Task: Bounded transactional knowledge transport (2026-09-25)
- [x] T1 depends_on: [] Reproduce native stall with stable CPU/socket counters and responsive bounded DB read; stop recovery and release deploy lock.
- [x] T2 depends_on: [T1] Prove native CLI regression on GitHub; implement knowledge-only bounded transactional Hrana adapter and protocol/atomicity tests.
- [ ] T3 depends_on: [T2] Independently review, publish exact-head green PR, notify and coordinate deployment.
- [ ] T4 depends_on: [T3] Complete lock-protected all-source recovery and restore hourly timer with real health evidence.
Dependency graph: T1 -> T2 -> T3 -> T4.
Review: red head e3aa9167, CI36182632129: 1 regression failed, 1357 passed, 7 skipped. Independent transport review approved transaction/protocol coverage. No local test suites; native GIL-blocking I/O cannot be bounded by threads. Preserve document/FTS transactions, rotating batons, safe HTTPS routing and retry entire prepared batches after ambiguous failure.

# Task: Knowledge writer lock occupancy (2026-09-25)
- [x] T1 depends_on: [] Confirm bounded writer probe timeout4.040s with reads44–67ms, stopped recovery and released deploy lock; inspect200-chunk write transaction.
- [x] T2 depends_on: [T1] GitHub red/green regressions; make no-distill prefilter honor optional summaries and persist one complete authoritative document per transaction.
- [ ] T3 depends_on: [T2] Independent review, all exact-head CI green, accepted notification and coordinated deployment.
- [ ] T4 depends_on: [T3] Verify writer availability and complete bounded recovery, restore timer and real health.
Dependency graph: T1 -> T2 -> T3 -> T4.
Review: red head bff0eecd, CI36186210160 reproduced the new regressions. Parent and independent reviewer approved the narrow implementation. Preserve complete multi-chunk document/FTS/prune atomicity; no timeout increase, dropped rows, speculative retries or fabricated health. No local test suites.

# Task: Knowledge atomic HTTP transaction cleanup (2026-09-25)
- [x] T1 depends_on: [] Identify lost-receipt orphan window in rotating-baton writes; preserve failed recovery and read-only provenance.
- [ ] T2 depends_on: [T1] GitHub regression-first proof; single-request conditional transaction plus close, SQL-only store parity for upsert and source prune.
- [ ] T3 depends_on: [T2] Independent review, exact-head CI green, accepted notification and coordinated deployment.
- [ ] T4 depends_on: [T3] Verify passive writer availability; complete bounded recovery, real healthy timestamp and enabled timer.
Dependency graph: T1 -> T2 -> T3 -> T4.
Review: tests-first head3c8bb97d, CI36188923632 reproduced the new cleanup regressions; parent and independent reviews approved the implementation and parity coverage. No local suites, timeout inflation, speculative live retries or fabricated health. Preserve first SQL error, document/FTS/prune atomicity and idempotent ambiguous-commit replay.

# Task: Durable config-drift writer recovery (2026-09-25)

## Dependency graph
- T1 depends_on: [] - Inspect production audit logs and deployed configuration.
- T2 depends_on: [] - Independently review drift auditor and regression coverage.
- T3 depends_on: [T1, T2] - Implement minimal durable repair and regression cases.
- T4 depends_on: [T3] - Create PR; verify every applicable exact-head CI check; notify.
- T5 depends_on: [T4] - Merge through normal protections, verify production deployment, and publish a fresh clean audit.

## Checklist
- [x] T1 Production evidence
- [x] T2 Code review
- [x] T3 Repair and regression coverage
- [ ] T4 GitHub CI and notification
- [ ] T5 Production deployment and audit

## Review
- Local test suites prohibited; validation runs on GitHub runners.

- Production 2026-09-25 audit wrote successfully; findings were an obsolete Flex drop-in and an uninstalled optional SLM unit.
- Archived the exact redundant Flex drop-in to `/root/radon-config-drift-20260925/write-performance-data.conf`; verified canonical effective write/read-only paths after daemon-reload.
- Original SLM unit fails production systemd-analyze verification (exit 1, missing llama-server); guarded candidate passes (exit 0). No services activated.
- Added Linux CI verification and install-without-activation regressions. Exact-head CI pending.

- CI repair: `scripts-df` reported the SLM owner-document contract; updated the unit example and provisioning semantics in `docs/ml/newsfeed-slm-tagger.md`. Re-run all applicable checks on the new head.
- CI repair: an unchanged SIGTERM regression failed on the second head after passing on the first. Synchronize the isolated wrapper with its actual parent wait boundary and preserve all reporting/status assertions; retain subprocess diagnostics. Prior failure output did not capture the precise exit path. Production wrappers unchanged.

# Task: Resume interrupted database backups after deploy (2026-09-25)

## Dependency graph
- T1 depends_on: [] - Confirm interruption in production and replay-safety of standalone backup.
- T2 depends_on: [T1] - Regression tests for active-backup replay, dormant exclusion, repeated recovery, and eventual off-box failures.
- T3 depends_on: [T2] - Restore only the explicitly replay-safe backup oneshot after release activation; preserve all other oneshot exclusions.
- T4 depends_on: [T3] - Exact-head GitHub CI, deploy, successful real local/off-box backup heartbeat.

## Checklist
- [x] T1 Sep24/Sep25 backups killed by deploy stop-clean at09:09/09:08; normal recovery skips all oneshots.
- [x] T2 Regression coverage
- [x] T3 Surgical restoration
- [ ] T4 CI and live backup

## Review
- Backups remain quiesced while code changes; only a backup captured in the interrupted active snapshot is replayed. No trading/order oneshot replay.

- GitHub red evidence: CI36176394767 cloud-al reports four expected interrupted-backup failures; 2,251 passed and two skipped on 86b16936. Implementation adds only explicit backup replay and accepted asynchronous restart submission.

- Independent review: do not couple eventual backup/upload failure to app rollback. Verify start submission, retain normal oneshot verification exclusion, and pin retry after rejected submission without a restore marker.

## 2026-09-25 Scheduled writer freshness investigation
- [x] T1 Collect current health, timer and writer evidence. depends_on: []
- [x] T2 Repair admin freshness classification and retired control applicability; add regression coverage. depends_on: [T1]
- [x] T3 Create scoped PR and verify exact-head GitHub CI. depends_on: [T2]
Dependency graph: T1 -> T2 -> T3.

Review: Seven on-demand writers were correctly registered but admin freshness ignored categories. Admin now labels them On demand and scores scheduled writers only. Errors and last-run ages remain visible. Retired exit-orders rows are excluded without mutating storage. Unknown services remain scheduled. knowledge-ingest disabled timer and liquidcompute timeout investigations handed to parent for separate operational repair. PR707 head61a4973e:31 applicable checks green,7 expected skips; Playwright200+1 passed. Desktop/mobile screenshots inspected. Pushover200/status1 request8474b0e5-ecba-4d16-8817-4a430c742f03. No local suites run. Completion notes kept local to preserve the verified PR head; parent coordinates merge/deploy.


## Operator A implementation — 2026-09-25
- [x] T1 Attention model and regression cases. depends_on: []
- [x] T2 Action queue, overview, responsive styles and reusable controls. depends_on: [T1]
- [x] T3 Integrate current polling, timestamps and diagnostic navigation. depends_on: [T1, T2]
- [x] T4 Component/E2E coverage and independent review. depends_on: [T2, T3]
- [ ] T5 PR, exact-head all-applicable CI green and accepted Pushover. depends_on: [T4]
Dependency graph: T1 → T2 → T3 → T4 → T5, T1 → T3, T2 → T4. Independent model, presentation, and test preparation run in parallel; integration waits for their contracts. Isolated worktree protects unrelated local SLM changes. No local suites; GitHub CI only. No merge/deploy authorization.

Review: Selected A implemented using existing Clear tokens and mutation owners. Independent model, UI, and coverage reviews completed. TypeScript static check passed; desktop/mobile browser fixture inspected, including 320px dark layout, primary action within first viewport, no horizontal overflow, and nested confirmation focus return. Local test suites were not run; regression execution and screenshots are assigned to GitHub CI.

CI review: e3fc14a0 passed all eight Vitest shards and the coverage ratchet. The curation guard requires a dated review for modified held-out admin-panel.spec.ts; recorded its static locator review and separate browser fixture coverage without claiming that held-out suite ran.

## 2026-09-25 Durable SLM review and readable images

Dependency graph: T1 -> T2 -> T3 -> T4.

- [x] T1 Add operator-only decision backup with merge and regression coverage. depends_on: []
- [x] T2 Restore packet, decisions, draft tags and exact position after restart. depends_on: [T1]
- [x] T3 Show source images automatically at useful desktop/mobile size. depends_on: [T2]
- [ ] T4 Verify in browser and exact-head GitHub CI; document result. depends_on: [T3]

Review: pending. Existing localStorage decisions migrate into the backup when the original packet is loaded. The packet stays in browser storage; the server stores only votes, labels and cursor. No local test suites per operator policy.

## Operator header recovery controls — 2026-09-25
- [x] T1 Inspect gateway/service capabilities and define header layout. depends_on: []
- [x] T2 Add persistent header access, explicit gateway Start/Restart/Stop, and service controls using current mutation owners. depends_on: [T1]
- [x] T3 Add regressions and inspect desktop/mobile fixtures. depends_on: [T2]
- [ ] T4 Publish PR; complete exact-head CI and accepted notification. depends_on: [T3]
Dependency graph: T1 -> T2 -> T3 -> T4. Gateway implementation, header composition, and coverage preparation may run in parallel after contracts are established. Existing confirmations, source freshness, push leases, host permissions and pending exclusion remain enforced. No live remediation or local test suites. Bulk Start All/Stop All scope awaits optional user clarification; default to each service plus existing Restart All.

Review: independent gateway/coverage reviews completed; shared command lock and source freshness guards implemented. TypeScript static check passed. Manual dark desktop and320px mobile fixture review confirmed direct header access, grouped Gateway commands, service inventory reflow, primary action within752px and no horizontal overflow. Tests authored for GitHub CI; no local suites or live infrastructure actions run.

CI repair review: befea93d preserved21 new gateway/service component regressions; one existing copy assertion required explicit unavailable start/stop/restart wording. Cloud CI exposed an existing pipefail race in required/active unit membership checks (2261 passed,1 failed): pure-shell exact matching replaces early-exit grep pipelines and adds4096-unit topology coverage without relaxing required-unit or oneshot replay guards. No local suites.

## 2026-09-27 Golden-set production-data disclosure

## Dependency graph
- T1 depends_on: [] - Confirm the reported production journal identifiers and trade details remain tracked at HEAD.
- T2 depends_on: [T1] - Add a failing fixture-safety regression and remove production-derived labels.
- T3 depends_on: [T2] - Run focused and full verification, review the diff, commit, and publish a PR.

## Checklist
- [x] T1 Confirm disclosure at HEAD.
- [x] T2 Regression and minimal remediation.
- [ ] T3 Verification and delivery.

## Review
- Removed the three production-derived journal labels rather than substituting synthetic brokerage history; retained all pre-existing semantic journal coverage.
- Regression rejects long numeric journal identifiers in the tracked golden set. Focused fixture tests pass; the affected suite is blocked locally because Python 3.13 lacks `libsql_experimental` and package installation is network-denied, while Python 3.14's extension segfaults.

### PR #879 CI repair — 2026-10-02

- [x] T1 depends_on: [] Inspect exact-head CI and reproduce the documentation ownership failure locally.
- [x] T2 depends_on: [T1] Update the knowledge embeddings owner documentation to match the sanitized fixture and regression.
- [x] T3 depends_on: [T2] Run the documentation contracts and review the repair before publication.
- [ ] T4 depends_on: [T3] Push the repair, verify exact-head CI, and send the accepted green notification.

Review: original ownership test reproduced red (1 failed); owner documentation updated with the fixture removal, six-digit identifier guard and baseline review requirement. All 89 documentation contracts pass; diff whitespace is clean. Changes limited to owner documentation and task tracking; the ownership gate remains enforced. Exact-head CI and notification receipt pending.
# Task: PR #766 follow-up, demo CI guard and Linux deploy cleanup (2026-09-28)

## Dependency graph and checklist
- [x] T1 depends_on: [] Confirm PR state, CI wiring, demo database identity, and the Linux cleanup tests.
- [x] T2 depends_on: [T1] Provision both repository CI demo URL secrets and verify their registration.
- [x] T3 depends_on: [T1] Reproduce and repair deploy child cleanup on Linux CI, preserving the fixed test timeout.
- [ ] T4 depends_on: [T2, T3] Open follow-up PR, verify every applicable exact-head CI check, and send the required notification.

## Review
- Both secrets point to Turso `radon-demo` and appear in `gh secret list`; the push-only guard will run on the next main push.
- Linux cloud CI on `5f7c36ab` red-failed at the preheld gateway gate (status 66). The fixture now reports it inactive, reaches the hanging stop child, and Linux cloud CI passed on `6156ac79`; the harness remains `timeout=5`.

## Nightly testing 2026-09-29

- [x] T1 depends_on: [] Build isolated toolchain, inspect open testing PRs, recover trusted checkpoint.
- [x] T2 depends_on: [T1] Audit 3cc3d84a..c274a773 using import graph, run standing gates and repeat local changed tests three times, reconcile ledgers; browser repetitions assigned to CI.
- [x] T3 depends_on: [T2] Remediate verified findings with red/green evidence, one commit per finding.
- [x] T4 depends_on: [T3] Complete three serial full-gate rounds and baseline cloud failure-list comparison.
- [x] T5 depends_on: [T4] Prepare substantive draft-PR and durable issue inputs; publication, exact-head CI, browser and notification receipts are tracked on issue #83.

Review: six separate red/green finding commits; three serial closing rounds recorded in TEST_AUDIT.md, with two earlier load-sensitive failures explicitly retained beside green isolated reruns. Cloud FAILED lists match the detached base. Targeted hook coverage: 100% lines, 98% branches, 100% functions. Browser and external delivery receipts belong to issue #83. Fake-only tests; no live services, credential inspection, main pushes or merges.

## Nightly reliability 2026-09-29

- [x] T1 depends_on: [] Install isolated Python toolchain, read trusted checkpoint and open PRs, select tonight's branch.
- [x] T2 depends_on: [T1] Audit 5c27667c..c274a773 and codemap importers; verify standing sweeps and inherited acceptance.
- [x] T3 depends_on: [T2] Fix verified findings serially with fault-injection red/green and focused gates.
- [ ] T4 depends_on: [T3] Run permanent drills, validate diff, publish substantive draft PR and await exact-head CI.
- [ ] T5 depends_on: [T4] Record one durable issue comment and final review.

Review: pending; only mocks/local fixtures and GitHub operations are authorized. Full suites and Vitest are assigned to CI.

Nightly review update: REL-291 red 4, green runner/hooks 152; standing catalogs 57 passed. Remaining repairs and closing verification pending.

Nightly closing review (2026-09-29): 12 repaired findings, including five new IDs R-710 through R-714. All reproductions confirmed serially before fixes. Python drills 95 passed; standing catalogs 57 passed; inherited acceptance 125 passed; expanded monitor 530 passed. Two production-browser regressions passed and screenshots inspected; TypeScript, build and six curation checks passed. Changed YAML parsed, runner bash syntax and diff checks passed, gitleaks found no secrets. Full pytest/cloud/Vitest and its idempotency drill remain assigned to exact-head PR CI. Remaining historical acceptance is explicit in the audit appendix; no invented BLOCKED status. Runner credentials are stripped from agent execution; no Pushover credentials are available here, and no secret file was sought. Publication, CI and one issue comment remain the final steps.

Publication review: the runner PAT rejected workflow updates. Removed the unpublished workflow edits from local history and consolidated both new browser cases into the existing CI-selected order-safety spec. No CI gate changed; production browser 3 passed and curation 6 passed after consolidation.

Delivery review: draft https://github.com/joemccann/radon/pull/795 is published. CI ticket fixtures were corrected without changing assertions; all eight Vitest shards then passed. Stop modification received the same trigger-price fault injection (red twice, focused green 175). Documentation owner gate red twice, then 71 passed after maintained docs were updated. Exact final-head CI and the single durable issue comment remain pending; GitHub's managed AI scanner separately reports an unsupported model and requires provider/operator repair.

# Nightly CI performance audit — 2026-09-29

- [x] T1 depends_on: [] Read open performance PRs, rolling checkpoint, ledger, and rules.
- [x] T2 depends_on: [T1] Classify 30 organic main runs and reconstruct release critical paths.
- [x] T3 depends_on: [T2] Rank safe candidates; implement qualifying experiments with red/green contracts.
- [ ] T4 depends_on: [T3] Verify, publish substantive changes if any, and record one rolling issue comment.

Review: CIP-015 is the sole qualifying critical-path experiment; 136 focused tests and 49 cloud contracts passed. Collection retains all 38 original cases and adds 3. No production source, gate, shard, timeout or deployment behavior changed. Release gain remains VALIDATING; PR CI and one rolling issue comment are pending.
# Task: AI chat chart prompt and repetitive completion (2026-09-29)

## Dependency graph

- T1 depends_on: [] - Trace production turn, replay exact prompt and image, record failing regressions.
- T2 depends_on: [T1] - Preserve live reads after knowledge lookup without exposing retrieval to tool-capable rounds; reject repetitive output with bounded recovery.
- T3 depends_on: [T2] - Focused/full verification, browser screenshot, live prompt replay, review.
- T4 depends_on: [T3] - Publish a reviewable PR, verify exact-head CI, send the required green notification.

## Checklist

- [x] T1 Trace and reproduce
- [x] T2 Patch and regression coverage
- [x] T3 Verify and review
- [ ] T4 PR and exact-head verification

## Review

- Original Turso turn matched (18:06:31.410Z, grok-4.7, image_count 1). Five red regressions captured. Patched live chart replay reaches rank_spreads and get_option_chain in six rounds; thirteen successful tool events; no order. Final verification: 401 unique focused tests passed across 47 files; 12 Playwright tests passed; repetition detector coverage 100%; TypeScript and focused ESLint passed. Desktop/mobile screenshots reviewed. Initial full web run: 9693 passed, 9 failed, 10 skipped; it overlapped final source edits and browser verification. Every failed file passes in the fresh focused runs. Exact-head CI pending. Trace: tasks/ai-chat-repetition-2026-09-29.md.

## Nightly testing 2026-09-30
- [x] T1 Build isolated toolchain and read trusted checkpoint/open PRs. depends_on: []
- [x] T2 Audit delta/import graph, standing gates and legacy inventory. depends_on: [T1]
- [x] T3 Repair verified findings with red/green evidence. depends_on: [T2]
- [x] T4 Run three serial closing gate rounds and hygiene review. depends_on: [T3]
- [ ] T5 Publish substantive draft, watch exact-head CI, post one rolling comment. depends_on: [T4]

### Review
T-517 and T-518 are verified with rejected mutations and 37 focused passes; no product code changes. Delta repetitions and three full serial closing rounds are complete. The first closing Python failure passed 103/103 alone; later full rounds passed. Cloud lists add zero failures against the detached base. Publication/CI/notification receipts and final delivery status are tracked on rolling issue #83 after this prepublication checkpoint. Only testing/2026-09-30 may be pushed; T-435 retains its operator action.

## Nightly reliability 2026-09-30

- [x] T1 depends_on: [] Install isolated toolchain, recover trusted checkpoint, inspect open reliability PRs and create authorized branch.
- [x] T2 depends_on: [T1] Audit c274a773..e3063f0c and codemap importers; execute standing sweeps and re-triage inherited acceptance.
- [x] T3 depends_on: [T2] Repair verified findings serially with fault-injection red/green and focused gates.
- [ ] T4 depends_on: [T3] Run permanent drills and validation; publish substantive draft PR and await exact-head CI.
- [ ] T5 depends_on: [T4] Post exactly one durable issue comment and complete review.

Review: seven inherited findings repaired in separate commits with repeated serial red evidence and focused green gates. Permanent Python drills: 95 passed; catalog/admission sweep: 108 passed. No new delta findings. Full suites, cloud gates and Vitest belong to PR CI. Historical candidates retain their original acceptance on issue #81; no inspection-only closure. No production access. Publication and exact-head CI pending.

# Task: T-435 keyless setup-gate browser test (2026-09-30)

- [x] T1 depends_on: [] Isolated keyless compile-mode build (`.next-setup-gate`, skip production trace audit) + Playwright config/spec outside `web/e2e/` + vitest contract.
- [x] T2 depends_on: [T1] New non-gating `e2e-setup-gate` CI job + `test_ci_setup_gate_job.py` pin.
- [ ] T3 depends_on: [T2] Ready PR vs main. Exact-head CI green. No merge.

## Nightly testing 2026-10-01
- [x] T1 Build toolchain, recover checkpoint and select authorized branch. depends_on: []
- [x] T2 Audit delta/importers, reconcile ledgers, run standing gates and three delta repetitions. depends_on: [T1]
- [x] T3 Repair verified findings with red/green evidence. depends_on: [T2]
- [x] T4 Complete three serial closing rounds and hygiene review. depends_on: [T3]
- [x] T5 Prepare draft PR and rolling-comment artifacts; track publication and exact-head CI receipts on issue #83. depends_on: [T4]

### Review
Six findings have red/green evidence; three full serial rounds pass Python 13448, Vitest 10300 and cloud 2400 each. Cloud FAILED lists match the empty detached-base list. TypeScript and targeted coverage pass. Fake-only execution; no production access, credential-file lookup, main push or merge. Publication, exact-head CI/browser results and the single final checkpoint are recorded on issue #83 after committing, avoiding a change to the head whose checks are being reported.

## Nightly reliability 2026-10-01

- [x] T1 depends_on: [] Install toolchain, recover trusted checkpoint, inspect open PRs, create tonight’s branch.
- [x] T2 depends_on: [T1] Audit main delta and codemap callers; re-triage standing candidates and safety sweeps.
- [ ] T3 depends_on: [T2] Repair verified findings serially with red/green fault injection and focused gates.
- [ ] T4 depends_on: [T3] Run drills, validate changes, publish substantive draft, watch exact-head CI.
- [ ] T5 depends_on: [T4] Post one durable issue comment and record final review.

Review: pending; fake-only verification, full suites and Vitest assigned to CI.

## Nightly documentation 2026-10-02

- [x] T1 depends_on: [] Recover trusted checkpoint, inspect open PRs, create authorized branch.
- [x] T2 depends_on: [T1] Audit every delta commit and reconcile inherited findings against source and open PRs.
- [x] T3 depends_on: [T2] Repair verified documentation defects with minimal owner changes and red/green contracts.
- [ ] T4 depends_on: [T3] Verify, publish one draft PR, watch exact-head CI, post one rolling issue comment.

Review: 22 commits / 146 paths audited through cb47c52a. Four inherited ownership gaps and four source-backed documentation defects corrected; DOC-149 disproved by four resolving CommonMark links. New contracts failed before owner corrections. Runtime behavior, CI gates and generated codemap remain unchanged. Publication, exact-head CI and the one rolling-issue comment remain pending.
## Nightly testing 2026-10-02

- [x] T1 Build toolchain, recover trusted checkpoint, select authorized branch. depends_on: []
- [x] T2 Audit delta and importers, reconcile ledgers, run standing gates and delta repetitions. depends_on: [T1]
- [x] T3 Repair every verified finding with red/green evidence. depends_on: [T2]
- [x] T4 Run three serial closing gate rounds and hygiene review. depends_on: [T3]
- [ ] T5 Publish substantive draft, verify exact-head CI and post one rolling comment. depends_on: [T4]

### Review 2026-10-02

Three verified findings have red/green evidence. All nine closing gates pass, and every cloud failure-list comparison matches the clean detached base. Product change is limited to the relay poller; prior ledger entries, reliability ledgers and codemap files are unchanged. Publication, exact-head CI and the single rolling-issue receipt are the remaining external steps. The held-out admin browser preflight and missing Pushover environment remain operator-only delivery items.
## Nightly reliability 2026-10-02

- [x] T1 depends_on: [] Install toolchain, recover trusted checkpoint and create authorized branch.
- [x] T2 depends_on: [T1] Audit delta and codemap importers; run standing sweeps and re-triage inherited candidates.
- [x] T3 depends_on: [T2] Repair verified findings serially with repeated red and focused green evidence.
- [x] T4 depends_on: [T3] Run permanent drills, validate, publish substantive draft and await exact-head CI.
- [x] T5 depends_on: [T4] Post one durable rolling comment and complete review.

Review: delta and direct importers audited; four new and five inherited repairs have red/green evidence. R-303 is operator-only after GitHub rejected workflow publication. Permanent drills 95 passed. No live systems, credentials, main push, merge, codemap changes or test-ledger edits. Publication/CI and the one durable issue comment remain tracked by T4/T5; final receipts belong on issue #81.

Delivery review: draft https://github.com/joemccann/radon/pull/866; source head `22e88dfc454d7f2246493948af46d43fe310887b` completed all 24 required contexts, 32 passing checks total and seven non-applicable skips. Exactly one durable comment posted: https://github.com/joemccann/radon/issues/81#issuecomment-5947930686. Nine repairs resolved; 20 historical candidates retain acceptance. R-303 workflow publication and Pushover delivery remain operator-only; notification credentials were absent on both environment-only checks. No merge or live operation occurred. This receipt changes only the checklist; its own exact-head CI is watched before ending the session.

## Nightly documentation 2026-10-03

- [x] T1 depends_on: [] Read trusted checkpoint and instructions, review open PRs, create authorized branch.
- [x] T2 depends_on: [T1] Audit every commit through 15bdb9a4 and reconcile inherited findings.
- [x] T3 depends_on: [T2] Correct verified durable-contract defects with minimal owner changes and red/green checks.
- [x] T4 depends_on: [T3] Verify, publish substantive draft if needed, watch exact-head CI, post one rolling comment.

Review: 18 commits / 117 paths audited through 15bdb9a4. DOC-154 through DOC-158 corrected at existing owners; seven new documentation contracts failed before correction and pass after it. Documentation/path-filter 138 passed; publication/model/hold/subscription 291 passed; offline Gateway CLI 30 passed. Ten changed local links and anchors resolve; owner JSON parses; diff and secret scans clean. No runtime behavior, live operation or generated codemap changed. Draft #886 published; source head ff2d9d53029106774b3e76c1ca44c8488592070c completed 32 passing checks with seven non-applicable skips. Exactly one rolling comment posted: https://github.com/joemccann/radon/issues/202#issuecomment-5968274601. The comment carries the audit cursor, inherited closures and acceptance criteria for DOC-154 through DOC-158. This delivery receipt changes only the checklist; its own exact-head CI is watched before ending the session. Review and merge remain operator-owned; no deployment, merge or notification was performed.

## Reliability nightly 2026-10-03

- [x] T1 depends_on: [] — isolated Python toolchain; trusted checkpoint and open PR inventory.
- [x] T2 depends_on: [T1] — audit delta plus codemap callers, standing safety sweeps and inherited candidates; 18 commits / 220 scoped paths; 92 standing passes.
- [x] T3 depends_on: [T2] — fault-injection red/green repairs, focused gates and per-finding commits.
- [x] T4 depends_on: [T3] — permanent drills, substantive publication and exact-head green draft PR.
- [x] T5 depends_on: [T4] — single rolling checkpoint comment and closing review.

### Review 2026-10-03

Three new regressions and four inherited repairs have repeated red/green evidence. R-303's already merged correction passes the actual parsed acceptance test. Standing safety/catalog checks and 95 permanent drills pass. Fifteen historical candidates/operator tasks retain acceptance on issue #81. No live-system operation, secret lookup, main push, merge, generated codemap change or test-ledger edit occurred. T4/T5 external delivery receipts will be recorded in the single durable issue comment after exact-head CI.

Delivery review: draft https://github.com/joemccann/radon/pull/882; source-verification head `bcfd8b8c8a64656cbfb16c99f634f426234b7c53` completed all 24 required contexts, 32 applicable checks and seven non-applicable skips. Exactly one rolling comment posted: https://github.com/joemccann/radon/issues/81#issuecomment-5966924908. Seven source repairs resolved; fifteen inherited candidates/operator tasks retain acceptance. Final checklist receipt changes only this file and its exact-head CI is watched before ending the session. The runner owns notifications; no notification or live-system action was performed.

## CI performance nightly 2026-10-03

- [x] T1 (depends_on: []): read open performance PRs/checkpoint and audit origin/main changes.
- [x] T2 (depends_on: [T1]): measure 30 organic Actions runs, cache state and gate closure.
- [x] T3 (depends_on: [T2]): rank safe critical-path candidates and implement qualifying experiments.
- [x] T4 (depends_on: [T3]): verify substantive experiment; publication/CI blocked by confirmed repo-only token scope.
- [ ] T5 (depends_on: [T4]): review rails and post one durable checkpoint on issue #196.

Review 2026-10-03: CIP-016 reserved experiment restored against 15bdb9a4; regression red 2 failed/7 passed, green cloud 83 passed, script gate/inventory/path contracts 90 passed. YAML/inline shell/Python syntax, pinned actions, complete 12-family gate equality and diff/secret reviews pass. Full suites, Vitest, Docker builds and workflow lint require PR CI. Expected workflow-scope publication block remains; exact delivery receipt and all run strata belong in the single issue #196 checkpoint. No production access, synthetic samples, merge, main push or additional experiment ID.

## Nightly reliability 2026-10-04

- [x] T1 depends_on: [] Install isolated toolchain, recover trusted checkpoint and reserve tonight's branch.
- [x] T2 depends_on: [T1] Audit delta and codemap importers; execute standing sweeps and triage inherited acceptance.
- [x] T3 depends_on: [T2] Repair verified findings serially with fault-injection red/green and focused gates.
- [x] T4 depends_on: [T3] Run permanent drills and validate substantive publication and change hygiene.
- [ ] T5 depends_on: [T4] Publish one draft, await exact-head CI and post one rolling comment; record external completion receipts on issue #81.

Review: delta audit finished through 6603be9b; three new source findings and inherited R-036 have repeated red/green evidence. R-035 passes executable upstream acceptance. Standing sweeps 110 passed; permanent drills 95 passed, relay 113, research/docs 280 and 305, controller/setup 18, delta scripts 153, delta API 15 and deploy lifecycle 7 passed/2 platform skips. The broader cloud delta run had 167 passes and one environment-only subprocess failure; the failing case passes alone with the venv interpreter on PATH. Substantive publication check, Python/config parsing, shell/Node syntax, diff and four-commit secret scan pass. External publication, exact-head CI and the single durable comment are pending; final completion receipts belong on issue #81 without changing the reported head. Open PR #882 reserves R-721/R-722/R-723, REL-302/REL-303/REL-304 and R-315/R-316/R-317/R-318. Only tonight's branch is eligible for push; full suites and Vitest belong to CI.

## Nightly testing 2026-10-04

- [x] T1 Build toolchain, recover trusted checkpoint and read open testing PRs. depends_on: []
- [x] T2 Audit delta and inverse importers; run standing gates and three delta repetitions. depends_on: [T1]
- [x] T3 Verify candidates with deliberate defects and remediate each serially. depends_on: [T2]
- [x] T4 Complete three serial full-gate rounds, base cloud comparison and hygiene review. depends_on: [T3]
- [ ] T5 Publish substantive draft, watch exact-head CI and post one rolling issue comment. depends_on: [T4]

### Review 2026-10-04 (before publication)

Nine findings have red/green evidence, including seven reverified historical P2s omitted from the latest checkpoint inventory. Focused gate 343 passed; three complete local rounds retain one unreproduced Caddy startup failure and two inherited T-530 failures, with all isolated owner replays green. Per-finding focused verification precedes each explicit-path commit. Syntax, whitespace, secret, ledger-prefix and mutation-restoration checks pass. No production operations, other-branch writes, codemap or reliability changes. T5's external PR/CI/checkpoint completion is recorded once on issue #83 after exact-head CI; this pre-publication checklist does not claim a future green result. Open draft #885 owns T-528 through T-530.

## Nightly documentation 2026-10-04

- [x] T1 depends_on: [] Recover trusted checkpoint, inspect open PRs, create authorized branch.
- [x] T2 depends_on: [T1] Audit every delta commit and reconcile carried findings against current source and open diffs.
- [x] T3 depends_on: [T2] Correct verified owner drift with focused red/green contracts.
- [ ] T4 depends_on: [T3] Verify, publish substantive draft, watch exact-head CI, post one rolling comment.

Review: 27 commits (17 non-merge) and 68 paths audited through 6603be9b. DOC-154 through DOC-158 are covered by open #886 and excluded. DOC-159 through DOC-163 correct IB recovery and order ownership at existing owners; 12 new cases were red before correction. Closing docs/path-filter 145 passed; Gateway and order contracts 63 passed; relay 61 passed. All 29 changed relative links/anchors resolve, no external links added, diff/syntax/secret checks clean. No runtime behavior, live systems, secret reads, CI weakening or codemap edits. Runtime correction for setup_ibc.sh remains in PR Next. Full suites assigned to exact-head CI; notifications owned by runner.

Findings before edits and value gate:
- DOC-159 P0: local IBC operator -> rely on blank restart settings -> unexpected broker session cycle or 2FA -> cloud/docker-compose.yml:28-35 and scripts/setup_ibc.sh:171-179 -> docs/implement.md:225, docs/ib_tws_api.md:589, docs/ib-connection-troubleshooting.md:245 -> docs/ib-gateway-recovery.md. Reader/operator action is diagnosing daily cycle; harm is unsafe authentication recovery; prose is necessary to distinguish blank configuration from disabled behavior. Exact settings remain in compose/setup. Local installer behavior requires a separate runtime repair; do not change it here.
- DOC-160 P0: recovery operator/developer -> assume relay never requests a broker restart or restarts after one stale interval -> unsafe manual second login -> scripts/lib/staleDataMachine.js:380 and scripts/ib_realtime_server.js:941-958 -> scripts/CLAUDE.md:69, web/CLAUDE.md:284, docs/ib-connection-troubleshooting.md:241 -> docs/ib-gateway-recovery.md. Reader/operator action is distinguishing socket retry from lease-gated broker restart; harm is stacked authentication; prose explains actor ownership, while the decision core owns thresholds and modes.
- DOC-161 P0: local Docker operator -> treat TCP health as authenticated recovery -> trade from a disconnected session or retry an in-flight login -> docker/ib-gateway/docker-compose.yml:46-59 and scripts/api/ib_gateway.py:1351-1370 -> docs/ib-gateway-docker.md:105-119, docs/ib-connection-troubleshooting.md:101 -> docs/ib-gateway-recovery.md. Reader/operator action is choosing the readiness gate; harm is false recovery and unsafe 2FA retries; prose distinguishes Docker status from account authentication.

- DOC-162 P0: local recovery operator -> kill a process selected using a stale client registry -> discard an unacknowledged order or interrupt unrelated clients -> scripts/ib_place_order.py:563-643, scripts/ib_sync.py:88 and scripts/ib_orders.py:44 -> docs/ib-connection-troubleshooting.md:109-145 -> scripts/CLAUDE.md allocation/order-placement contracts. Reader/operator action is diagnosing collisions safely; harm is order loss; prose must explain cleanup boundaries while code owns allocation.
- DOC-163 P0: cancellation developer/operator -> use master identity for any order -> cancellation/modification fails and an order remains live -> scripts/ib_order_manage.py:90-118 -> docs/ib_tws_api.md:22-39,538-558 -> scripts/CLAUDE.md Cancel / Modify. Reader/action is selecting order-owner identity; harm is a live order remaining active; prose needs to distinguish visibility from mutation authority.

## Nightly documentation 2026-10-05

- [x] T1 depends_on: [] Recover trusted checkpoint, inspect open PRs and create authorized branch.
- [x] T2 depends_on: [T1] Audit every delta commit and reconcile inherited acceptance against current source.
- [x] T3 depends_on: [T2] Correct verified owner drift with focused red/green contracts.
- [ ] T4 depends_on: [T3] Verify substantive changes, publish one draft, watch exact-head CI and post one rolling comment.

Review: 65 commits (49 non-merge) / 112 paths audited through 72e09ffb. DOC-164 through DOC-168 corrected at existing owners; eight new contract cases failed before corrections. Closing docs/path-filter, extraction and IBC contracts: 177 passed; auth/health/ingestion: 91 passed; policy: 11 passed. Seven changed links/anchors resolve; no new external links or changed machine configuration. Python syntax, whitespace and redacted diff secret scan pass. Inherited drafts #886/#906 and installer repair #909 are merged; DOC-159 retains operator-only effective-cycle verification. Open #911/#913 own firewall runtime/rollback and extraction/expiry test improvements. No live operations, secrets, runtime changes, codemap edits or CI weakening. Publication, exact-head CI and the single rolling comment remain pending; final receipts belong on issue #202.

Findings and value gate before edits:
- DOC-164 P0: operator -> enforce API tailnet trust using full-stack restart and health status -> unintended combined-host Gateway cycle or false bypass proof -> cloud/scripts/operator-radon.sh:504 and scripts/api/server.py:1994 -> docs/operations.md:532-547 -> docs/operations.md. Reader/action: API access rollout; harm: unsafe broker cycle/auth boundary; prose explains scoped reload and independent allow/deny proof that parser/schema alone cannot. Proof: checked-in API unit, auth predicates and offline auth/health tests.
- DOC-165 P2: network operator -> reapply tailnet policy from dated device/session claims and copied grants -> repeated login or mistaken device removal/access loss -> cloud/tailscale/policy.hujson:1 and cloud/tests/test_tailnet_policy.py -> docs/operations.md:517-528 -> docs/operations.md. Reader/action: safe policy replacement; harm: stale setup and lost recovery; prose owns apply/stop/rollback order, code owns grants. Proof: policy and its offline tests; live inventory requires operator review.
- DOC-166 P2: research maintainer -> diagnose alias discovery using unconditional fallback-only claim -> miss existing watched revisions -> scripts/research/worker.py:61-73 -> docs/dropbox-research.md:8,107-110 -> docs/dropbox-research.md. Reader/action: distinguish new aliases from saved watched streams; harm: wrong queue diagnosis; prose explains durable cursor ownership, executable ingestion tests own the exact cases.
- DOC-167 P2: research operator -> expect queue diagnostics to contain only error classes -> overlook persisted extraction cause while diagnosing holds -> scripts/research/model.py:115-128 and scripts/research/state.py:19-22 -> docs/dropbox-research.md:35 -> docs/dropbox-research.md. Reader/action: locate safe extraction diagnostics; harm: misleading incident diagnosis; prose only needs removal of the obsolete restriction, with existing extraction text retained. Proof: test_research_extract_error.py::test_extract_detail_reaches_health_and_journal.
- DOC-168 P2: API maintainer -> change or diagnose Gateway daily cycle from duplicate schedule prose -> recovery and API owners drift independently -> cloud/docker-compose.yml:28-35 -> scripts/api/CLAUDE.md:31 -> docs/ib-gateway-recovery.md#daily-cycle. Reader/action: broker lifecycle implementation; harm: contradictory restart guidance; prose only needs the existing owner link, since compose owns clocks and the recovery owner owns blank-field semantics. Proof: compose and existing daily-cycle documentation contracts.

# Task: Nightly testing 2026-10-05

- [x] T1 depends_on: [] Build isolated toolchain; read open testing PRs and trusted checkpoint.
- [x] T2 depends_on: [T1] Audit 6603be9b..72e09ffb using codemap edges, inverse tests, gates and historical findings; standing suites and three delta repetitions.
- [x] T3 depends_on: [T2] Repair verified findings red/green, explicit per-finding commits and append-only ledgers.
- [x] T4 depends_on: [T3] Three consecutive serial full gates, exact base cloud failure-list comparison and hygiene.
- [ ] T5 depends_on: [T4] Substantive publication check; one draft PR with exact-head green CI; one rolling issue comment.

Review before publication: 65 commits / 112 paths audited through 72e09ffb. T-533/T-534/T-535 have deliberate-defect red/green evidence and separate focused-gate commits; product source is restored byte-for-byte. Three consecutive serial closing rounds each pass 14,200 Python, 10,389 Vitest and 2,572 cloud cases. The clean-base cloud gate passes 2,561 cases and every sorted failure-list comparison is empty. Historical ledger/task prefixes are preserved. Four browser replay acceptances remain operator-only if the web-path-filtered CI job does not run. T5's external draft, exact-head CI and single checkpoint completion receipts will be recorded once on issue #83 without changing the reported head; this pre-publication checklist does not claim future green delivery. No production access; runner owns notifications.
## Nightly reliability 2026-10-05

- [x] T1 depends_on: [] Install isolated toolchain, read trusted checkpoint and open PR inventory, create authorized branch.
- [x] T2 depends_on: [T1] Audit delta and codemap importers; run standing sweeps and re-triage inherited acceptance.
- [x] T3 depends_on: [T2] Repair verified findings serially with red/green fault injection and focused gates.
- [x] T4 depends_on: [T3] Run permanent drills, syntax and diff hygiene; validate substantive publication.
- [ ] T5 depends_on: [T4] Publish one draft, watch exact-head CI, post one rolling checkpoint and record review.

### Review 2026-10-05

Audit complete through 72e09ffb: 65 commits, 112 changed paths and 300 paths including codemap callers. Two new and three inherited repairs have repeated fault-injection red/green evidence; ten remaining historical/operator candidates retain acceptance on issue #81. Standing gates 81 passed, delta acceptance 135 passed, permanent Python drills 95 passed; focused firewall 137/37, rejection 76, mode-switch 138 plus cloud owner 30, provider/caller 206 passed. Fifteen Python files, three shell files and embedded logout Python parse; no YAML/JSON/TOML changed. Diff, five-commit secret scan and substantive publication check pass. No live access, credential lookup, main push, merge, codemap edits or test-ledger edits. T5 external publication, exact-head CI and the single rolling comment are recorded on issue #81 without changing the verified head.


# Task: Nightly testing 2026-10-07

- [x] T1 depends_on: [] Build credential-free toolchain; read trusted checkpoint and open testing PRs; branch from origin/main.
- [x] T2 depends_on: [T1] Audit 39334555..8afaaa61 and inverse test consumers; standing gates and three changed-file repetitions; reconcile inherited findings.
- [x] T3 depends_on: [T2] Verify each actionable gap with a passing-old/failing-new mutation, restore source, run focused gates and commit one finding per commit.
- [x] T4 depends_on: [T3] Three consecutive serial full-gate rounds; clean-base cloud FAILED-list comparison; syntax, whitespace and secret checks; append ledgers and review.
- [ ] T5 depends_on: [T4] Substantive classifier; push only testing/2026-10-07; one draft PR and exact-head green CI; one durable issue #83 comment.

Review: Four verified findings have red/green receipts (T-538/T-540/T-541 P1; T-539 P2). Delta repetitions pass 230 Python and 156 cloud cases each; new YAML owner passes 8 cases three times. Four separate finding commits and three consecutive full closing rounds pass 14,292 Python, 10,390 Vitest and 2,617 cloud cases each; corrected clean-base cloud also passes 2,617 and all closing FAILED-list comparisons are empty. The earlier Caddy startup failure remains recorded separately with its 51-case isolated pass. Delivery remains pending; T5 external draft, exact-head CI and the single checkpoint completion receipt will be recorded on issue #83 without changing the verified head. No production connections, credential lookup, notification, main push or merge authorized.
## Mobile risk-reversal quote preset signs, 2026-10-06

- [x] T1 depends_on: [] Reproduce negative BID/MID preset labels and selection: five component failures and two mobile browser failures before the fix.
- [x] T2 depends_on: [T1] Preserve signed net quotes in preset labels and selected limit values.
- [x] T3 depends_on: [T2] Verify 51 focused units, two mobile mocked order cases, six curation checks, dark/light screenshots and diff hygiene; record review.
- [ ] T4 depends_on: [T3] Publish the isolated fix PR, verify all applicable checks on its exact head and confirm the required normal-priority Pushover receipt.

### Review

Product change is limited to four sign-preserving expressions in MobileOrderTicket. Six added component regressions cover a risk-reversal book crossing zero and all-negative BID/MID/ASK presets. Red: five component failures and two mobile failures; green: 51 component/risk/order tests, two real-pointer mobile cases (retries=0), six curation checks. Browser cases confirm exact BUY combo envelopes with SELL call/BUY put legs and negative limit prices, no early POST, and explicit unbounded-risk acknowledgment. Dark/light full-viewport screenshots inspected under /tmp/radon-risk-reversal-sign-dark/ and /tmp/radon-risk-reversal-sign-final/. Fresh-route source typecheck passes with zero diagnostics; standard tsc reports three missing workflow imports from the pre-existing .next/dev/types/validator.ts. An additional retained mobile-skew case failed when a Clerk error overlay intercepted its click; its short-strangle case passed. No financial/auth gate or CI suite was changed. The new browser spec is explicitly held out pending production-server preflight. Generated test build and tsconfig additions removed; no live order was sent.

Publication verification: branch updated to main 05edd0566; its CI and VPS deployment completed successfully. Full local coverage attempts were interrupted under approximately 9.7 GiB swap usage; the first had unrelated lazy-loader timeouts, and the serial attempt recorded an equibles-contract sorting failure. These are incomplete runs, not coverage or full-suite success. All applicable GitHub checks on the published exact head must finish successfully before PR-green notification and completion.

## Tester Army publication, 2026-10-06

- [x] T1 depends_on: [] Recover the saved Tester Army delta onto current main in an isolated branch, preserving original work and excluding private rescue artifacts.
- [x] T2 depends_on: [T1] Sol 6.1 per-project reconciliation and review; reproduce product regressions and verify focused repairs.
- [x] T3 depends_on: [T2] Luna 6 independent browser/API verification, full local gates and publication/secret hygiene.
- [ ] T4 depends_on: [T3] Commit and create PR; watch every expected check on the exact head until green; send one accepted Pushover notification.

### Review

API review complete: current-main red 41 failures, focused green 82, independent Luna green 82, full API 1179, changed executable lines 28/28 covered. Full scripts/root: 13143 passed, 2 skipped, 23 subtests, one unchanged login-runner timing failure whose focused repeat passed. Full root Vitest: 10386 passed, 21 skipped; unchanged coverage ratchets pass, changed executable web lines 46/46 covered. Site: production TesterArmy 27/27 and retained Playwright 13/13; status regression red/green proved. Workstation: production SDK 77/77, 135 distinct retained cases verified, session fixture 1/1, performance/sidebar 8/8 and visual review complete. Three live-data and five operator cases remain unverified; one existing skip. Actionlint and final staged/commit secret scans gate publication. Exact-head CI and notification receipts will be recorded in the PR without preclaiming success; October 3 receipts remain historical evidence.

## Nightly documentation 2026-10-06

- [x] T1 depends_on: [] Recover trusted checkpoint, inspect open PRs and create authorized branch.
- [x] T2 depends_on: [T1] Audit every delta commit and inherited acceptance against current source.
- [x] T3 depends_on: [T2] Correct verified owner drift with focused red/green contracts.
- [ ] T4 depends_on: [T3] Verify, publish substantive changes if any, watch exact-head CI and post one rolling comment.

Findings and value gate before edits:
- DOC-169 P1: setup/recovery operator -> locate the production application or broker -> use the wrong host for configuration or recovery -> cloud/tailscale/policy.hujson:1 and scripts/api/server.py:1035 -> docs/external-services.md:44 -> docs/spof-host-split.md. Reader/action: select the service owner; harm: blocked configuration/recovery. Prose explains host roles; exact topology and credential paths remain in their existing owners, linked rather than repeated. Proof: role refusal and policy contracts.
- DOC-170 P2: local Docker operator -> retrieve logs after container recreation -> expect broker journald on a local Docker deployment -> cloud/docker-compose.yml:84 and docker/ib-gateway/docker-compose.yml:1 -> docs/ib-gateway-docker.md:98 -> docs/ib-gateway-recovery.md. Reader/action: select the retained log source; harm: wrong diagnosis command or missing evidence. Prose distinguishes source/deployed configuration and read-only diagnosis; exact logging driver/tag stays in compose. Proof: compose test and shim logs verb.
- DOC-171 P1: network operator -> recover from a failed cloud firewall replacement -> blindly repeat an indeterminate mutation or proceed without attachment verification -> cloud/hetzner/firewalls/hcloud_firewalls.py:116-140 -> docs/operations.md:643 -> docs/operations.md. Reader/action: stop and inspect rules/attachment before retry; harm: lost recovery access or incomplete protection. Prose owns safe apply/recovery order; tool owns timeouts and commands. Proof: offline fault suite.

- DOC-172 P0: recovery/provisioning operator -> execute duplicated cloud instructions -> lose host data without recovery, misroute Gateway recovery, or treat obsolete auth/health claims as gates -> cloud/scripts/setup-vps.sh:1045-1063 and cloud/scripts/operator-radon.sh:504, cloud/CLAUDE.md:395 -> cloud/README.md:109,323,432 and cloud/radon-cloud-deployment-guide.html -> cloud/CLAUDE.md and existing recovery/topology owners. Reader/action: choose safe provisioning and broker recovery; harm: destructive reset or wrong session handling. Prose is necessary for owner routing and historical non-execution boundaries; exact commands remain in owners/source. Proof: setup, host-role, existing bootstrap/recovery contracts. Unique Clerk/DNS prerequisites remain in the README pending operator/provider verification; no claims about live provider state are added.

- DOC-173 P1: release operator -> plan deployment without app images -> omit a required exact-SHA gate or diagnose the wrong execution plane -> .github/workflows/ci.yml:1163 and cloud/scripts/deploy.sh:1823 -> cloud/CLAUDE.md:35-41,261 and cloud/README.md:deployment copy -> cloud/CLAUDE.md. Reader/action: identify mandatory release artifacts; harm: blocked recovery/deployment. Prose distinguishes code defaults from installed per-unit execution and directs release diagnostics to enforced gates. Proof: workflow dependency closure, image-reuse contracts and installed drop-in admission source; live installation is not inferred.

Review before publication: 11 commits / 78 changed paths audited through 393345550d521548808d9884be0e5ab10ae48854. Merged #914 closes DOC-164 through DOC-168; DOC-159 retains operator-only effective local cycle/timezone verification. DOC-169 through DOC-173 correct topology discoverability, scoped log diagnosis, uncertain firewall recovery, obsolete cloud instructions and required image/environment admission. Five new owner contracts failed before corrections; closing docs/path-filter 167 passed and focused cloud 101 passed. All 62 added/changed local links and anchors resolve; no external links added. Safe parser/help, Python AST, whitespace and redacted secret-pattern checks pass; no machine config formats changed. Open #922 owns cloud admission and breadth runtime repairs, #924 owns GPU/browser acceptance, #921 owns workflow experiment restoration, #923 owns codemap. No runtime behavior, live systems, credential reads, notification actions, codemap edits or enforcement weakening. Draft #925 is published. Final source review removed unsupported approval wording from owner routing; the strengthened overview case failed before correction and passes afterward. Five focused commits and 63 resolved local links are the closing state; latest-head CI and the single rolling comment remain pending. External receipts belong on issue #202.
## Nightly testing 2026-10-06

- [x] T1 depends_on: [] Build isolated toolchain, recover trusted checkpoint and create authorized branch.
- [x] T2 depends_on: [T1] Audit merged delta and inverse consumers; standing gates and three changed-file repetitions; reconcile historical findings.
- [x] T3 depends_on: [T2] Verify and repair findings serially with red/green evidence and per-finding commits.
- [x] T4 depends_on: [T3] Three serial closing full-gate rounds, base cloud comparison and hygiene review.
- [ ] T5 depends_on: [T4] Publish substantive draft if warranted, await exact-head CI and post one rolling issue comment.

### Review 2026-10-06

Audit complete through 39334555: 11 commits / 78 paths. T-536/T-537 have mutation red/green evidence, separate test-only commits and unchanged production implementations. Three serial closing rounds each pass 14,251 Python, 10,396 Vitest and 2,624 cloud cases; audited-base cloud passes 2,616 and all FAILED-list diffs are empty. Historical prefixes, frozen paths, syntax, whitespace and secret checks pass. No production access, credentials, unauthorized branch push, merge or notification. Two browser acceptances remain operator-only. T5 external publication, exact-head CI and the single rolling comment are recorded on issue #83 without changing the verified head; this pre-publication checklist does not claim future green delivery.
## Nightly reliability 2026-10-06

- [x] T1 depends_on: [] Install isolated toolchain, recover trusted checkpoint and open PR inventory, create authorized branch.
- [x] T2 depends_on: [T1] Audit delta and codemap importers; verify standing sweeps and inherited candidates.
- [x] T3 depends_on: [T2] Repair verified findings serially with repeated red/green fault injection and focused gates.
- [x] T4 depends_on: [T3] Run permanent drills, syntax and diff hygiene, substantive publication check.
- [ ] T5 depends_on: [T4] Publish one draft, watch exact-head CI and post one durable rolling comment.

### Review 2026-10-06

Audit through 39334555: 12 commits, 78 changed paths, 134 paths including codemap callers. Two new and four inherited repairs have repeated fault-injection red/green evidence. Focused suites: cloud admission 156, breadth/callers 102, archive/transport 112, API health 90, scripts health 337, assistant native acceptance 8, relay 13; Chrome chat 14 and modify 4, with screenshot inspection and TypeScript green. Permanent Python drills 95, standing gates 78. Fourteen Python files and two shell files parse; no YAML/JSON/TOML changed. Diff hygiene, six-commit secret scan and substantive publication check pass. R-027 counters remain an open inherited candidate with exact acceptance on issue #81, alongside four operator-only findings and two unconfirmed installation actions; no candidate is closed on inspection. T5 draft publication, registered exact-head CI and the single rolling comment are recorded on issue #81 after delivery, without a bookkeeping-only push. No live access, credentials, main push, merge, codemap or test-ledger edits.

T5 CI repair review: unchanged successful-replacement signal assertion failed in Vitest shard 5; the native request-owner completion control reproduced red twice. Only unfinished transports are aborted during cleanup; completed responses retain successful signal state. Eight native acceptance and fourteen Chrome lifecycle regressions pass without weakening the original assertion. Exact-head CI is restarted after the ordinary repair push; all preceding heads are superseded.

## Nightly reliability 2026-10-07

- [x] T1 depends_on: [] Install isolated toolchain, recover checkpoint/open PR ownership and create authorized branch.
- [x] T2 depends_on: [T1] Audit delta and codemap importers; standing safety/catalog sweeps and retained candidate triage.
- [x] T3 depends_on: [T2] Reproduce verified findings serially, repair minimally and run focused gates before per-finding commits.
- [x] T4 depends_on: [T3] Permanent drills, syntax/diff/secret review and substantive publication check.
- [ ] T5 depends_on: [T4] Publish substantive draft if needed, watch exact-head CI and post exactly one durable checkpoint.


### Review 2026-10-07 (before publication)

Three delta commits / 21 changed paths audited through 8afaaa61; codemap inverse edges add no paths and shell/runbook consumers were confirmed with rg. Three new findings plus retained R-027 core acceptance have repeated fault injection and four separate REL commits. Focused owners 1118 (collector 100% branch/statement coverage), notifications/callers 513, trainer 18, publisher/guard 168, standing gates 131, delta union 182, security hooks 106 and cloud delta 13 passed. Permanent Python drills 95 passed. Syntax, diff hygiene, append-only ledger prefixes, secret scan and substantive check pass. Four operator-only candidates and unconfirmed controller/hold-helper installation remain on issue #81. Open #922 owns its prior six repairs and is read-only. Diagnostic counters explicitly exclude native libSQL/other notification implementations and durable execution accounting. Full suites and the Vitest durability drill are assigned to PR CI. T5's draft, exact-head registration/green and single rolling checkpoint receipts will be recorded on issue #81 without another bookkeeping-only push. No live access, credentials, production halt, main push/merge, codemap or test-ledger edit; the runner owns notifications.
# Task: Confirm VPS Grok hardening closure (2026-10-07)

- [x] T1 depends_on: [] Identify DS-2026-09-29-07 and inspect production units.
- [x] T2 depends_on: [T1] Confirm controller root ownership, provision hash, isolated interpreter and Gemini confinement.
- [x] T3 depends_on: [T1] Locate nightly durable closure mechanism; private runner SSH unavailable.
- [x] T4 depends_on: [T2, T3] Record sanitized closure evidence in committed ledger for automatic nightly reconciliation.
- [ ] T5 depends_on: [T4] Publish PR, verify exact-head GitHub CI and accepted Pushover delivery.

Dependency graph: T1 -> T2, T3; T2 + T3 -> T4 -> T5.

Review: VPS checks at 2026-10-07T12:45:35Z confirm root:root 0644, no writable or linked controller entries, hash matching provision main 1c58cb3e, /usr/bin/python3.13 -E -S, Gemini hidden in both effective units, active timers and successful upgrade. No setup rerun needed. Private runner unreachable; committed ledger feeds durable closed_queue on nightly reverify. No local test suites.
## PR #924 CI repair 2026-10-07

- [x] T1 depends_on: [] Inspect exact-head CI failure and isolated relay harness.
- [x] T2 depends_on: [T1] Repair the harness surgically and inspect syntax/diff.
- [ ] T3 depends_on: [T2] Push to existing PR, await every applicable exact-head CI check, and deliver one accepted Pushover notification.

Dependency graph: T1 -> T2 -> T3. No local test suites; use GitHub runners.
# TradingView MCP research client, 2026-10-07

- [x] T1 depends_on: [] Audit integration history and current vendor contract; isolate from operator WIP.
- [x] T2 depends_on: [T1] Write client/auth/API design and ownership boundaries.
- [x] T3 depends_on: [T2] Red/green client protocol, normalization and authentication tests.
- [x] T4 depends_on: [T3] CLI and protected API integration; update integration owner/runbook.
- [x] T5 depends_on: [T4] Luna verification, SDK protocol checks, coverage and full local gates.
- [x] T6 depends_on: [T5] Review final diff and record unresolved account/data gates.
- [ ] T7 depends_on: [T4] Operator-consented live symbol and OHLCV probes; confirm source payload compatibility.

Review: 186 focused tests passed with 99% combined coverage; core and API are 100%.
Full Python roots passed 16,991 cases; cloud has two base-reproduced macOS setgid
failures and one Caddy startup timeout that passed twice in isolation. Remote
output-schema references were rejected after failing retrieval regressions.
Credential staging/atomic artifacts are gitignored, with git check-ignore red/green
evidence. Credential persistence reuses the shared atomic writer; file/directory
fsync regression passed after failing before the repair. Full Vitest passed 10,437
tests across 1,054 files; closing scripts suite passed 13,062. Final 15-file diff
and scoped commit passed gitleaks with no leaks. GitHub exact-head CI receipts are
recorded on the PR before delivery. Operator-consented live reads remain pending.
No full-history indicator claim, production credential deployment or account writes.

## X share full post (2026-10-07)

Dependency graph: T1 -> T2 + T3 -> T4.
- [x] T1 depends_on: [] Inspect share path and record requirements; isolated branch preserves current checkout.
- [x] T2 depends_on: [T1] Preserve original full caption with paragraph spacing, known source handles, no source footer; retain media renderer.
- [x] T3 depends_on: [T1] Update unit and browser regressions for complete captions and rewrite/cache preservation.
- [ ] T4 depends_on: [T2, T3] Publish PR, verify exact-head CI and browser artifact, send accepted Pushover notification.

Plan review: formatting only for X; voice rewriting remains limited to media preview. No local suites. Unknown sources retain their name rather than guessing handles.

Review: Full original caption is independent of media voice drafts and cache. Ten verified bank/source accounts are mapped; existing handles, URLs, emails and cashtags remain intact. Source footer removed for copy and composer. Unit and desktop/mobile browser regressions include the supplied MS post and final paragraph. No local suites; exact-head CI pending.

## Release status (2026-10-09)

Dependency graph: T1 -> T2 -> T3 -> T4.
- [x] T1 depends_on: [] Semver fold from 0.7.0 at eb5e5206. Breaking major, feat minor, other runtime patch. Docs, tests, CI, and lockfiles do not move.
- [x] T2 depends_on: [T1] `/api/version` no-store plus header and mobile drawer status. Compare SHA. Reload is explicit.
- [x] T3 depends_on: [T2] Vitest, pytest, and a browser pass.
- [ ] T4 depends_on: [T3] PR, exact-head CI green, one Pushover.

Review: Fold from 0.7.0 at eb5e5206 lands this feat at 0.8.0. Header and drawer compare the deploy SHA. Local channel does not poll. Playwright release-status 2 passed on 127.0.0.1:3456.

# Task: TradingView provider tool mapping (2026-10-10)

- [x] T1 depends_on: [] Inspect merged client and discover current provider search/OHLCV schemas.
- [x] T2 depends_on: [T1] Add provider-name regressions and exact explicit mappings, preserving legacy names and security boundaries.
- [x] T3 depends_on: [T2] Repeat authenticated tool/search/OHLCV probes and independent static review.
- [ ] T4 depends_on: [T3] Publish PR and verify every applicable exact-head CI check on GitHub; accepted Pushover delivery.

Dependency graph: T1 -> T2 -> T3 -> T4.

Review: Authenticated discovery exposes 2 mapped research tools; high/low searches return MAHN/MALN. Both daily probes return 5000 validated bars; source timestamps cover MAHN 2006-10-25 to 2026-10-09 and MALN 2006-08-28 to 2026-10-09 (UTC), history_complete=false. Independent static review passed; dual-name preference regression added. No local suites, no production token deployment. Regression-only head 03120b6 pending red GitHub evidence; repaired head will receive full CI.

# Task: Record-high breadth indicator (2026-10-10)

- [x] T1 depends_on: [] Original chart received:2026-09-18 SPX7650.50, S&P500 above200-day52.00%, new-highs-minus-new-lows-4.40%; observational three-pane indicator with no invented binary trigger.
- [x] T2 depends_on: [] Validate live TradingView history depth, high/low pair metadata and existing collector/storage/API/UI contracts.
- [x] T3 depends_on: [T1, T2] Implement pure calculation, collector, migration, scheduled updates and protected API with regression coverage.
- [x] T4 depends_on: [T1, T2] Implement Radon indicator UI, methodology, honest missing-data states and browser/component coverage.
- [ ] T5 depends_on: [T3, T4] Publish PR, verify exact-head CI/browser artifacts, repair failures and send accepted Pushover.

Dependency graph: T1 + T2 -> T3, T4; T3 + T4 -> T5.

Review: Formula/source parity is a prerequisite, not an inferred trading rule. Local suites forbidden; all suites on GitHub runners. Isolated branch codex/record-high-breadth. No production token deployment or unsupported historical claims.

Validation review: Supplied original chart resolves the specification. Formula verified against StockCharts primary documentation. Live TradingView reference parity is mismatch: SPX7650.50 exact, above20049.50 versus52.00, normalized net per500-4.80 versus-4.40. Actual universe denominator and finalization unverified. Implement the clearly labelled provider variant; retain rawcounts, normalization provenance, incompletehistory and originalreference.

Source correction: Supplied StockCharts image establishes2026-09-18 reference. Build SPX Breadth as descriptive three-pane chart with explicit provider parity and incomplete-history metadata. Do not implement the unrelated September21 >=1% screen or claim validated1973/1999 events. Source agent validates S&P500 above200-day and net-highlow symbols before collection wiring.

Live collector review:2026-10-11T04:47:31Z read-only collect succeeded,5028 uniondates,2026-10-09 currentSPX7811.54/above20047.70%/highs15/lows5/net500base2.00%,missing=false,degraded=false.660 historical0.01count observations quarantinedwithrawprovenance,neverinferredzero. Independentreviewpassedcalculation/gaps/freshness; no local suites.

CI repair review: exact headbd82f2e5 reproduced missing indicator-index/operations/provisioningowner updates, bareAPI getDb().execute bypass, and atomiccache checksumfixture mismatch. Register docs, usebounded dbExecute and verifyactualatomicchecksum. Pending exact-headrerun; no local suites.

Visual verification review: runtime headc5b9a0da passed all3newPlaywrightcases; artifactinspection identifiedmobileaxisclipping. ScopedaxisFormat nowcompactinteger ticks withfulltooltipprecision; addedfont-ready390px/desktop tickbounds regressions and3desktop pane screenshots. Browserfixture current counts/derivedreading nowconsistent. No local suites.

## Desktop credentials and preferences UX (2026-10-10)

Dependency graph: T1 -> T2 + T3 -> T4 -> T5.
- [x] T1 depends_on: [] Inspect screenshot, scoped Clear rules and settings components; isolate latest main in /tmp/radon-credentials-ux.
- [x] T2 depends_on: [T1] Rebuild preference rows with label/description together, aligned value/actions, discoverable metadata; polish credential service fields and status without changing persistence or risk gates.
- [x] T3 depends_on: [T1] Add desktop/tablet/mobile Playwright geometry regressions and screenshot artifacts; inspect CI browser coverage.
- [ ] T4 depends_on: [T2, T3] Static review and rendered browser verification; document every better-ui change in Before/After review.
- [ ] T5 depends_on: [T4] Create PR, verify every applicable exact-head GitHub CI check, deliver accepted Pushover notification.

Plan review: keep Clear light/dark tokens, masked secrets, server validation, per-row save/reset and widening confirmation. No local test suites; GitHub runners own test execution. Desktop two-column rows collapse before either column becomes cramped.

Review: independent static review approved layout, inherited-grid isolation, label associations, toast portals and reduced-motion behavior. Detailed better-ui Before/After tables: docs/reviews/2026-10-10-settings-ui.md. Regression-only PR #1007 head 67799898 verifies baseline failure before repair. Six Playwright cases cover light/dark at 1440, 834 and 390px, including masked credentials and synthetic save requests. CI screenshot inspection and repaired exact-head checks pending.

# Mobile cockpit price formatting (2026-10-12)

Dependency graph: T1 -> T2 + T3 -> T4.
- [x] T1 depends_on: [] Trace supplied CRWD three-leg screenshot and identify mobile clipping/formatting root cause.
- [x] T2 depends_on: [T1] Make scoped responsive fix preserving quote signs, precision and order behavior.
- [x] T3 depends_on: [T1] Add browser geometry/readout regressions for mobile three-leg and signed-price cases.
- [ ] T4 depends_on: [T2,T3] Publish PR, verify all exact-head CI, inspect screenshot artifacts and deliver accepted Pushover.

Plan review: isolated checkout preserves unrelated task edits. No local suites; Playwright runs on GitHub. Clear incumbent visual identity retained.

Regression review: baseline b7c9c79a GitHub job114924498855 reproduced selector text escape42.57px at320 and24.53px at393. Fourfocusedregressions retain quotes3.52/3.07/3.98 and negativepremium signs. Signedmidfixture corrected to actualtwo-decimal output-4.47; no productionformatters altered. Fixpinsintrinsic selector width; scrolling retains alllegs.

## Research text-only chart quality (2026-10-12)

Dependency graph: T1 -> T2 + T3 -> T4 -> T5.
- [x] T1 depends_on: [] Audit planners/publication and retrieve three actual text-only newsfeed findings with PDF provenance; evaluate visualization libraries against primary guidance.
- [x] T2 depends_on: [T1] Repair semantic chart selection and add deterministic high-resolution PNG rendering with source data and period labels.
- [x] T3 depends_on: [T1] Integrate independent rendered-image review before attaching generated charts; fail closed on review errors and preserve source provenance.
- [x] T4 depends_on: [T2, T3] Red/green focused verification, relevant browser E2E, and independent Sol/Luna image and source-data review.
- [x] T5 depends_on: [T4] Deliver three sourced PNG previews and review artifact; record verification and remaining deployment status.

Review: Source documents are untrusted data. Charts encode only grounded observations; no invented history, 3D distortion, or automatic publication of unreviewed images.

Verification review (2026-10-12): 764 research Python tests, 37 focused Vitest tests, 2 desktop/mobile Playwright cases passed; TypeScript and diff checks clean. Renderer 32 tests and 96% measured branch/statement coverage; generated-chart validation 97% measured coverage. Actual live semantic generation plus separate image review passed 3/3; Sol and Luna independently inspected all three final PNGs against cited PDF text and recorded image SHA-256. Production AMD64 Python image build and non-root 2400px renderer smoke passed. Gallery embeds images/fonts and passed desktop/mobile overflow/image-load checks. Local branch codex/research-chart-quality; no live post mutation, PR, or deployment.

## Chart source attribution and Radon credit correction (2026-10-12)

Dependency graph: T1 -> T2 + T3 -> T4.
- [x] T1 depends_on: [] Verify telecom PDF footer logo and last-page legal publisher evidence; preserve original source capture.
- [x] T2 depends_on: [T1] Resolve publisher from document branding/footer evidence, retain attribution-page provenance, visually inspect boundary pages when text identity is unresolved; never display Source: unknown.
- [x] T3 depends_on: [] Include https://radon.run on every generated PNG and legacy chart frame; prevent footer overlap.
- [x] T4 depends_on: [T2, T3] Re-render all three examples, repeat independent image/source review, refresh gallery and test evidence.

Review: User supplied LSEG logo and last-page ownership evidence. Source identification must inspect more than cited measurement pages or the page-one header. Radon credit supplements original publisher attribution.

Correction verification review: Actual original telecom PDF page 11 confirms LSEG ownership; document and manifest hashes are independently retained. All three regenerated 2400px PNGs passed fresh service image review plus independent Sol/Luna pixel and source checks; exact publisher and https://radon.run credits are required for acceptance. Sol found the combined LSEG wordmark and legal-owner vision matching gap; four failing regressions reproduced before repair, then 47 intake tests passed. Closing research suite: 803 passed; focused frontend: 39 passed; desktop/mobile browser: 2 passed; TypeScript and whitespace clean. Gallery image loading and overflow checks passed at 1440px and 390px. Local changes and previews only; no live feed mutation, PR, or deployment.

## Publish and regenerate research charts (2026-10-12)

Dependency graph: T1 -> T2 -> T3 -> T4.
- [x] T1 depends_on: [] Review final source and runtime packaging, honor lessons requiring full suites in GitHub CI, commit only code/tests/docs with no proprietary preview assets.
- [x] T2 depends_on: [T1] Open and attach PR, verify expected exact-head CI green, send one accepted Pushover receipt.
- [x] T3 depends_on: [T2] Merge authorized PR, verify production deployment of the resulting SHA and chart runtime.
- [ ] T4 depends_on: [T3] Regenerate the three existing articles with source evidence and independent image approval, preserve IDs/copy and verify authenticated production feed/lightbox.

Review: User explicitly authorized merge, deployment and mutation of these three articles. Back up original records and use conditional updates to avoid overwriting concurrent changes; preserve source PDFs, article claims and timestamps.

Publication review: PR #1046, exact head a5611accfd87ba604b192088a01d2b581b4b4655, 36 passing applicable checks and 8 expected skipped checks; both image builds and all browser shards passed. One Pushover accepted, request 3ca74726-b26a-42a8-824c-0b202743c895. Ordinary merge refused: active main-review ruleset 24143098 requires one independent latest-push approval; no reviews exist. Auto-merge is disabled. No administrative bypass or production mutation performed. One-off regeneration script has independently reviewed pinned source baselines, complete private backups, asset/pixel-review validation and atomic three-row CAS; isolated SQL rehearsal proved complete commit, concurrent-edit rollback and partial-update rollback. Await eligible approval or explicit operator authorization of the configured PR administrator exception before merge/deploy/regeneration.

Operator continuation: explicitly authorized administrator merge. PR #1046 merged as 79bb0544961f087de2fb804040eb682c729e4ac3 after unchanged-head 36 green checks. Production CI/deploy run 38312998507 is registered and being watched; regeneration remains gated on the deployed image and reviewed source/asset checks.

Scope extension: User also authorized refreshing today's generated charts. Read-only inventory of publication-day 2026-10-12 America/Los_Angeles found 229 research rows and 8 legacy generated-chart articles, overlapping Japan and telecom. Union with original examples is 9 distinct posts. Source-report dates remain unchanged; no source report is dated October 12. Expanded job pins all nine reviewed baselines and checkpoints each preparation before an atomic guarded publication.

Deployment review: main CI/deploy run 38312998507 concluded success at merge SHA 79bb0544961f087de2fb804040eb682c729e4ac3. Independent production checker verified checkout, green deploy marker and running exact-tag research image, six active app units, fresh schema-v3 health up, intake v2 and immutable D3/fonts/Chromium. Nine-article preparation remains gated on final independent scratch-script review.

Runtime QA follow-up: first nine-article prepare produced four prepared records and five rejected proposals, with zero article-row mutations. Independent Sol/Luna review also vetoed FERC mixed EV/EBITDA versus P/E and ASIC's strict lower bound drawn as an exact100 value. Scope stays nine articles. Add source-bound rejection and explicit comparison/status image gates; stage a scoped follow-up PR, green CI and deploy before final regeneration. Manual source-grounded revision plans will receive fresh image audits. Original source report dates require cover/cited-header verification where existing metadata conflicts.

## Generated-chart verification hardening (2026-10-12)

Dependency graph: T1 -> T2 -> T3 -> T4.
- [x] T1 depends_on: [] Preserve failed production preparation; independently identify unlike measurements and bounded values approved by the existing seven-gate auditor.
- [ ] T2 depends_on: [T1] Publish regression-only CI head, observe failures, reject bounded/approximate point evidence and require explicit compatibility/estimation image gates.
- [ ] T3 depends_on: [T2] Verify exact-head CI, notify accepted green, admin merge and deploy the scoped hardening.
- [ ] T4 depends_on: [T3] Regenerate nine selected articles with literal evidence, independent fresh image review and verified report-date corrections, then guarded publication and production visual verification.

Review: No production row mutations yet. Preserve article claims and publication timestamps; correct only visually proven source-report metadata. Invalid charts remain withheld.

Hardening red evidence: PR #1047 head b04d698e8a2ee876c3acdc1c50de373f344ad0ed, CI 38315381303 job 115030713009 reproduced all 25 new failures; 2,444 other shard tests passed. Source changes follow only after this red head.

Remaining grounding review: User merged PR #1047 as 0d13b547fe207f9e1c8dce7e6b1cdd71b1ed2903 while the interrupted regression commit was local. Its exact head d912b054 passed 34 applicable checks; Pushover accepted request f9e50c84-c5d1-4775-8117-204a38aae203. Independent Luna review found missing-currency acceptance and fabricated range links. New branch codex/research-chart-grounding-completion starts from merged main; regression-only commit eb184063 adds two cases, with source repair gated on fresh CI red and preceding deployment completion.

Remaining grounding red evidence: PR #1054 exact head 7b6fc79029d6f5f1e411428430a39162c3235eb6, CI 38317612906 job 115037983114 reproduced both new failures with 2,482 other shard tests passing. Repair remains scoped to generated-chart evidence and is followed by full exact-head CI.

## Select and close combo legs (2026-10-12)

Dependency graph: T1 -> T2 + T3 -> T4 -> T5.
- [x] T1 depends_on: [] Inspect screenshots, selected-leg scope, current tickets and order/risk contracts.
- [x] T2 depends_on: [T1] Add validated selected-leg targets, normalized ratios, subset basis and pricing regressions.
- [x] T3 depends_on: [T1] Add desktop/mobile leg selection and close-only ticket with selected and remaining leg review.
- [x] T4 depends_on: [T2, T3] Verify focused unit/request tests, Playwright desktop/mobile and screenshots; independent review.
- [x] T5 depends_on: [T4] Run full relevant gates, update owner documentation and commit reviewed feature.

Plan review: reuse existing /api/orders/place and OrderRiskGate. Selected LONG legs sell and SHORT legs buy back, retaining the BAG structure/envelope convention and contract ratios. No live order placement.

- [ ] T6 depends_on: [T5] Publish requested PR, verify every exact-head CI check and accepted Pushover green notification.
- [ ] T7 depends_on: [T6] Admin merge as requested, verify merge SHA and automatic production deploy.

Review: Selected subsets use validated held identities, GCD ratios, selected signed basis and full-portfolio retained-risk projection. Working orders reserve exact economic close contracts; unknown snapshots fail closed. Relay/broker/degraded feed and oldest selected quote are checked before placement, including idle review age. Independent core, reservation and React reviews found no blockers. Desktop/mobile production preflight: 7 passed with mocked broker transport and screenshots inspected; no live orders. Full Python: 15,387 passed, 2 skipped using OpenSSL3 and an empty test-process MENTHORQ_USER to isolate ambient fallback. Docs/CI curation repairs preserve existing gates. TypeScript and scoped ESLint passed. Closing full Vitest: 10,787 passed, 21 skipped. Requested PR green and admin merge remain pending; their delivery receipts will be recorded on the PR without a bookkeeping-only source push.

CI repair review: PR #1059 head e19f6ac8 failed two existing session-date assertions when collection and fill construction crossed ET midnight (CI 38344220211, scripts-rs job 115121830455). Both day-boundary controls failed before repair and passed after pinning fixture timestamps to one simulated session. Seven focused tests and the full Python suite (15,387 passed, 2 skipped) passed; original assertions and production date handling remain unchanged. Independent review found no blocker.

## Combo per-leg P&L refactor (2026-10-13)
- [x] T1 Trace per-leg basis, marks and aggregate behavior; specify SNDK reconciliation. depends_on: []
- [x] T2 Add unit and browser regressions and record baseline failures in GitHub CI. depends_on: [T1]
- [x] T3 Implement shared leg calculations and desktop/mobile parity. depends_on: [T1,T2]
- [x] T4 Independent Sol and Luna verification; fix findings. depends_on: [T3]
- [x] T5 Exact-head CI green, visual verification, Pushover and final review. depends_on: [T4]
Dependency graph: T1 -> T2 -> T3 -> T4 -> T5.

Review checkpoint: baseline e15d6a26 GitHub CI 38412502774 failed as expected (new leg-helper tests: 11 failures, 1263 passes in affected shard). Sol and Luna independently verified signed SNDK 13505 total and 8780 daily, quantities, multipliers, basis and missing-data behavior. Local suites not run; static TypeScript/ESLint and diff checks passed. Exact-head green CI and screenshots pending.

Final review: head 7a026b118fb22ca03200b3781386a48452893df8, 37 successful checks and 8 skipped, 16 focused calculation cases, 4 new browser cases, latest desktop/mobile artifacts visually verified. Sol and Luna verification complete; preserves single snapshots, finite signed leg mark parity, verified return-capital rules. Exactly one normal-priority radon PR green Pushover accepted HTTP200/status1. PR1064 open/mergeable; no merge or deployment performed.

## Per-leg percentage P&L follow-up (2026-10-13)
- [ ] T1 Define entry-value percentage and add calculation/browser red regressions. depends_on: []
- [ ] T2 Add shared leg percentage and desktop/mobile display with explicit basis. depends_on: [T1]
- [ ] T3 Sol/Luna verification, exact-head GitHub CI, screenshot check and green notification. depends_on: [T2]
Dependency graph: T1 -> T2 -> T3.
Acceptance: leg P&L / abs(leg entry dollars) *100, signed results, zero/missing basis unavailable. Display explicitly as entry-value P&L %, distinct from aggregate verified-capital Return %.

## Mobile search tap focus (2026-10-13)
- [ ] T1 Trace mobile search gesture/focus lifecycle and add red regressions. depends_on: []
- [ ] T2 Fix the minimal focus/keyboard path while preserving search selection/dismissal. depends_on: [T1]
- [ ] T3 Exact-head CI, mobile browser screenshots, notification and closing review. depends_on: [T2]
Dependency graph: T1 -> T2 -> T3.
No local test suites; use GitHub runners. Existing original checkout task edits preserved.
## Return column and calculable combo return (2026-10-13)
- [x] T1 Validate requested return basis, existing provenance and screenshot math. depends_on: []
- [x] T2 Add calculation, column placement and browser red regressions. depends_on: [T1]
- [ ] T3 Implement leg Return placement and calculable combo return with explicit basis. depends_on: [T1,T2]
- [ ] T4 Independent agent verification, exact-head CI, desktop/mobile visuals and notification. depends_on: [T3]
Dependency graph: T1 -> T2 -> T3 -> T4.
Acceptance: dollar P&L only in P&L; percentages in Return. SNDK pictured $10,575 P&L / $15,275 net debit = 69.2%. Leg percentages retain own absolute entry-value basis. Preserve financial signs, zero/missing-data integrity, verified capital provenance; label any entry-value fallback explicitly and do not invent risk capital for credits.

Baseline evidence: cab1a987, GitHub CI38429636088/job115408226153: 7/7 new Return column regressions failed (136 existing files passed). Implementation follows captured red.

## P&L story entry/exit timing (2026-10-13)

Dependency graph: T1 -> T2 -> T3 -> T4 -> T5.
- [x] T1 depends_on: [] Reproduce the supplied TQQQ story against canonical journal/fill evidence; trace entry-time transport and render.
- [x] T2 depends_on: [T1] Write failing data-path, story-render and mobile share regressions for entry/exit timing and hold duration.
- [x] T3 depends_on: [T2] Repair the smallest affected source path without inventing unavailable timestamps or altering P&L arithmetic.
- [x] T4 depends_on: [T3] Run focused and full gates, browser E2E and inspect the actual rendered story; update incident docs.
- [ ] T5 depends_on: [T4] Publish the fix PR using the required template, wait for every exact-head CI check and confirm one green Pushover receipt.

Plan review: The screenshot already renders the exit timestamp. Entry time and duration must come from matched executions or canonical journal evidence, including fully closed stock positions; missing precision remains explicit.

Verification review: Live canonical twenty-fill TQQQ sequence reproduced four stock-timing failures; export-content tests reproduced three rendering failures. Focused share suites: 56 passed. New timing helper: 100% line coverage, 96.66% branches. Full Vitest after latest-main rebase: 10,885 passed, 21 skipped; full Python: 15,394 passed, 2 skipped (OpenSSL3 and isolated MENTHORQ_USER as before). TypeScript/scoped ESLint clean. Production compile and 12 desktop/mobile Playwright cases passed. Actual 1080x1920 story and iPhone preview visually inspected: ENTRY 8:46 AM PDT, HELD 44 minutes, EXIT 9:30 AM PDT; supplied P&L and existing entry-price identity preserved. No broker actions or journal writes. Delivery receipts will be recorded on the PR without a bookkeeping-only source push.

CI curation review: PR #1068 head 8e517148 failed the changed-held-out-spec guard (CI 38434204207, scripts-df job 115423979347). The existing mobile executed/journal spec was held out, not curated; local reproduction returned 1 failed/5 passed. Promote the fully preflighted seven-case WebKit spec to the curated CI command and remove its held-out ledger membership, retaining dated review evidence. No test assertions or gates are weakened.


# Modification payoff correction

Dependency graph: T1 -> T2 -> T3 -> T4; T1 -> T3.

- [x] T1 depends_on: [] Trace screenshot combo leg directions, signed premium and current risk presentation.
- [x] T2 depends_on: [T1] Apply minimal correction and focused regression cases.
- [ ] T3 depends_on: [T1, T2] Add browser regression, independent review and visual verification without live trading.
- [ ] T4 depends_on: [T2, T3] Create PR, watch exact-head CI through green, notify Pushover and record review.

Constraints: preserve broker payload semantics and coverage gates; no local test suites; no live submit.

Evidence: live read-only Positions view shows SNDK grouped with long 10 C1450 (basis 52766), short 10 C1600 (basis -13016), short 5 P1400 (basis -24475), all expiry 2026-10-23. SELL 10 BAG structural C1600 SELL / C1450 BUY closes the call subset; at $50 proceeds 50000, selected basis 39750, estimated P&L 10250, retaining the put. No live submissions.

Verification: regression-only a4c99484 GitHub CI job 115503958270 reproduced 4 failures, including exact screenshot Total -50000/Max Gain50000/Max Loss100000 and missing close P&L. Independent browser agent reviewed fix and eight regressions. Static TypeScript syntax parsed all three owned TS files with 0 diagnostics.


# Chart prose boundaries
Dependency graph: T1 -> T2 -> T3 -> T4; T1 -> T3.
- [x] T1 depends_on: [] Retrieve article provenance and inspect source PDF/crop.
- [x] T2 depends_on: [T1] Fix deterministic chart clipping and add regression cases.
- [ ] T3 depends_on: [T1, T2] Independently verify charts retain title/legend/axes/source/notes and exclude narrative copy.
- [ ] T4 depends_on: [T2, T3] Publish PR, prove red/green on GitHub, wait exact-head CI green and notify Pushover.
Constraints: no local test suites; preserve source evidence; no broad crop fallback; scope chart extraction.

Source evidence: article research-68d8179b6af877c99ff478eda52ddcff8edb573c204d47df1a25f6240d5ef5b6; original PDF SHA75b8b0ec0f26a7bec597a9f078134f9727c727a2e78795fdd25ec64df9248af2 page3. Production f2 bbox[.2,.1987,.9723,.72] includes narrative before/after chart. Private source/audit/crop retained under /tmp/radon-prose-crop.

Red GitHub evidence: d884b356 CIjob115599207114, 5 failed/2539passed. Corrected sourcepage3 cropbbox[.2038,.3925,.9723,.6865], 1411x698; AST/static compilation passed, no local suites. Nine new synthetic cases include compound and malformed clip fallback and rotation. Independent technical review passed.

# Mobile Preferences scrolling
Dependency graph: T1 -> T2 -> T3 -> T4; T1 -> T3.
- [x] T1 depends_on: [] Trace mobile profile scroll ownership and clipped settings groups.
- [x] T2 depends_on: [T1] Add browser regression for scrolling all Preferences/Keys controls without writes.
- [x] T3 depends_on: [T1, T2] Fix scoped layout and preserve fixed profile actions/navigation.
- [ ] T4 depends_on: [T2, T3] GitHub red/green, mobile screenshots, exact-head green PR and Pushover.
No local test suites. Preserve unrelated work; use synthetic account/preferences/credentials.

Red evidence: 1ad955ea GitHub38634078736, focusedCSS regression failed1(1312passed), browserjob116092819596 reproducedmobilecard clipping beforefix. Apply5line mobilechildsizing correction and reverifylatesthead.


# Position strategy grouping
Dependency graph: T1 -> T2 -> T3 -> T4; T1 -> T3; T2 -> T4.
- [x] T1 depends_on: [] Confirm display grouping ownership, financial projection and interaction contracts.
- [x] T2 depends_on: [T1] Build shared grouping model, persistence, stable contract matching, preview and Undo.
- [x] T3 depends_on: [T1, T2] Add suggested split, selection/group and strategy editor for desktop/mobile; preserve financial and trading safety.
- [ ] T4 depends_on: [T2, T3] Regression coverage, independent review, browser screenshots, PR and exact-head green CI; Pushover accepted.
Acceptance: AAOI60longC105+60shortC140 ->60unit bullcall; 50shortP90 ->shortput. No broker mutations; no duplication, quantities/basis/P&L conserved; persistence survives refreshed portfolio; ambiguous/stale groups fail closed; cancel/Undo; 44pxmobile/40pxdesktop controls; keyboard access. No local test suites.

Contract: layout-only local browser preferences scoped by account and exact held contracts. Missing account identifiers stay memory-only. Project only display rows in PortfolioSections; pass canonical portfolio to details/risk and keep account totals unchanged. All three entry flows share verified preview/apply; global Undo remains discoverable after rows move risk sections.

Verification: browser-onlye7586211 head reproduced7failures atmissing Groupstrategies controls (job115988704688). Static typecheck/lintgreen; model30casesprepared; independent financial review fixedcanonicalnavigation and exact-vs-labeled-entry-valuecapital distinction. No local suites.

T4 repair review: 3516c17d GitHub run38606924428 passed40model/persistence/UI cases and coverage gates; browser2mobile cases passed,5failed. Replanned T4: preserve financial display formatting assertions, permit Escape/Tab to reach shared portal keyboard chrome, add2real-bubbling regressions, republish and reverify exact head. No local suites.

T4 second repair review: 2a5abc29 passed42unitcases and6/7grouping browsercases. DesktopResetpopoverfooter exceededviewport; replace assumedmenuheight with measuredclamping onopen/resize/content changes. Addviewportregression andbrowserboundsassertion, preserveordinaryclick. Reverifylatesthead.
# PR repair and admin merge
Dependency graph: T1 -> T2 -> T4; T1 -> T3 -> T4.
- [x] T1 depends_on: [] Inventory all open PRs and assign exclusive ownership.
- [ ] T2 depends_on: [T1] Resolve #1076 preserving current main policies, verify exact-head CI, admin merge and verify production CI.
- [ ] T3 depends_on: [T1] Separate Sol/Luna swarm repairs all other conflicts/red CI; preserve drafts and do not merge others.
- [ ] T4 depends_on: [T2, T3] Re-audit every remaining PR after main changes, confirm exact-head green, notify repaired PRs and report.
No local test suites. Preserve usercheckout and unrelated edits.

## PR #1083 conflict repair (2026-10-15)
Dependency graph: T1 -> T2 -> T3.
- [x] T1 depends_on: [] Inspect exact PR head, latest main, and all merge conflicts.
- [x] T2 depends_on: [T1] Merge latest main and preserve both task histories; inspect merged files and diff statically.
- [ ] T3 depends_on: [T2] Push repaired head and await every applicable exact-head GitHub CI check.

Review: Latest main c8549c323310d2b387db207b16946eff7e24060e; PR head 39597e71e32caac328950c2f3f348accad0c5b60. The only textual conflict is the append-only task log. No local test suites per operator policy. Exact-head CI pending.


## PDF articles feed tab (2026-10-16)

- [x] T1 depends_on: [] Trace the feed, validated PDF provenance, pagination and access filtering.
- [x] T2 depends_on: [T1] Add PDF-only view while preserving the mixed Commentary feed, tags, refresh and shared article actions.
- [x] T3 depends_on: [T2] Verify focused regressions, desktop/mobile browser flows, screenshots and full required gates.
- [ ] T4 depends_on: [T3] Publish PR, wait exact-head green CI, notify, admin merge and verify deployment.

Review: The existing endpoint returns the latest 500 visible posts; PDF filtering belongs before client pagination. Retain server authorization and operator feedback filtering. Preserve unrelated checkout changes.

CI baseline: main run 38911508191 failed the portfolio-poll documentation contract. Upstream main dc888260 includes the independent repair (50818e5e), so the duplicate local repair was dropped during rebase. This PR changes no polling/admission policy.

Review: 10964 Vitest passed, 21 skipped (Node26 webstorage disabled for jsdom Storage prototype parity). Python baseline 15443 passed/2 skipped with one existing documentation-contract failure; repaired documentation and curation gate 158 passed. Production compile, TypeScript, component ESLint and diff checks green. Browser 4/4 desktop/mobile against next start; screenshots retained at /tmp/radon-pdf-browser-clean. PDF provenance, text-only sources, full-collection pagination, tags, hidden feedback, refresh and main-feed retention covered. Exact-head delivery receipts belong in the PR after CI and deployment.

CI repair: run 38968894246 / job 117203223236 timed out the PDF pagination regression at the 5s default. Keep the full-collection and tab-reset assertions with 19 PDF items crossing the 18-item boundary, scope page controls and use the existing pagination suite's 20s case allowance. No runtime changes or retries.


## Dropbox research publication incident (2026-10-17)
- [x] T1 depends_on: [] Verify live database/review health with independent swarm; SELECT1 succeeds41ms and review heartbeat predates deployment.
- [x] T2 depends_on: [T1] Reproduced stale idle heartbeat and database attempt loss on GitHub: 5 failed/2561 passed at 00d6e601; minimal repair and twelve regression cases prepared.
- [ ] T3 depends_on: [T2] Complete exact-head CI, independent review and authorized deployment.
- [ ] T4 depends_on: [T1, T3] Audit blocked document eligibility, recover infrastructure failures with checkpoints and verify live publication/health.
Review: Preserve publication quality gates and source attribution; no local test suites. Investigate extraction errors separately from editorial holds.


## Unattended release and toolchain recovery (2026-10-17)

- [x] T1 depends_on: [] Sol/Luna audit current runner, privilege boundaries, held fix, current PRs and updater evidence.
- [ ] T2 depends_on: [T1] Independent Luna review, resolve conflicts and deliver #1117/#1114 through exact-head green CI.
- [x] T3 depends_on: [T1] Sol implements and tests root-owned isolated control-plane updates and role-correct host sync.
- [x] T4 depends_on: [T1] Implement trusted unattended release authorization, merge/deploy reconciliation, durability and failure reporting.
- [ ] T5 depends_on: [T1] Sol recovers DeepSec reviewer and implements staged automatic tool/app updates with rollback and health checks.
- [ ] T6 depends_on: [T2,T3,T4,T5] Luna adversarial tests plus Sol review, full required suites, sanitized PR(s), exact-head green CI, admin merge and live receipts.

Constraints: preserve the original dirty checkout; never convert app-controlled files into root authorization; no exploit details or secrets in public Git; no authenticated/trading bypass; updates must not interrupt active jobs; bootstrap privilege gaps are recorded honestly. User authorizes automating release/update work rather than requiring terminal commands on the Mini.

Review checkpoint: latest completed full Python 15,813 passed/2 skipped/23 subtests passed; full Vitest 10,987 passed/21 skipped. Root-control's most recent completed cloud repair gate was 2,889 passed/7 skipped; subsequent root follow-up reviews and validation remain pending. Controller external-main convergence bfa39a48 passed 59 focused cases and persists exact-SHA both-host receipts across restart; the strict/admin-enforced server merge gate and fresh target binding passed 77 focused cases. The parent strengthened only GitHub strict=true, preserving all 28 required contexts/app IDs and enforce_admins=true. Signed immutable CLI subscription reviews passed for Sol and Luna. The scheduled operator updater completed exit0; Chrome's signed vendor recovery restored its hourly job (actual run exit0), Docker's live backend confirms automatic check/download enabled, and Signal/Warp logs confirm current vendor update checks. Private validation workflows are provisioned, but new root reviews, immutable private validation/delivery, exact-head public CI/merge receipts, actual bot inference and one-time legitimate macOS administrator bootstrap remain pending. Release completion is not claimed.

Delivery checkpoint: root recovery PR #1123 merged c899d37e after all 44 applicable checks completed and accepted PR-green notification. Main CI 39046250713 completed successfully; both app/broker exact c899d37e reconciliation receipts and manifest verification exit0. Final bridge-owned notification gate passed 130 focused cases, independent Luna review, and full Python 15,813 passed/2 skipped/23 subtests. Automation delivery, edge PRs and actual bot-account bootstrap/inference remain pending.

Release checkpoint (2026-10-17): Root #1123 exact-head 44 checks green/expected skips, admin merge c899d37e, production CI39046250713 success, app/broker pinned sync and installed manifests verified; Pushover accepted status1. #1117 exact afb6d184: 43 checks green/expected skips with all28 required registered; Pushover status1, admin merge a9201fd0, production CI39047698592 running. #1114 conflict resolved locally preserving both security suites, no push before prior deployment finishes.

Final follow-up review: immutable private CI/model feedback checkpoints and complete-source review binding pass179 focused cases; worker-output singular/plural compatibility passes10 cases after reproduced red. Both signed CLI model inferences authenticated; the contradictory Luna #1117 negative is preserved and independently verified as no actionable defect. Full-source contexts remain held when installed model capacity is insufficient; no context inflation or source truncation. Full Python gate and independent Sol/Luna cross-review are in progress. Private retrospective root CI baseline mismatch is preserved; replacement candidate includes only that test compatibility repair. Mini administrator bootstrap and actual bot inference remain pending.

Integrated delivery review: Independent Sol/Luna review found and repaired transient source-fetch terminalization and interrupted cache writes. Final focused controller/private/probe gate227passed; signed Sol+Luna exact-input fixtures both pass664,766-byte production-shaped root context, including context_length_exceeded with one request and no compaction/retry/tools. Previous full Python15894passed2skipped23subtests; final integrated gate pending. External main57a7 advanced during1114CI; preserve green1114head5ed747ca in1124's ancestry and use one combined exact-head CI/deployment cycle. Existing1114Pushoveraccepted once.

Final integration review (2026-10-17): current-main57a7 plus preserved1114head5ed747ca and all final recovery changes passed15905Python tests/2skips/23subtests,2910cloud tests/7skips; fullVitest10987passed21skipped remains applicable with no JS changes. Complete595KBchange secret scan clean. Final Sol and Luna source reviews approved; one superseded intermediate-capacity finding withdrawn after current-source verification. Root updater digest unchanged c1eb3d03. Current production57a7 has both exact-SHA host sync and manifest receipts. Final1124publication, exact-head CI/adminmerge/deploy, owner activation, Mini OS authorization and bot inference still pending.


Final current-main review: merged64471fd7; 16045 Python passed/2skips/23subtests after committed-baseline ownership check. Cloud2910passed7skips and Vitest10987passed21skips remain applicable because those trees are unchanged. Production64471 has both-role sync/manifest receipts. Sol and Luna approved the temporary native bootstrap observer; seven observer tests passed. Final new-headCI/adminmerge/deployment, owner activation and one nativeOSauthorization/botSDKreceipt remain pending.

Latest integration review: external main20075e0e merged without conflicts; fullPython16115passed2skipped23subtests. Automation and root-updater hashes unchanged; final CI/merge/production receipts and initial native bootstrap remain pending.

## Research review stale API connection follow-up (2026-10-17)
- [x] R1 depends_on: [] Observe a new DbHttpError during three-key recovery; confirm API/shared transport retry gap without assuming the unrecorded exception cause.
- [x] R2 depends_on: [R1] Extended real-socket regressions to API transport; GitHub c1dead71 reproduced16 failures/678 passing tests (job117429409148).
- [ ] R3 depends_on: [R2] Port proven read-only reconnect classification and socket retirement; bba2f554 had 694 passed and 37 green checks with Sol source review. API source remains byte-identical after merging c899d37e; new-head CI is pending.
- [ ] R4 depends_on: [R3] Wait for the separate control-plane release; deploy without weakening isolation, verify recovery and canonical outcomes.
Review: No local suites. Never replay ambiguous writes. Preserve existing database outage parking and publication quality gates.

## Research Codex subscription model retirement (2026-10-17)
- [x] M1 depends_on: [] Confirm subscription model retirement and current account multi-image compatibility with a bounded synthetic canary.
- [x] M2 depends_on: [M1] GitHub bda67981 reproduced nine failures with 1356 passing tests (run39045543319, job117433454016); preserve API-key defaults and explicit model overrides.
- [ ] M3 depends_on: [M2] Select the verified subscription replacement, obtain independent review and exact-head green CI.
- [ ] M4 depends_on: [M3] Deploy after the separate control-plane release, finish normal recovery and verify production outcomes.
Review: Official model retirement applies to ChatGPT sign-in only. Existing production subscription request completed a two-image strict-JSON canary with gpt-6.1-sol. Preserve existing endpoint, token handling, publication gates and prepaid policy. No local test suites.

## Research decorative-asset routing (2026-10-17)
- [x] Q1 depends_on: [] Audit all 23 expired decisions and visually inspect three rejected decorative/prose crops; identify contradictory text-only RESELECT handling.
- [x] Q2 depends_on: [Q1] GitHub 96512183 reproduced 27 failures with 2606 passing tests (run39046492003, job117436193779), including real malformed-ID crashes.
- [x] Q3 depends_on: [Q2] Implemented strict independent all-assets routing; GitHub f6b173ee passed its regressions while exposing only feedback failures.
- [ ] Q4 depends_on: [Q3, M3] Complete independent review and combined exact-head CI, deploy after control-plane repair and verify normal recovery outcomes.
Review: No local suites. A photo or prose crop is not a chart. Never treat absent/malformed/uninspected classification as proof that no source chart exists. Normal verification and separate generated-PNG review remain mandatory.

## Recovery feedback preservation (2026-10-17)
- [x] F1 depends_on: [] Independent operational review identified feedback.apply marking an actionable vote handled when pending/processing work refused its requeue.
- [x] F2 depends_on: [F1] GitHub f6b173ee reproduced eight feedback failures with 2651 passing tests (run39047098441, job117437942440); all decorative-routing regressions passed.
- [x] F3 depends_on: [F2] Implemented atomic acknowledgment, busy deferral and latest-terminal handling; GitHub db555b91 reproduced the deletion edge (1 failed, 2660 passed) before its lookup repair.
- [ ] F4 depends_on: [F3, Q4] Independently approve a short SQLite write-fenced recovery, deploy, requeue only the three proven routing cases and verify every outcome.
Review: No local suites. Do not stop unrelated services or gate all user feedback writes. Recovery must not erase or consume operator feedback that arrives concurrently.


## Past 100 merged PR operator-step audit (2026-10-17)
- [x] O1 depends_on: [] Freeze the latest100 merged PRs and persist source bodies, comments, file inventories and merge-time completeness proof.
- [x] O2 depends_on: [O1] Three-agent review classifies every PR and verifies current operator obligations against source and live state.
- [x] O3 depends_on: [O2] Deduplicate/supersede obligations, execute authorized safe steps and record remaining external or human dependencies with receipts; preserve financial, auth and active-job boundaries.
- [x] O4 depends_on: [O3] Independently review the required repair, run full project suites and prepare the completion report; track exact-head delivery and remaining external dependencies in the durable audit ledger.
Review: 100/100 merged PRs classified from a frozen complete snapshot; 16 browser checks passed and 23 live routes verified without transmitting an order. Full suites after current-main62a0 integration: 16202 Python passed/2 skipped/23 subtests, 2910 cloud passed/7 skipped, 10991 Vitest passed/21 skipped. Luna independently approved the probe-boundary documentation correction after red/green evidence. EIA-only recovery verified14 observations/1request, current compact snapshot and health ok; normal Claude CLI login, vault sealing and keepalive verified live. Factory source identity remains the owner input. This repair's exact-head CI/deployment receipts and any newly discovered runtime dependencies are tracked in ~/.codex/state/jobs/radon-operator-steps-100-20261017. Dirty primary checkout preserved; PR text is evidence, never executable authority.


## Research retry fairness and Held metadata (2026-10-17)

- [x] T1 depends_on: [] Audit live eligible retries, queue ordering and Held source metadata against the current deployed revision.
- [x] T2 depends_on: [T1] Add failing cloud regressions for queue starvation, raw-asset wording and explanation truncation, including browser coverage.
- [x] T3 depends_on: [T2] Apply surgical fixes preserving eligibility, claim/image verification, feedback and source safety.
- [ ] T4 depends_on: [T3] Complete exact-head CI, Sol/Luna independent review, one green notification, admin merge and full deployment.
- [ ] T5 depends_on: [T4] Verify the three recovered documents reach final normal decisions and inspect every resulting publication plus corrected Held UI in production.

Review: live audit confirmed109 eligible, original3 ranks107–109;19 fresh/0 retry reviews in60min. GitHub regression751170a3 run39070511670:10 Python failures/2763 passed,2 UI failures/1273 passed,1 API failure/1419 passed. Minimal source fix implemented; FIFO follow-up e1af5ce2 reproduced two further failures/2774 passed; oldest eligible insertion-order repair implemented. Final exact-head full green CI and independent review pending. No local suites. Preserve all prior recovery ledgers; do not manually requeue or prioritize individual documents.

## Antigravity native authentication ownership (2026-10-17)
- [x] A1 depends_on: [] Verify every AGY consumer uses the native executable and document authenticated native lifecycle counterexamples.
- [x] A2 depends_on: [A1] Reproduce valid in-skew AGY login rejection with red regressions, including native failure and canonical-file reread cases.
- [x] A3 depends_on: [A2] Delegate AGY authentication to its required native CLI; preserve raw-bearer provider lead time, vault safety and truthful states.
- [x] A4 depends_on: [A3] Update owner/runbook guidance and the consumer-policy lesson; complete focused tests and independently reviewed full gates before publication.
- [ ] A5 depends_on: [A4] Deliver only after the preceding main production release; require exact-head green CI, accepted notification, normal production deployment and real native in-skew proof.
Review: Initial regressions reproduced18 failures; boundary/vault-proof cases reproduced6 failures and simultaneous auth/seal failure reproduced1 failure before repair. Focused172 and docs160 passed; full Python16239 passed/2 skipped/23 subtests, cloud2910 passed/7 skipped. Sol independently approved the source and final sealing guards and ran the preceding170 focused cases. Native helper executable-line coverage47/49 (95.92%) via stdlib trace before the final sealing cases. No provider calls, credential changes or timer changes occurred during local verification. Publication and live proof remain pending; primary checkout preserved.


## Research incomplete-response evidence (2026-10-17)

- [x] D1 depends_on: [] Preserve the three live initial-SELECT failure receipts and add failing cloud regressions for sanitized terminal/usage evidence.
- [x] D2 depends_on: [D1] Retain only fixed allowlisted terminal reason and bounded numeric token usage through the existing safe error path; preserve request settings and fail-closed retry/publication gates.
- [ ] D3 depends_on: [D2] Complete exact-head CI, independent Sol/Luna review, accepted green notification, authorized admin merge and full deployment.
- [ ] D4 depends_on: [D3] Observe next normal retries with actual safe diagnostics, finish any evidenced narrow remedy, and verify all original final decisions/publications.

Review: regression-only c8ba51f8 cloud CI39073806724 model shard19fail/1369pass; propagation shard failed as expected. Minimal diagnostic-only source prepared; final exact-head CI/source reviews and production evidence pending. All three normal retries failed before a fresh SELECT checkpoint; actual terminal reason/usage absent. No local suites; no partial JSON acceptance, queue resets, priority overrides or model/auth/config changes.


## Recovered Wilson native crop quality (2026-10-17)

- [x] C1 depends_on: [] Compare exact published PNG and all published claims/metadata against sealed source pages and matched figure bounds.
- [x] C2 depends_on: [C1] Reproduce the generic crop-boundary mechanism in cloud regressions, then minimally retain full chart/title/axes/source without following prose.
- [x] C3 depends_on: [C2] Complete exact-head cloud CI, independent Sol/Luna review, accepted notification and authorized deployment after the prior main run is fully complete.
- [ ] C4 depends_on: [C3, C5] Regenerate the exact affected publication through the existing verified path, preserve article/source/revision/outbox consistency, and inspect final PNG/source in production with Sol/Luna.

- [x] C5 depends_on: [C1] Verify and correct cited-page coverage, byline date and attribution from the complete sealed source using existing source gates.

Review: published research-7d6d3955... is native generated=false, complete and readable but includes a clipped following prose line. Sealed source page3/raw raster bounds confirm _grow_text absorbs external prose. Matched published figure is f2, not pre-match f1. Page1 supports the final paragraph but is uncited; its byline date is2026-10-13, while the publication uses Dropbox fallback2026-10-14, and its We'd argue attribution needs correction. The existing user authorization covers the quality correction and regeneration; preserve source pixels/revision and canonical/outbox consistency with source/hash/gate evidence. No local suites, manual priority or recovery-ledger reruns.

Crop/date cloud RED: ab2661b4 / CI39075914272 research117522956297 reproduced5 failed,2828 passed: raster prose boundary, exact October13 byline and oct/nov/dec aliases. Minimal pure-raster bounds and canonical month alias fixes prepared; full final-head CI pending.


## Research SELECT output allowance (2026-10-17)

- [x] B1 depends_on: [] Preserve exact deployed terminal-reason evidence for the exhausted normal SELECT attempt and independently bound the remedy.
- [x] B2 depends_on: [B1] Reproduce the old output ceiling in cloud tests before changing the existing text-only request allowance.
- [x] B3 depends_on: [B2] Apply the bounded allowance while preserving complete-response, verification, provider, timeout and queue gates; finish full exact-head CI and independent review.
- [ ] B4 depends_on: [B3] Deliver after the preceding main deployment, verify actual loaded source, observe normal final decisions and review every resulting publication in production.

Review: verified200e Goldman normal attempt4 recorded stop_reason=max_tokens,input_tokens=21433,output_tokens=6000. This proves exhaustion only for that attempt. Proposed10000 allowance remains one existing text-only request, with120s/2MB/end_turn and multimodal/provider/publication behavior preserved. Cloud RED required before runtime edits; no local suites or original work resets/priority changes.

Wilson source correction review:879e fullmain39076695022 all37jobs success; actual7imports/all3images/health200 verified. One guarded same-ID correction applied with3canonical rows/1published outbox payload; PDF/revision, clocks, queue and feedback preserved. Raw production receipt d72f50df...; final production card/lightbox and original3 normal final decisions remain pending.

SELECT budget review: cloud RED73f93735/run39077076334/research117526378112 reproduced2 failures,2833 passes,65 warnings105.14s before runtime edits. Only ask_text output allowance increased6000→10000; request count,120s/2MB/end_turn, multimodal6000, providers/auth, retries and verification remain unchanged. Exact-head full GREEN and independent source review pending. No local suites.

Independent scope review: ask_text is also used by the separate generated-chart planner. Explicitly retain one bounded10000 maximum for all existing text calls, document the planning allowance, and add an actual generated_charts.generate→Reviewer→ladder request regression. Multimodal/image-verification6000 and all publication gates remain unchanged; no extra request/interface introduced. Prior02a240a6 source review is superseded; new exact-head reviews required.

## Native raster title boundary (2026-10-17)

- [x] G1 depends_on: [] Preserve independent production visual block and exact finalf4/source provenance.
- [x] G2 depends_on: [G1] Cloud RED for body paragraph attachment, with genuine external title/subtitle/source preservation.
- [ ] G3 depends_on: [G2] Minimal raster-only title guard, exact-head full CI and source/image Sol/Luna review, notification and authorized merge/deploy.
- [ ] G4 depends_on: [G3] Actual deployed native rerender, guarded same-ID correction and all resulting production/source image verification.

Review: Goldman normal6 publishes one supported native table, but external attachment records709chars/5lines of preceding prose as title. Independent Luna blocked visual quality; root/Sol accept superseding old visual approval. Preserve all data/claims/metadata/queue and complete source pixels. No local test suites.

# Revert X share-copy footer (2026-10-17)

Dependency graph: T1 -> T2 -> T3 -> T5 -> T6 -> T4.

- [x] T1 depends_on: [] Identify PR #1143 and isolate the rollback from unrelated operator files.
- [x] T2 depends_on: [T1] Reverse only the footer implementation, expectations and owner-document addition; retain delivery history and record the withdrawn preference.
- [x] T3 depends_on: [T2] Verify exact restoration, focused/full suites and browser sharing without the footer.
- [x] T5 depends_on: [T3] Integrate concurrent Explain PR #1145, resolve only the overlap, refresh verification and wait for the preceding production deployment.
- [x] T6 depends_on: [T5] Integrate concurrent indicator PR #1144, preserve its changes and refresh verification after its production deployment.
- [ ] T4 depends_on: [T3,T5,T6] Publish the revert PR, watch exact-head CI, notify once, admin-merge and verify normal production deployment.

## Review

The user withdrew the requested footer. Restore the pre-PR share copy across P&L, newsfeed and all report builders. Preserve unrelated changes, historical task entries and the separate existing report-generator limitation. No external post is submitted. Verification: all 15 feature-owned implementation/test/document files match the pre-feature revision; focused Vitest 207 passed; affected Python 110 passed; full Python 16,328 passed, 2 skipped and 23 subtests passed; cloud 2,921 passed and 7 skipped; Vitest 1,120 files and 10,996 tests passed, 21 skipped; Playwright five passed with mobile/desktop screenshots inspected. All report-copy and preview builders, verified CTA copy and fallback passed smoke checks without the automatic footer. React review found no new hooks, state, fetching or dependency behavior. Temporary local dependency-root overrides were restored. Concurrent main 3c9b7e67 is integrated; all 34 incoming Explain files match current main exactly and both task-ledger entries are retained. Refreshed Python 16,336 passed, two skipped and 23 subtests passed; cloud 2,921 passed and seven skipped; focused web 207 passed; Playwright five passed. The default full web run had five unchanged Next-config import timeouts; those five files passed 29 tests in isolation. Full Vitest using the supported history-derived build-version override passed 1,124 files and 11,023 tests, 21 skipped. No test/timeout/retry/worker/CI gate changed. The updated-base mobile screenshot clips the incoming share layout; it is retained unchanged and recorded in PR risks. Updated-head CI and production delivery pending.

Final integration review: eac73b0b is included. All 44 incoming indicator files and the Explain feature are preserved exactly; the 15 footer-owned implementation/test/document files still match the pre-footer revision. Current-base full Python 16,367 passed, two skipped and 23 subtests passed; cloud 2,926 passed and seven skipped; full Vitest 1,126 files and 11,060 tests passed, 21 skipped with the supported derived build-version setting; focused web 207 passed and browser five passed. Preceding main CI/deploy is green. Updated-head CI and delivery remain pending.


## Test notification isolation (2026-10-18)

- [x] N1 depends_on: [] Confirm the unmocked hold paging path and preserve unrelated workspace changes.
- [x] N2 depends_on: [N1] Reproduce credential and notification transport leaks in cloud regression tests, without sending any external request.
- [x] N3 depends_on: [N2] Isolate test credentials and live Pushover transports, default-mock hold paging, and document the contract.
- [ ] N4 depends_on: [N3] Open PR, complete all exact-head CI gates and deliver one accepted PR-green notification.

Review: Production Gateway paging, hold behavior and broker state are outside the patch. Tests run in GitHub Actions only; regression probes replace the last transport boundary before exercising the old behavior.

Notification isolation review: cloud RED `1cdf8f37`, run `39151257572`: hold shard 1 failed/1152 passed (one fake sender call); isolation shard 5 failed/2578 passed/1 skipped. Shared root fixtures strip credentials and block urllib/requests Pushover message/cancellation requests with a teardown latch. Default hold pager is fake; explicit paging tests still supply observable senders. Production files remain unchanged. Full final-head CI pending.


## VIX model spread indicator (2026-10-18)

- [x] T1 (depends_on: []): Verify VIX / VIXEQ / COR1M sources, units, windows, licensing and capture fixtures.
- [x] T2 (depends_on: [T1]): Specify formula, API, storage and chart; record failing Python/API/UI tests.
- [x] T3 (depends_on: [T2]): Implement ingestion, storage and trusted scheduled units.
- [x] T4 (depends_on: [T2]): Implement API, types, hook and freshness schedule.
- [x] T5 (depends_on: [T2]): Implement Regime chart, navigation and browser coverage.
- [x] T6 (depends_on: [T3, T4, T5]): Integrate, run full suites, ingest real data and verify browser.
- [ ] T7 (depends_on: [T6]): PR, exact-head green CI, Pushover, admin merge and production verification.

### Review

- Source research reproduced 3,101 aligned Cboe sessions, 2014-06-19 through 2026-10-16.
- Formula verified: sqrt(COR1M / 100) * VIXEQ; latest model 13.235944, spread 6.754056.
- Screenshot bands use 252 sessions (mean 3.949551), while requested prose specifies 20 (mean 4.327849). Preserve explicit window labels.
- User confirmed personal, non-commercial use only on 2026-10-18. Source gate resolved within that scope; no public/demo data or raw-source redistribution.
- Integrated suites: Python 16,155 passed/2 skipped (74.49% coverage), after integrating main 948fedc8; Vitest 11,136 passed/21 skipped; cloud 2,982 passed/7 skipped. Touched collector 61 passed/97.58% coverage after real full-history upload regression. Synthetic desktop/mobile Playwright 6 passed.
- Production Turso migration 98 applied; direct query verifies 3,101 history rows (2014-06-19 through 2026-10-16), complete snapshot at 2026-10-18T22:00:11.019739Z and service health. Native authenticated production-build browser verified 3,101 sessions, 20/252 bands, timer-derived countdown and four inspected desktop/mobile theme screenshots; exact-head delivery remains pending.

- Final updated-base verification: full Python 16,155 passed/2 skipped, cloud 2,982 passed/7 skipped, full Vitest 11,136 passed/21 skipped with every coverage ratchet green. Production compile and native-session real-data browser checks passed; existing two newsfeed type errors reproduce on the clean base and remain outside this feature. React review verified unconditional hooks, ResizeObserver cleanup, finite chart values and explicit window/range state.

VIX model delivery review: initial exact-head CI found missing canonical Clear/TesterArmy route registration (TesterArmy run 39170989963: 1 failed/78 passed). Add the new page to the unchanged strict inventory check; local inventory/generator now cover all 70 App Router pages exactly once. No runtime code or test gate changed. Focused exact inventory plus new-route desktop/mobile Playwright: 3 passed; repaired-head CI pending.

## Operator hardening: research image executables (2026-10-18)
- [x] RCLI-T1 (depends_on: []): Verify image CLI and research auth/mount contracts.
- [x] RCLI-T2 (depends_on: [RCLI-T1]): Pin research executable boundary to image while retaining provider grants.
- [ ] RCLI-T3 (depends_on: [RCLI-T2]): Cloud red/green regression and full CI; document rollout and review.

### Research executable review
- Cloud regression RED verified in private CI job 117778809500; GREEN validation pending.
- Scope review: research executable mount only; auth/HOME/state and Node unit mounts retained.

## Subscription service environment maintenance (2026-10-18)

- [x] SE1 depends_on: [] Verify current service inputs and installation sequence.
- [x] SE2 depends_on: [SE1] Add synthetic environment publication regression and obtain cloud red evidence.
- [x] SE3 depends_on: [SE2] Publish the service allowlist before unit activation and on every deploy.
- [ ] SE4 depends_on: [SE3] Complete cloud gates and prepare the independent maintenance PR.

Review: Production provisioning and credential operations require operator coordination; no live changes are part of this branch.

Subscription environment review: cloud red recorded on private run 39162568477; the dedicated publisher uses pinned directory descriptors and root-private output. Keeper UID, canonical native credential writes, vault socket and paging stay unchanged. Cloud green pending; no production operations performed.

## Responder service account maintenance (2026-10-18)

- [x] RA1 depends_on: [] Map account, native authentication, runtime promotion, state, clone pickup and action grants.
- [x] RA2 depends_on: [RA1] Obtain cloud red for the isolated account and gated migration contracts.
- [x] RA3 depends_on: [RA2] Implement independent account/runtime and complete trusted host handover.
- [ ] RA4 depends_on: [RA3] Complete cloud gates and prepare the independent maintenance PR.

Review: Existing subscription credential lifecycle remains separate; host migration requires explicit native authentication under the new service account.

Responder account review: private cloud red run 39162568477 proved the missing account/domain contracts. Optional source deploy is inert; trusted preparation, native login and explicit pickup acknowledgement are required before timer handover. Keeper runtime remains separate. Synthetic transport and handover tests cover rollback/race paths; cloud green pending. No production state or credential material changed in this session.

## Operator hardening: public media publication (2026-10-18)
- [x] MPUB-T1 (depends_on: []): Verify current producer, edge, backup, and trusted-install contracts.
- [x] MPUB-T2 (depends_on: [MPUB-T1]): Add descriptor-bound publisher regression coverage and cloud RED evidence.
- [x] MPUB-T3 (depends_on: [MPUB-T2]): Implement bounded publication, initial migration, trusted units, cleanup, and rollback.
- [ ] MPUB-T4 (depends_on: [MPUB-T3]): Cloud GREEN/full CI, isolated permission checks, and final review.

### Public media publication review
- Private CI RED verified in job 117778809500; GREEN validation pending.
- Current source metadata inventory supplied by root: 4291 regular single-link radon-owned PNGs; no links or directories.
- Preserves producer/API paths, public URLs and staging backup; initial edge switch requires complete bounded backfill.
- Static AST, shell syntax, artifact-table count and whitespace checks completed; no local suites or production mutations.

### Public media source-lifecycle review repair
- Preserve all supported source leaves, including aged files still served from staging; source cleanup remains the retention owner.
- Reject supported-source overcount before copies/removals; regressions cover initial backfill, cached/uncached refresh, and unchanged destination/cache on overflow.
- Static AST/whitespace verification only; replacement private cloud validation and independent review remain pending.

## Performance snapshot helper (2026-10-18)

- [x] P1 depends_on: [] Verify the installed runner and current snapshot handoff contract.
- [x] P2 depends_on: [P1] Reproduce helper isolation and snapshot validation requirements in cloud tests.
- [x] P3 depends_on: [P2] Implement dedicated scheduled helper installation and consume only its validated snapshot.
- [ ] P4 depends_on: [P3] Complete exact-head cloud checks and document owner-run migration and rollback.

Review: Rootless plist review and static checks are permitted locally; execution tests run in cloud CI. Host account provisioning and credential handover require the existing administrator installation path. Preserve all existing credentials until the administrator verifies handover.

Snapshot helper review: private cloud regression run confirmed the consumer, plist and installer contracts fail before implementation. The final implementation uses helper-only private inputs and a dedicated read group for mode-750/mode-640 exports; collection has fixed configuration, byte/time bounds, strict host-key verification and atomic output. AST, bash syntax and rootless plist lint passed. No local suites, live probe, account creation or credential movement ran. Administrator handover and final exact-head cloud verification remain pending.


### Private CI integration repair (2026-10-18)
- [x] CI1 depends_on: [] Inspect exact-head private CI failures without local suites or production access.
- [x] CI2 depends_on: [CI1] Repair implementation defects and align synthetic fixtures and owner documentation with the intended maintenance contract.
- [x] CI3 depends_on: [CI2] Parse changed sources, check shell syntax and whitespace, and return a committed candidate for private cloud verification.

Review: Existing gates remain mandatory; no production provisioning or credential operation belongs to this repair.

Static repair review: changed Python sources parse, changed shell entrypoints pass syntax validation, and `git diff --check` is clean. No local test suite or live/production test ran. Exact candidate cloud re-verification remains required.


### Independent responder review repair
- [x] RR1 depends_on: [] Verify transient systemd states and the native installer output location against existing source.
- [x] RR2 depends_on: [RR1] Refuse busy/unknown workload state on both sides of timer disable and normalize native CLI layout before reporting readiness.
- [x] RR3 depends_on: [RR2] Add synthetic transient-state, lookup-failure and provisioned-layout coverage for cloud verification.

Independent review repair: handover reads explicit ActiveState and permits only confirmed inactive/failed workloads; transient states and query failures refuse before timer changes or restore the prior timers after disable. Native CLI installation normalizes only an absent canonical slot as the dedicated account and validates the executable before preparation completes. Synthetic coverage authored for both handover directions, second-check races and native installer layouts. AST and whitespace checks pass; cloud verification remains pending.

### Independent media review repair
- [x] MR1 depends_on: [] Verify bounded-read accounting and the canonical public PNG producer contract.
- [x] MR2 depends_on: [MR1] Reject initial oversize without opening payload, reserve attempted source/destination reads and validate PNG framing/dimensions.
- [x] MR3 depends_on: [MR2] Author synthetic budget, read-failure and malformed/oversized PNG coverage for private cloud verification.

Independent media review: initial stat oversize is refused before payload open; each source/destination attempt reserves its stat-size plus growth sentinel before any read, including failures and rejected content. Source reads use the pinned stat-size bound rather than the global cap. Public producer always emits PNG; the publisher now enforces PNG chunk framing/CRC, 16MP IHDR dimensions and terminal IEND without privileged pixel decoding. Synthetic regressions cover invalid magic, read failures, destination comparisons, oversized initial metadata and malformed/dimension-overflow PNGs. AST and whitespace pass; cloud verification pending.

### Private CI follow-up
- [x] PC1 depends_on: [] Read failed scripts-df job 117794290142 without a local test run.
- [x] PC2 depends_on: [PC1] Replace the obsolete shell-target assertion with the canonical Python collector target and host-pin derivation contract.

Follow-up review: the helper issues SSH through a Python argv list from root-installed config, so the old quoted shell `$target` command no longer exists. The documentation contract now pins host extraction, protected config and the exact Python target operand. Python AST and whitespace checks pass; fresh private CI remains required.

## Service isolation maintenance (2026-10-18)

- [x] T0 depends_on: [] Verify current applicability, existing deliveries and safe rollout dependencies.
- [ ] T1 depends_on: [T0] Prepare isolated performance snapshot helper with provisioning and regressions as its own PR.
- [ ] T2 depends_on: [T0] Prepare research runtime CLI configuration as its own PR.
- [ ] T3 depends_on: [T0] Prepare deploy serialization migration and compatibility as its own PR.
- [ ] T4 depends_on: [T0] Prepare token keeper environment contract as its own PR.
- [ ] T5 depends_on: [T0,T4] Prepare page responder identity provisioning as its own PR.
- [ ] T6 depends_on: [T0,T2] Prepare media publication isolation and asset migration as its own PR.
- [x] T7 depends_on: [T0] Verify existing host-key deployment controls.
- [ ] T8 depends_on: [T1,T2,T3,T4,T5,T6,T7] Complete immutable-head CI and independent reviews for each relevant PR.
- [ ] T9 depends_on: [T8] Verify authorized release order and provisioning receipts or identify exact remaining bootstrap.

Review: implementation is scoped to independently verified current behavior; preserve unrelated workspace files, production availability, credential boundaries and the existing workflow review policy. Test suites run in cloud only.

Service isolation source review: cloud-only regression RED recorded before implementation. Research CLI, keeper environment, performance helper, responder and media source candidates have independent Sol/Luna reviews; the lock migration has Sol source review with final Luna confirmation pending. Review repaired transient oneshot detection, native CLI normalization, first-upgrade recovery and bounded rejected-media reads before delivery. No production provisioning or native authentication has been performed. Full final-head CI and separate public PR delivery remain pending.

Deploy serialization private validation `981bbe7c`: scripts-ac 4 failed/3119 passed/1 skipped; scripts-df 1 failed/1606 passed/3 skipped; cloud-al 24 failed/2925 passed/2 skipped. Repaired isolated interpreter dependencies, observer transport fixtures, owner documentation and canonical installed-mode comparison. Preserve wait budgets and add real temporary-file observer coverage for both generations, first bootstrap, FIFO and symlink refusal. Replacement immutable-head full CI remains required.


## Subscription publisher prestage ordering (2026-10-18)

- [x] PS1 depends_on: [] Confirm first-upgrade behavior and normal recovery/sync/deploy ordering against the reviewed final stack.
- [x] PS2 depends_on: [PS1] Author isolated main-path regressions for old grants/helpers, prestage and required normal publication.
- [x] PS3 depends_on: [PS2] Record cloud-only RED before applying the minimal runtime guard.
- [x] PS4 depends_on: [PS3] Apply the scoped fix while preserving recovery journals and normal deployment ordering.
- [ ] PS5 depends_on: [PS4] Obtain cloud GREEN and independent review before delivery.

Review: test-only preparation was committed separately; the runtime guard was applied only after cloud RED. Local suites, source publication and production actions remain excluded.

Prestage ordering review: cloud RED recorded 7 failed/25 passed. All seven failures exercised premature stage publication; all six strict normal-deploy cases passed. Applied the stage-only guard after RED and clarified the environment owner. Recovery, pinned synchronization and rollback journals are unchanged. Static checks only locally; focused cloud GREEN and independent review remain pending.


### Prestage lock bootstrap readiness

- [x] PL1 depends_on: [] Confirm entrypoint lock admission precedes prestage and trusted bootstrap provisions the new canonical inode.
- [x] PL2 depends_on: [PL1] Author cloud-only entrypoint regressions for canonical first bootstrap, strict normal admission and unsafe/unknown lock state.
- [x] PL3 depends_on: [PL2] Record intended cloud RED before changing runtime.
- [x] PL4 depends_on: [PL3] Apply only the reviewed stage deferral.
- [x] PL5 depends_on: [PL4] Obtain expanded cloud GREEN and independent final review before delivery.

Review: applied the reviewed guard after the intended cloud RED was confirmed. Preserve normal required locks, recovery-only compatibility, journals and pinned synchronization; expanded cloud GREEN and independent final review are complete; public CI remains pending.

Independent readiness review: require valid directory authority before classifying a missing canonical leaf. Add writable-parent, writable-authority and foreign-authority cases using real temporary metadata; the script-copy fixture explicitly maps the production filesystem root/UID-0 domain to its temporary root/current UID, without changing production policy or mocking supervisor/flock/stat. Runtime guard applied after cloud RED; expanded cloud GREEN and independent final review are complete; public CI remains pending.

Canonical parent contract review: temporary script copies explicitly map the fixed radon group lookup to the fixture's actual gid; a mismatched expected gid and a nonwritable but incorrect0700 parent mode must refuse missing-leaf deferral for default and explicit-canonical selectors. Production's exact0750/radon-gid contract is enforced before missing-leaf deferral.

Expanded readiness cloud RED recorded 5 failed/96 passed/0 skipped: four canonical first-bootstrap stage cases and the symlink-parent case failed; all subscription environment cases passed. Applied the reviewed root-authority stage guard after that evidence. No local suites or production actions; expanded cloud GREEN and independent final source review are complete; public CI remains pending.

Final readiness checkpoint: expanded focused cloud validation passed 101/101 with 0 failures and 0 skips. Sol and Luna approved the same runtime/test/documentation source. This checkpoint changes task status only; public full CI remains pending before delivery. No local suites or production actions ran.


## Media publication watcher recovery (2026-10-18)

- [x] MP1 depends_on: [] Preserve actual failed-unit metadata and bounded journal evidence; establish the trigger/start failure sequence.
- [x] MP2 depends_on: [MP1] Reproduce the observed scheduling failure in a real isolated cloud systemd fixture before runtime changes.
- [x] MP3 depends_on: [MP2] Apply the smallest bounded scheduling/recovery repair and obtain cloud GREEN plus independent Sol/Luna review.
- [ ] MP4 depends_on: [MP3] Complete exact-head public CI, one accepted green notification, authorized merge/deploy and independent live publication checks.

Review: no local test suites; retain all PNG validation, authority, read-budget and timeout controls. No IB Gateway or unrelated service changes. Current deployed1bd6 CI green; path failed with unit-start-limit-hit while retry timer is active. Full final live verification remains incomplete.

Media scheduling regression: corrected cloud RED `39191147518` at test source `df0eb32f` recorded 8 failed/42 passed/0 skipped, including ten real successful systemd starts, service `start-limit-hit`, path `unit-start-limit-hit`, and the old helper leaving the path failed. Initial fixture-only failures are excluded. Runtime changes applied only after this valid RED; all PNG and resource-bound source remains unchanged.

Media scheduling final focused review: after integrating current green main `61b6f7e`, corrected identical test blobs produced cloud RED `39191711427` (8 failed/42 passed/0 skipped) and GREEN `39191809980` (50 passed/0 failed/0 skipped). The real systemd fixture recovered the latched legacy path, kept it disabled/inactive, ignored a 100-write burst and completed three later timer publications. Runtime/test blobs remain exactly `2651738c`; the checkpoint clarifies boot and inactivity timer deadlines and records independent Sol/Luna source review. Publisher/PNG logic and all service/path/timer resource bounds remain unchanged. Public exact-head CI, merge/deploy and live verification remain MP4.

## Performance probe handover dependencies (2026-10-18)

- [x] PH1 depends_on: [] Preserve actual partial grant state and identify launchd stdout EACCES plus schema-1 host metrics-object rejection against the pinned app host.
- [x] PH2 depends_on: [PH1] Cloud RED using real root/unprivileged log access and canonical producer payload shape before changing runtime.
- [x] PH3 depends_on: [PH2] Minimal protected log provisioning and schema compatibility repair, adversarial coverage, cloud GREEN and exact-head full CI.
- [ ] PH4 depends_on: [PH3] PR notification, authorized admin merge/deploy, guarded existing-grant continuation and actual production collection/permission/old-key rejection checks.

Review: no local suites. Preserve all source/failed root and coordinator receipts, both current restricted grants, and all private keys in their existing accounts. The actual probe target is the app host verified by its pinned Ed25519 fingerprint, not the Gateway broker. The third authenticated attempt stopped at launchd spawn EX_CONFIG before Python ran. No old key was revoked.

Handover dependency checkpoint: corrected cloud RED 39201217707 (8 failed, 33 passed) and identical-test GREEN 39201382169 (41 passed). Full private CI found the missing runner owner-document update. Pinned, authentication-free app tailnet SSH reached public-key rejection; public port 22 timed out. Add a default-target regression and migrate only the reviewed legacy public target, preserving the existing private key and host identity.

Default transport RED 39202014836: 1 failed and 41 passed against the old checked-in public target. Apply the documented app tailnet default only after that failure; preserve the test blob for GREEN.

Final performance dependency review: focused cloud GREEN 39202104661 passed 42/42 with the transport RED test blob unchanged; private full canonical CI 39202108512 plus images 39202108499 is complete, 41 checks successful or applicable skips on 86516d4e. Runner owner documentation repaired the initial full-gate failure. The Mac continuation preserves the exact retained grant and failed attention context, installs only the tested collector, provisions its protected log and migrates the fixed target/pin while holding the maintenance gate. It remains unpublished until public CI and deployment succeed; the authenticated native run remains unverified. No local suites ran.


# Nightly performance audit (2026-10-19, resumed delivery)

Dependency graph: P1 -> P2 -> P3 -> P4.

- [x] P1 depends_on: [] Resume tonight's branch and draft #1192; install dependencies; read trusted checkpoint and production snapshot.
- [x] P2 depends_on: [P1] Audit changed surfaces and network rotation locally; recheck PERF-019/020 red/green budgets.
- [x] P3 depends_on: [P2] Review transport safety and complete focused verification and build evidence.
- [x] P4 depends_on: [P3] Validate draft body and delivered code-head CI; prepare the single rolling checkpoint for final exact-head verification.

## Review

Resumed existing pushed commits d848fceb and 6c5b4684; no production access and no new IDs allocated. Audited 48 commits / 163 changed paths through origin/main 5bfadbb6. Network rotation: baseline serial reruns fail unchanged socket/concurrency budgets (5 sockets versus 1; in-flight 1 versus 2); current focused Python 339 passed, web 56 passed, cloud 91 passed. Local transport fault checks preserve 429 Retry-After, size refusal/socket retirement, five-second idle retirement, one stale-read retry and no token-refresh replay. Production build and output trace audit pass (230 manifests, no retained data files); Next 16 compile mode omits per-route First Load JS. Largest client chunks: 1,227,120 and 930,905 bytes; ExcelJS/PPTX imports already defer until export. Local profiles: exports parse 309 Taiwan/321 Korea rows into 321 composite rows; writer uses one 3210-parameter INSERT; snapshot query uses service index with no temp sort. Humanizer expands 4000 duplicate descriptors into one span. SQLite applies 96 migrations; two vector-index migrations require the unavailable libsql_vector_idx extension, so vector plans are outside this SQLite audit. Existing code head has complete green CI; final delivery/checkpoint follows this audit-only commit. No new actionable bottleneck verified beyond PERF-019/020. Runner owns notifications.


## 2026-10-21 Execute UW Advanced roadmap
Authoritative roadmap: /Users/joemccann/.codex/visualizations/2026/10/14/uw-advanced/roadmap.md.
- [ ] T1 (U1): Rebase scope against current main, verify account headers/channel access, write execution contracts. depends_on: []
- [x] T2 (U2): Shared REST governor, provider header reconciliation, direct-fetch integration, capability metadata. depends_on: [T1]
- [x] T3 (U3/U4): Single-owner managed collector, bounded durable buffering, channel semantics, compact materialization and health. depends_on: [T1]
- [x] T4 (U7/U8/U9): Advanced research adapters and persistent snapshots for multi-leg/Pulse/issuer/macro/futures; capability gated. depends_on: [T1]
- [x] T5 (U5/U6): Authenticated research API/MCP/UI, live overlays, source/gap/cadence provenance and selective candidate integration. depends_on: [T2,T3,T4]
- [ ] T6 (U10/U11): Independent reviews, GitHub exact-head CI, measured staged activation and tape capacity decision. depends_on: [T5]
Dependency graph: T1 -> {T2,T3,T4} -> T5 -> T6.
Validation: no local test suites; tests and E2E on GitHub runners. Do not modify IB execution/quote authority or public/demo redistribution. Full tape remains measurement-gated, Kafka not authorized by Advanced. Preserve honest gaps and daily OI.


### UW execution review checkpoint

Implemented T2–T5 on `codex/uw-advanced-execution`, PR #1214. REST probes returned 200 across six dataset families; missing quota headers and unreachable VPS SSH leave T1 entitlement verification and T6 production promotion incomplete. Streams remain disabled by default. Independent Sol/Luna findings are repaired: daily rollover probes, credential-scoped caches, killable bounded web reads, frontend credential ownership, observation provenance and retained-event replay. GitHub RED exposed outdated route policy/manifests/fixtures; repair CI and desktop/mobile screenshot verification remain pending. No local suites executed. Full-tape capture, live ranking/cache invalidation and source-parity promotion are deferred until pilot measurements, not claimed delivered.


UW follow-up: Sol/Luna re-reviewed `e92c723d2` and closed their final quota/replay findings. VPS read access was restored via existing Tailscale transport; new units remain absent/inactive and account receipts remain unverified. Repair CI reduced failures to one new panel-test isolation failure (1 failed / 1703 passed in that shard); add explicit cleanup because Vitest globals do not register the testing-library auto-cleanup hook. Final exact-head CI and browser artifacts remain required.

## Grok duplicate fallback incident (2026-10-21)

- [x] GF1 depends_on: [] Verify first-session completion, live aliases and LKG from production records.
- [x] GF2 depends_on: [GF1] Reproduce seven behavioral failures plus five new identity cases in cloud tests.
- [x] GF3 depends_on: [GF2] Reject alias retries, recognize successful assistant results, retain redacted failure diagnostics.
- [ ] GF4 depends_on: [GF3] Complete broader cloud checks and exact-head PR CI.
- [ ] GF5 depends_on: [GF4] Verify live responder and record rollout requirements.

Review pending. First CLI completed18:18:12Z and committed5a5bf2b9 before parent repeated the same CLI via its resolved LKG path at18:18:13Z. Parent did not retain the original triggering stdout/stderr. Cloud RED12failures (7 behavioral,5 missing-helper assertions); GREEN87focusedchecks; final broader cloud run323Pythonchecks and84cloudcontracts passed. No local tests or credential changes. Original responder finished18:22:35Z; incident commit separately surfaced as PR1220. Preserve that work. The clone was also held dirty by root/web node_modules because a missing newline commented out its ignore rule; added one more cloud RED/GREEN regression and restored that rule.

Integration review: merged current main0ba43e597, preserving atomic daily-budget reservation and no-fallback-on-budget-refusal. Isolated cloud tests333passed plus84cloudcontracts. Four initial remote harness failures were its real host deploy-transition journal leaking into rerun tests; a per-test temporary transition/lock/LKG plugin isolated those external files without altering production behavior or test assertions. Original PR-head full test CI passed; integrated head must rerun after the current main deploy finishes.

## LEAP admission-window incident 2026-10-22
- [x] L1 depends_on: [] Confirm journal, cache, provider canary and shared governor state.
- [x] L2 depends_on: [L1] Cloud failing regressions for transient embargo/minute windows and scanner client policy.
- [x] L3 depends_on: [L2] Allow explicitly bounded scanner admission waits; preserve fast interactive defaults and hard quota refusals.
- [ ] L4 depends_on: [L3] Cloud focused tests, PR full CI, deploy and successful production LEAP refresh.
- [ ] L5 depends_on: [L4] Verify cache coverage, service health, watchdog and review.

L2 evidence: isolated VPS red run: 7 failed, 2 passed (real SQLite governor, virtual clock and synthetic HTTP).

L3 verification: isolated cloud focused suite 250 passed; final admission regressions 10 passed, including full scan retention across embargo and rolling windows. Local checks are static only.

L3 cache-path review: a separate cloud red test proved singleflight still truncated the opt-in wait to ten seconds. Its explicit ceiling now matches admission; normal five-second and absolute 120-second bounds are regression-tested. Expanded cloud compatibility: 277 passed; final admission/cache regressions: 13 passed.

L3 broader importers: cloud health wiring exposed one legacy zero-argument client stub; updated it for the explicit policy. Broader service health, honest exhaustion and Robinhood-priority suites: 64 passed. Final main compatibility suite: 279 passed.

### LEAP review before release
- Preserved shared reservations, 80% background allowance, quotas, cache identity, exhaustion status and duplicate-scan refusal. Only LEAP opts into longer waits; cache lock and governor each retain a 120-second ceiling. Original per-ticker exception details are unknown; runbook states that evidence limit. Full exact-head CI and live scan remain release conditions.

## News article reader and gallery redesign (2026-10-27)
- [x] T1 (depends_on: []): inspect current reader, capture regression, define responsive interaction contract.
- [x] T2 (depends_on: [T1]): failing component/browser coverage for centered bounds and independent image navigation.
- [x] T3 (depends_on: [T2]): implement bounded reader and accessible image gallery with zoom, source and article controls.
- [x] T4 (depends_on: [T3]): focused/cloud suites, desktop/mobile light/dark screenshots, review.
- [ ] T5 (depends_on: [T4]): PR with exact-head CI green and required notification.

### Article reader review
- Cloud Vitest: 11,542 passed / 21 skipped before the final accessible-name correction; final focused run: 104 passed, 98.88% reader/gallery line coverage. Component lint passed.
- Browser: all eight final gallery width/theme cases passed in isolated batches, including 600px desktop height, touch, focus wrapping, Escape, zoom and reset-on-next-article. Existing source context and sharing/export checks passed. Both PDF-tab compatibility cases passed after awaiting URL-owned tag filter transitions.
- Private 2x before/after captures inspected for the exact JP Morgan article at 393px and 1440px, dark/light. Original images and article copy preserved; no new dependencies or production changes. Exact-head PR CI and notification remain T5.
- Standalone typecheck remains red on four pre-existing diagnostics in unchanged `DashboardNewsFeed.tsx` and `lib/sameOrigin.ts`; neither reader component has a diagnostic.

- User preference: removed the toolbar label and its unused CSS. Focused cloud: 35 passed; all eight responsive gallery cases passed after removal.
- CI compatibility: TesterArmy image-name assertion now includes the gallery ordinal. Previously failed in run 40518150058; focused isolated browser journey passed (1/1), including bookmark request and Escape dismissal.

## Repeat last-100 PR operator audit repairs (2026-10-27)

- [x] T1 depends_on: [] Freeze PRs #1205-#1304; checkpoint 100 bodies and discussions, no retrieval failures.
- [x] T2 depends_on: [T1] Reproduce demo dependency build failure; confirm vendor wheel is generated, intentionally untracked.
- [x] T3 depends_on: [T2] Install the verified materializer step in the demo VM build recipe and verify automatic deployment (30d8fdc6a472, all revision/isolation/auth gates green).
- [ ] T4 depends_on: [T1] Complete safe operator actions and publish all outstanding items with receipts.

Review: source archive changes rejected: the wheel is deliberately generated from a hash-verified upstream artifact. The VM recipe needs the same prerequisite as production.

## Event-odds ownership correction from operator audit (2026-10-27)

- [x] E1 depends_on: [] Add failing contracts for on-demand event-odds ownership and watchdog exclusion.
- [x] E2 depends_on: [E1] Align Python/TypeScript catalogs with the existing GET-triggered producer; preserve freshness windows and manual refresh.
- [ ] E3 depends_on: [E2] Focused/full verification, green CI, notification, merge and production verification.

Review: Python catalog 34 passed; focused web perimeter/auth/health 176 passed. Full Python 18,338 passed / 12 skipped; full web 11,549 passed / 21 skipped; cloud 3,505 passed / 24 skipped. Deployment verification remains pending. No event-odds timer exists on either online Mac or production.

## Mac mTLS fixture compatibility (operator audit #1263)

- [x] C1 depends_on: [] Reproduce strict OpenSSL certificate rejection on unchanged fixture: Missing Authority Key Identifier; 84 affected mTLS tests.
- [x] C2 depends_on: [C1] Add explicit CA/leaf key identifiers to generated test PKI, retaining strict verification and negative identity tests.
- [x] C3 depends_on: [C2] Focused mTLS 129 passed; full Python 18,338 passed / 12 skipped.

Review: strict verification and all negative protocol tests remain enabled. Replaced only local dependency symlinks with ordinary APFS-cloned directories. Full web used the actual computed RADON_RELEASE_VERSION=0.47.7 build input; unchanged history derivation exceeded five-second config-import budgets otherwise. No test timeouts or assertions were relaxed.

## Public preview policy chosen by operator (2026-10-27)

- [x] P1 depends_on: [] Operator chose public previews with request limits; preserve authenticated generators.
- [x] P2 depends_on: [P1] RED: shared 61st request was 200; add central GET/HEAD budget before rendering.
- [ ] P3 depends_on: [P2] Verify spoofed hops, route switching, reset, normal unfurls and deployment.

Review: unit tests cover GET/HEAD, route switching, spoofed left hops, reset and independent clients. Real Next HTTP integration: 1 passed, 61st request 429 before handler; other peer 400 for missing card parameters. Existing bounded per-instance limiter only; no distributed-limit claim or new billing credential.

## Release-controller policy reconciliation (2026-10-27)

- [x] R1 depends_on: [] Verify controller requires strict checks while live protection had strict=false.
- [x] R2 depends_on: [R1] Restore strict=true through the narrow status-checks API; verify all 28 names/app IDs, admin enforcement and every other protection field are unchanged.
- [x] R3 depends_on: [R2] Verify the existing serial release controller resumes (#1298 merged); align root guidance with its strict-checks contract. Subsequent main CI green.

## Claude bundled Bun follow-up (#1239)

- [x] B1 depends_on: [] Production native binary fails the 1.4.4 floor: embedded Bun v1.4.3 despite the host upgrade.
- [x] B2 depends_on: [B1] Verify the replacement native package's registry SHA-512 and embedded Bun 1.4.4; update exact 2.1.311 pin/lock and real image smoke.
- [ ] B3 depends_on: [B2] Green image CI, deploy, verify binary floor and keeper health.

Audit release review: CI scripts-df rejected the new HTTP-only spec because it was unclassified. Added its dated local verification and explicit hold-out reason to the existing curation ledger; retained required unit coverage and all workflow gates.

## Private candidate freshness feedback from operator audit (2026-10-27)

- [x] S1 depends_on: [] RED: unpublished stale candidate returned waiting with no private feedback.
- [x] S2 depends_on: [S1] Return existing invalid-candidate feedback without fetching source, creating validation workflows or publishing; preserve prior markers. Focused 293 passed; full Python 18,461 passed / 12 skipped; full cloud 3,511 passed / 24 skipped.
- [ ] S3 depends_on: [S2] Focused/full tests, exact-head CI, notification, merge and installed controller feedback receipt.

Review: Sol found no actionable regression; Luna independently passed 292 focused tests. Added their suggested explicit API allowlist/no-workflow-push and published-marker preservation assertions; 293 focused now pass. The regression asserts no model review or release receipt, schema-valid diagnostics and stable repeat ticks. Fresh-base source review, exact-head CI and publication gates remain required. Deployment/installed feedback verification pending.

## TheMarketEar unattended renewal (2026-10-27)
- [x] TME1: Verify same-account Firebase refresh and provider token endpoint; depends_on: []
- [x] TME2: Red/green renewal and grant persistence tests; depends_on: [TME1]
- [x] TME3: Wire proactive renewal and narrow runtime seed allowlist; depends_on: [TME2]
- [x] TME4: PR #1312 merged f4bd3a8d9; run 40553429761 green. Stored-grant-only fresh browser verified 29 premium bodies and renewal through 2026-11-10; depends_on: [TME3]

### Review
- Renewal/auth/browser coverage: 70 passed. Full Vitest: 11,576 passed, 21 skipped.
- Renewal module coverage: 100% statements/lines/functions, 97.95% branches.
- Full cloud: 3,511 passed, 24 skipped. Full Python: 18,460 passed, 12 skipped; two local setup/docs failures repaired and their focused regressions pass.
- Four pre-existing TypeScript errors are in files identical to origin/main; no new errors.
- Strict renewal proof rejects absent/throwing DOM probes: 2 native Node regressions failed first, then passed; full Vitest confirms both.
- Latest-head CI and installed fresh-browser renewal remain pending in the PR.

## MarketDesk article-first publication and independent PDF archive (2026-10-27)

Dependency graph: M1 -> [M2, C1, D1] -> U1 -> M4 -> M5.

- [x] M1 depends_on: [] Verify preview-denied/download-enabled account state; no independent working download route verified. User prioritizes source summaries and charts over PDFs.
- [x] M2 depends_on: [M1] Persist discovery before slow work; replay pending rows outside latest-25/lookback; publish article provenance without downloading PDF.
- [x] C1 depends_on: [M1] Parallel voice-copy lane: input-only bullets, connected prose, grounded $tickers, final Humanizer score/certificate after normalization. Slash-separated ticker red/green covered; private per-run editorial audits retain every scored draft and park on storage outage.
- [x] D1 depends_on: [M1] Parallel durable PDF archive with isolated credentials, global throttling, atomic backoff, attachment CAS and same-post provenance healing; three kill/recovery boundaries.
- [x] U1 depends_on: [M2, C1, D1] Article-only provenance, no raw source bullet lists for legacy/new posts, accurate article/PDF links; 48 component tests and two desktop/mobile browser cases green and screenshots inspected.
- [x] M4 depends_on: [U1] Full suites, PR #1314 exact-head 45 green checks, accepted notification, admin merge 6fda0d80b and production deploy run 40560029613 succeeded.
- [x] M5 depends_on: [M4] Managed replay recovered 34 articles: 92 published total, zero pending, 18 relevance holds, five drops and two missing-summary failures. All six named reports are published and visible in the live feed; 80 source PNGs and six matching final editorial audits verified. The broader-policy replay is tracked in MS5.

Review: source facts and qualifications remain intact; full web 11,587 passed, full default Python collection 18,583 passed, 12 skipped after owner-document repairs. PDF store/job 36 passed after independent P2 repairs; full cloud 3,525 passed, 24 skipped. First two live articles scored 100/100, matched saved audits and source facts; 46 private PNGs verified. Existing relevance/fidelity/Humanizer gates remain; request-limit refusals never bypassed. No working standalone download endpoint claimed.


## MarketDesk publication follow-up verification (2026-10-27)

Dependency graph: F1 -> [F2, F3] -> F4 -> F5.

- [x] F1 depends_on: [] Verify real interrupted transport and independent Luna ticker finding against the source; preserve provider limits and source facts.
- [x] F2 depends_on: [F1] Actual SIGTERM regressions fail before cancellation repair; retain 143, lease, cleanup and service-health evidence. Agent focused 251 passed.
- [x] F3 depends_on: [F1] Ambiguous stock/acronym, rewrite and URI regressions; retain ordinary AI spending, indices and country/currency labels. Root integrated focused 366 passed.
- [x] F4 depends_on: [F2, F3] Sol reviewed the repaired index case with no remaining findings; full Python 18,622 passed / 12 skipped, cloud 3,525 passed / 24 skipped, web 11,595 passed / 21 skipped. Suites ran sequentially; independent Luna verification passed 565 focused tests with no remaining review findings.
- [x] F5 depends_on: [F4] PR #1316 passed 43 current checks, one accepted notification, admin merge 0e3572cc1 and successful production deployment at 19:32 UTC. Installed job/copy/cycle hashes match the merge; named live feed and private source/audit receipts verified.

Review: article-first PR #1314, renewal PR #1312 and follow-up #1316 are deployed. All six originally missing reports are live with source-grounded prose and preserved images. PDF archival remains provider-blocked with persisted backoff; it does not block publication. The newly chosen all-suitable policy and its replay are tracked in MS1–MS5.


## Combo order submission repair (2026-10-28)

Dependency graph: T1 -> {T2,T3,T4} -> T7 -> T5 -> T6.

- [x] T1 depends_on: [] Inspect repository instructions, current branch, code map and reported PendingSubmit failure; isolate current main.
- [x] T2 depends_on: [T1] Trace Python combo construction, transport and acknowledgement; identify and repair verified defects with regression coverage.
- [x] T3 depends_on: [T1] Independently trace web/API combo payload and broker lifecycle boundaries; report evidence and targeted corrections.
- [x] T4 depends_on: [T1] Independently inspect broker protocol, history and existing tests; challenge diagnosis and define missing coverage.
- [x] T7 depends_on: [T2,T3,T4] Preserve non-blocking order-warning evidence for unresolved submissions without changing acceptance policy.
- [ ] T5 depends_on: [T2,T3,T4,T7] Reconcile findings, review changes and obtain GitHub-runner red/green verification.
- [ ] T6 depends_on: [T5] Publish template-compliant PR, wait for all applicable exact-head CI checks and send one accepted green notification.

Constraints: no local test suites; no live order placement, cancellations, risk-gate weakening or automatic leg splitting. Preserve unrelated branch/work. Root cause remains unknown until source evidence.

Review checkpoint: three-agent trace found and repaired missed orderStatus.permId acknowledgement, unknown-transmit classification, API audit/response propagation, and idempotency retention. GitHub RED a8d428f50 / run 40709528967: 12 broker + 6 API + 2 web regressions failed, with 7039 other tests passing in those shards. Independent review added four recovery-path regressions; The original 20 regressions passed at dd15072e9. The first four recovery failures at that head were fixture-only and excluded. Corrected recovery RED 5c00ca26a / run 40712276100: four expected identity/status failures and 2308 passing; the same tests are retained for final GREEN. Local work used static parsing, TypeScript, ESLint and diff checks only. The hook required installing locked dependencies and correcting two pre-existing type declarations (optional newsfeed content and environment index signature); runtime behavior is unchanged for those declarations.

Production evidence: retained API logs show CBRS BUY50 timed out PendingSubmit without permId on 2026-10-28 14:36:49Z; fresh broker sync 14:49:09Z contained no matching open order or fill. Gateway 10.50.1f, IBC 3.24.2, ib_insync 0.9.86 with advertised protocol 157–176. Six explicit non-executing margin previews yielded five combo timeouts and one single-option result; the final preview had no errorEvent or decoder exception. IB documents SMART-combo what-if limitation 360, so these previews do not establish execution-router failure or validate a routing fix. No live orders/cancellations, Gateway settings, routing flags or risk gates changed. The current combo outage's broker cause and actual submission restoration remain unverified; the operator confirmed only the live session is available, so paper-session reproduction is blocked. Do not describe this PR as proven restoration of live combo execution.

Final source review: broker/API/web independent review completed; recovery retains only explicit indeterminate placement data and leaves restart/cooldown/read-script behavior unchanged. Final exact-head CI and notification remain pending.

New operator evidence: CBRS SELL close also timed out at 2026-10-28 15:09:54Z, order 417, total 23.3578s. Production container source matches origin/main. Code review proves warning 399 is emitted but omitted from trade.log and discarded by the placer; child stderr is not retained after normal process exit. Whether any warning occurred on 417 is unknown. Add order-scoped warning retention before final publication; do not make benign warnings blocking. d74987047 passed all 37 reported checks before this extension.

Warning diagnostics RED: cb8e9271f / run 40715646220 produced five expected missing-warning failures and 3016 passing tests; two unrelated/farm-warning controls passed. Runtime now preserves nonblocking, order-scoped 399/2109 diagnostics across all five post-transmit result paths, with existing credential scrubbing, deduplication and bounded retention. Independent review found no blocker. Original warning-test blobs unchanged for final GREEN. Native IBKR combo acceptance is untested; the operator has tried only Radon.


## Share regression and voice retirement (2026-10-29)

- [x] T1 depends_on: [] Reproduce pending-media composition and mobile action layout regressions with tests.
- [x] T2 depends_on: [T1] Make original-copy composition immediate and retire share voice generation.
- [x] T3 depends_on: [T1] Keep feedback, Explain and Share horizontal on mobile.
- [x] T4 depends_on: [T2, T3] Run focused/full applicable suites, browser verification and independent review.

- [ ] T5 depends_on: [T4] Publish a focused PR from current main; wait for every latest-head check, accepted PR-green notification, then operator-authorized admin merge and production verification.

Review:
- Three implementation agents and independent client/backend/mobile/E2E review; no remaining blockers. Red/green captured for immediate caption, protected retirement and shared action grouping. Browser found a real 320px wrap; final measured controls remain 44px high and share one baseline with full-width panels below.
- Full web: 11,073 passed, 10 skipped / 1,112 files. Command: `RADON_RELEASE_VERSION=0.13.9 npm test` from web. Unpinned baseline had five unchanged next.config import timeouts: synchronous release-history import measured 9,228ms against a 5s timeout; the existing image-build-supported version override reduces import to 10ms, isolated config 29/29 passed. No timeout or gate changed.
- Browser: 33/33 passed using Chromium at 320/393/640/1440 and iPhone Safari; pending/failing media, actual X popup/clipboard, full-copy reopen, genuine PNG/MP4 exports, contained feed/lightbox panels and simultaneous feedback+Share verified. Final dark mobile and pending-preview screenshots inspected under web/test-results.
- Focused share component/retirement: 26 passed, 98.48% line coverage. Focused docs contract: 2 passed. Broader docs: 185 passed, 6 baseline failures (existing external .claude/worktrees scan, TEST_LOG/TEST_AUDIT ledgers behind origin/main, missing yaml/requests). No Python and Vitest suites ran concurrently.
- Typecheck: four existing errors (unchanged optional content passed to Explain; three unchanged sameOrigin ProcessEnv incompatibilities). No errors added by this diff. git diff --check clean.
- Share uses sanitized existing article text; retired model prompts/cache/progress modules and tests removed, protected endpoint returns 410/no-store. PDF/MarketDesk editorial pipelines remain outside this share-specific retirement.
- Humanizer final editorial check for new synthetic caption fixture: "Yen hedge demand increased. Positioning remained near neutral." scored 100/100, no visible-rubric or supplemental qualitative findings. Existing source/regression text is preserved as fixture data, not newly drafted publishable copy.
- Release preparation preserves unrelated dirty task files/data/jobs/output in the original checkout.
- Fresh-main release validation on e3b778814: independent staged implementation review approved SHA256 32d3604cf8203fd0144fe82708364aac706e9784dfc51a5c0bbc8b9693a90882; Python 19,366 passed/12 skipped, cloud 3,533 passed/24 skipped, browser 33 passed, docs 194 passed and typecheck passed. Dependency directories are physical APFS clones for isolated Next resolution; an initial symlink-only environment failure was repaired and the full Python suite rerun green. Final web: 11,091 passed/10 skipped across 1,114 files with the supported release-version override; post-browser typecheck passed. All local required gates are green before commit.
- PR #1347 initial head 8bfc09131: scripts-df rejected the stale held-out mobile-spec ledger stamp. Reproduced locally (1 failed/5 passed); added the dated receipt for the existing 12-case iPhone Safari run without changing CI membership or any test gate.
- Curation repair verification: 200/200 curation and documentation tests passed; runtime implementation and its independent review digest remain unchanged.
- Browser CI repair: named feedback regions replace screenshots of flattened wrappers; actual Saved-status 320px wrap reproduced at 48px and repaired by giving confirmation its own row. Independent review approved; full web 11,091 passed/10 skipped and combined browser 36 passed, including unchanged actual voting payload/down-hide/up-stay assertions. Runtime retirement and original-copy flow remain unchanged.


## Mobile native platform pass (2026-10-30)

- [x] MN1 depends_on: [] Audit viewport, touch capabilities, inputs, scrolling and safe areas against mobile-native and scoped web rules.
- [x] MN2 depends_on: [MN1] Add failing regressions for verified defects, then apply minimal platform fixes.
- [x] MN3 depends_on: [MN2] Run focused Vitest, mobile/desktop Playwright and screenshot verification; document real-device limitations.

- [x] MN4 depends_on: [MN3] Isolate only mobile-native changes on current main and verify the resulting branch.
- [x] MN5 depends_on: [MN4] Commit, push and create the requested PR with the required body.
- [x] MN6 depends_on: [MN5] Confirm exact-head checks are green and deliver the required Pushover notification.

Review: Applied mobile-native to shared web platform styles and all 20 authored CSS files containing ungated hover. Red: both authored/Tailwind hover contracts failed; WebKit reproduced missing keyboard viewport metadata and the 1024px touch-tablet search input at 14px. Green: 192 original ungated hover rules covered via 189 wrappers and three strengthened media gates; mixed keyboard-focus/selected selectors preserved. Shared control press feedback, control-only selection suppression, capability-based 16px input floor, landscape text sizing, vertical overscroll policy, viewport-bounded dvh surfaces, pinch zoom in modal scrollers and Next interactiveWidget metadata applied. Full web 11,076 passed / 10 skipped across 1,113 files. WebKit 27 passed; Chromium 3 passed. Phone/tablet/desktop screenshots inspected. Existing selector/declaration audit: 5822 compared, zero unexpected changes. Typecheck reports four errors in files untouched by this pass (DashboardNewsFeed.tsx, sameOrigin.ts); no new errors in changed files. git diff --check clean. No supported phone/tablet appeared in USB inventory: real keyboard, browser chrome, safe areas/rotation and installed-PWA feel remain unverified, as required by the skill. No application dependencies or trading logic changed; PR creation is now authorized. Evidence: output/mobile-native-iztzjg24/verification.json.

PR preparation review: Current origin/main base 3cb47c7c5; 25 owned files isolated with a three-way merge against the pre-pass CSS snapshot, preserving unrelated original-checkout edits. Fresh worktree full web: 11,099 passed / 10 skipped across 1,116 files; typecheck passed. Fresh branch browser verification: WebKit 27 passed and Chromium 3 passed, screenshots inspected. PR checks pending.

CI inventory repair: PR #1355 initial head 7b0b8f95d reproduced the same curation failure in two jobs. Production build/output audit passed (235 manifests) and next-start preflight passed 5/5 platform/search cases before adding mobile-native-platform.spec.ts to the existing curated CI list. Focused curation guard failed first, then passed 6/6. Required checks, deployment dependencies and branch protections are unchanged.


## Audit animation implementation (2026-10-30)

- [x] AM1 depends_on: [] Verify the two approved opacity recipes and existing component lifecycles.
- [x] AM2 depends_on: [AM1] Add failing presence, input-modality and lifecycle regressions; implement cancellable CSS opacity presence across the three components.
- [x] AM3 depends_on: [AM2] Verify keyboard immediacy, inert exits, rapid reversal, reduced motion, and real pointer/touch behavior in unit/browser tests; inspect screenshots.
- [x] AM4 depends_on: [AM3] Run full web/typecheck/production verification and prepare the PR #1355 update.

Release completion receipt: exact-head CI and Pushover delivery are tracked in output/animation-implementation-2026-10-30/verification.json after push.

Review: Implemented only opacity presence for Feedback/Share (160ms entry, 120ms exit) and Columns (125ms entry, 100ms exit), reusing --ease-out. Keyboard/assistive activation and Escape remain immediate; reduced motion remains suppressed. Exits become inert/aria-hidden at logical close, rapid reopen cancels removal, and share export generations prevent stale downloads or busy-state changes. Red baseline: 20 failed / 1 passed before implementation. Focused component regressions: 64 passed; hook coverage 100% lines/functions, 97.67% statements and 96.29% branches. WebKit and Chromium each passed 11 real pointer/touch, keyboard, reduced-motion and frame-step cases; screenshots inspected at 10%, 50% and settled entry with unchanged geometry. React component/lifecycle review completed. The first production share regression found stale edited captions on rapid reopen; a failing unit regression confirmed it, and logical reopening now resets caption/preview while preserving edits through ordinary post refresh. Another failing unit regression confirmed late clipboard status could reach a new opening; generation checks now suppress it. Final full web: 11,127 passed / 10 skipped across 1,117 files. Typecheck passed; production build/output audit passed (235 manifests). Combined production browser regressions passed 35/35. The curation guard failed for the new unclassified spec, then passed 6/6 after its 11-case production preflight and registration. git diff --check clean. CI and notification are the remaining release steps, tracked in the completion receipt. No new dependencies or motion on financial data, navigation, layout or column changes. Hardware feel remains unverified; no physical phone/tablet is connected.


## MarketDesk publication transport (2026-10-31)

- [x] HP1 depends_on: [] Identify the actual publication transport and reproduce the stale pooled socket with a managed UID 1000 read-only transaction.
- [x] HP2 depends_on: [HP1] Reproduce publication batching after idle with a failing regression; retain read pooling and no ambiguous write replay.
- [x] HP3 depends_on: [HP2] Retire connections before non-read pipelines, update owner docs/runbook, and obtain independent review.
- [ ] HP4 depends_on: [HP3] Run focused and full required gates; publish a focused PR, verify exact-head CI, accepted notification and authorized admin merge.
- [ ] HP5 depends_on: [HP4] Verify the normal deployment and an actual new MarketDesk publication without changing retries, policy or provider pacing.

Review:
- Live `3ce7f24` SELECT 1 succeeds, a read-only transaction after 18s idle fails RemoteDisconnected inside the 30s API pool lifetime, and a fresh SELECT1 succeeds. Real publisher imports api.db_http; the separate daemon pool already expires after 5s. No model, broker, credential or cooldown changes.

- Three true red regressions and three controls; 207 focused tests passed in 21.85s. Independent review found no blocking issues. Documentation contract: 195 passed. Full infrastructure: 3,792 passed, 16 skipped. Web: 11,592 passed, 21 skipped; TypeScript passed.
- Full Python: 19,740 passed, 12 skipped, 63 subtests, with the unchanged credential-cancellation timing failure also observed before this repair (0.352s versus 0.2s). Its complete module passed separately: 11 passed. The assertion and credential code were not changed. Exact-head CI and production verification remain pending.

## Emil motion cleanup (2026-11-12)

- [x] EM1 depends_on: [] Reconfirm audit findings on current main and inspect scoped web instructions.
- [x] EM2 depends_on: [EM1] Remove recurring watchlist entrance and streaming prose pulse while preserving reduced-motion behavior.
- [x] EM3 depends_on: [EM2] Run focused UI verification and review the diff.
- [ ] EM4 depends_on: [EM3] Create a template-compliant PR, watch exact-head CI to green, and send the required notification.

Review: Removed the watchlist 480 ms entry and 80 ms delay; kept reduced-motion handling for row hover. Removed the 1.5 s text pulse; the separate chat cursor still indicates streaming and stops for reduced motion. Full web Vitest: 11,153 passed, 10 skipped. Watchlist and chat Playwright: 7 passed. `git diff --check` clean.
