# Approved AI industry B implementation

Reference local artifacts: `/Users/joemccann/dev/apps/finance/radon/web/.codex-design/ai-industry-20260916/`. User selected B; typography and Straddle chart composition approved. Production must use real snapshot, no synthetic observations or narrative. Top-level /ai-industry; preserve /regime/llm bookmarks. No backend source repairs in this PR. No local test suites.

Dependency graph: T1 -> T2,T3,T4; T2+T3+T4 -> T5 -> T6.
- [x] T1 depends_on: [] Create clean worktree from origin/main, inspect reference/contracts.
- [x] T2 depends_on: [T1] Value-chain panel, semantic measure library and source coverage with current snapshot statuses.
- [x] T3 depends_on: [T1] Top-level route/navigation and legacy redirect/query migration.
- [x] T4 depends_on: [T1] Shared Regime chart reuse, range/brush, cadence-correct data and regressions.
- [ ] T5 depends_on: [T2,T3,T4] Integrate, browser inspect, CI-focused coverage and review.
- [ ] T6 depends_on: [T5] Commit/push/open PR, exact-head CI green, one accepted Pushover notification.
