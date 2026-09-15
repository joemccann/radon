# Approved options chain anchor implementation

User confirmed the desktop/mobile design on 2026-09-15. Fixed underlying bar between independently scrollable lower/higher strike panes; live quotes never repartition rows or recenter. Explicit Recenter updates the anchor and resets panes. Preserve order risk, selection, filters, expiry semantics, realtime ownership, Clear tokens and theme parity.

Dependency graph: T1 -> T2; T1 -> T3; T1 -> T4; T2 + T3 + T4 -> T5 -> T6.

- [x] T1 depends_on: [] - Confirm design, inspect source and create isolated worktree from current main.
- [x] T2 depends_on: [T1] - Shared stable-anchor hook, desktop panes and shared spot bar (primary).
- [x] T3 depends_on: [T1] - Mobile panes and mobile regressions (mobile agent).
- [x] T4 depends_on: [T1] - Desktop/phone browser and regression coverage, CI selection (verification agent).
- [ ] T5 depends_on: [T2,T3,T4] - Review integration, static checks, generate codemap and publish PR.
- [ ] T6 depends_on: [T5] - Wait for exact-head CI green, inspect rendered browser artifacts, send accepted Pushover notification.

No local test suites. GitHub runners own all test execution. Existing dirty checkout is untouched. Main implementation lives in /tmp/radon-options-chain-anchor. No merge/deploy requested.

## Review
Implementation and static review complete. TypeScript and focused ESLint pass; Impeccable detector reports zero findings on changed components. Added shared hook, desktop isolation, mobile and four theme/viewport browser regressions. No local test suites executed. CI and screenshot evidence pending. Current main assigns codemap commits to the nightly job; feature branch leaves generated maps untouched.
