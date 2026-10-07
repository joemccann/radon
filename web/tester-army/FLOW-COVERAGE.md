# Workstation flow coverage map

Coverage levels: SDK route checks verify visible measurement/empty state and redirects. SDK journeys verify named interactions and mocked wire payloads. Existing Playwright suites are retained and CI curation is unchanged; the financial smoke job is non-gating and historical held-out specs remain held out. References identify coverage code, not execution evidence.

All current 67 App Router pages appear exactly once in the manifest test. Four guarded leaves require real Clerk/server authorization and are outside the deterministic authless browser fixture. Authentication/MFA, real brokerage execution/fills, entitlement behavior, third-party exports, every chart gesture and all combinatorial trades remain explicit limitations.

| Route | Added SDK coverage | Existing focused coverage / limitation |
|---|---|---|
| `/ai-industry` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/llm` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/` | render/redirect smoke; news tag filter, bookmark payload, media/Escape | newsfeed-sharing.spec.ts, clear-overview.spec.ts |
| `/dashboard` | render/redirect smoke; news tag filter, bookmark payload, media/Escape | newsfeed-sharing.spec.ts, clear-overview.spec.ts |
| `/portfolio` | render/redirect smoke; keyboard ticker search | ticker-search-chain.spec.ts, clear-overview.spec.ts |
| `/performance` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/orders` | render/redirect smoke | order-combo.spec.ts, stop-order-desktop.spec.ts; broker lifecycle only mocked |
| `/scanner` | render/redirect smoke; discovery candidate, pair validation/scan payload | scanner-ticker-scan.spec.ts; scanner modes use fixture data |
| `/watchlist` | render/redirect smoke; desktop sort, mobile cockpit navigation | clear-workspace-interactions.spec.ts |
| `/discover` | render/redirect smoke; discovery candidate, pair validation/scan payload | scanner-ticker-scan.spec.ts; scanner modes use fixture data |
| `/flow-analysis` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/flow-analysis/AAPL` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/AAPL?tab=book` | render/redirect smoke; separate SPY stock verification/confirmation payload; CRCL native SELL quote and confirmed option payload | ticker-search-chain.spec.ts; options/combo regression suites are retained |
| `/options` | render/redirect smoke; ticker entry, measurement tabs/history, 503 retry | clear-workspace-interactions.spec.ts, options-exposure.spec.ts; export only mocked |
| `/options/net-gex?symbol=AAPL` | render/redirect smoke; ticker entry, measurement tabs/history, 503 retry | clear-workspace-interactions.spec.ts, options-exposure.spec.ts; export only mocked |
| `/options/rv-ratio?symbol=AAPL` | render/redirect smoke; ticker entry, measurement tabs/history, 503 retry | clear-workspace-interactions.spec.ts, options-exposure.spec.ts; export only mocked |
| `/options/exposure?symbol=AAPL` | render/redirect smoke; ticker entry, measurement tabs/history, 503 retry | clear-workspace-interactions.spec.ts, options-exposure.spec.ts; export only mocked |
| `/journal` | render/redirect smoke; populated/empty ranges, realized totals | clear-workspace-interactions.spec.ts |
| `/cta` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/alerts` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/preferences` | render/redirect smoke; validation, 422 draft retention, retry/reset payload | profile-credentials-tab.spec.ts |
| `/profile` | render/redirect smoke; failed username save/retry payload | profile-credentials-tab.spec.ts; credential stores only mocked |
| `/regime` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/ats` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/backtest` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/bpi` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/breadth` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/calm-streak` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/cor` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/cot` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/credit` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/cri` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/credit-vix` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/curve` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/dispersion` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/divyield` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/gex` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/grg` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/hhlev` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/hyad` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/iei-hyg` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/iv-spread` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/ivrank` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/ma-ratio` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/margin` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/rsi-oversold` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/short` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/skew` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/skew2d` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/straddle` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/streaks` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/trin` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/vcg` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/vixcor` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/vixts` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/regime/panic-index` | explicit empty measurement state | canonical Clear inventory asserts the same empty state; deterministic fixture has no panic reading |
| `/regime/vol-cone` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/internals` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/setup` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/demo-pending` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/trial-expired` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/kit` | render/redirect smoke | clear-all-pages.spec.ts; smoke only |
| `/admin` | excluded: auth/provider gate | requires Clerk operator authorization; admin-panel authless run cannot render this gate |
| `/admin/slm-review` | excluded: auth/provider gate | clear-auth-pages.spec.ts; provider-backed gate, not simulated |
| `/sign-in` | excluded: auth/provider gate | clear-auth-pages.spec.ts; provider-backed gate, not simulated |
| `/sign-up` | excluded: auth/provider gate | clear-auth-pages.spec.ts; provider-backed gate, not simulated |
| `/research-workbench` | render/redirect smoke | research-workbench.spec.ts; provider-backed execution excluded |

## Verification scope

References above identify coverage code. Fresh execution results are recorded
in the pull request for its current head. Historical dated curation notes do
not establish current-head success.

Provider-backed, entitlement, live-broker and external-provider operations are
outside deterministic fixture coverage. An empty-state render does not verify
a populated measurement. No runtime risk or authorization guard is bypassed.
