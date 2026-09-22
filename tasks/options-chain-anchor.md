# Approved options chain anchor implementation

User confirmed the desktop/mobile design on 2026-09-15. Fixed underlying bar between independently scrollable lower/higher strike panes; live quotes never repartition rows or recenter. Explicit Recenter updates the anchor and resets panes. Preserve order risk, selection, filters, expiry semantics, realtime ownership, Clear tokens and theme parity.

Dependency graph: T1 -> T2; T1 -> T3; T1 -> T4; T2 + T3 + T4 -> T5 -> T6.

- [x] T1 depends_on: [] - Confirm design, inspect source and create isolated worktree from current main.
- [x] T2 depends_on: [T1] - Shared stable-anchor hook, desktop panes and shared spot bar (primary).
- [x] T3 depends_on: [T1] - Mobile panes and mobile regressions (mobile agent).
- [x] T4 depends_on: [T1] - Desktop/phone browser and regression coverage, CI selection (verification agent).
- [x] T5 depends_on: [T2,T3,T4] - Review integration, static checks and publish PR; current main reserves codemap commits for nightly maintenance.
- [ ] T6 depends_on: [T5] - Wait for exact-head CI green, inspect rendered browser artifacts, send accepted Pushover notification.

No local test suites. GitHub runners own all test execution. Existing dirty checkout is untouched. Main implementation lives in /tmp/radon-options-chain-anchor. No merge/deploy requested.

## Review
PR https://github.com/joemccann/radon/pull/449. TypeScript and focused ESLint pass; Impeccable detector reports zero findings on changed components. Added shared hook, desktop isolation, mobile and four theme/viewport browser regressions. Initial CI run 35008263230: seven Vitest shards passed; shard 1 exposed two test assumptions (loading readiness and currency formatting), corrected. All 102 existing browser scenarios passed; four new scenarios exposed a single-tick propagation lag through the ticker workspace's effect-updated price getter. Preserve the live-update assertions and forward reactive prices directly. Desktop/mobile runner screenshots visually reviewed. No local suites; latest repair verification pending. Feature branch leaves nightly-owned codemaps untouched.
