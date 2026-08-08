# REMEDIATION_LOG.md — PART B execution log

**Contract:** `TEST_AUDIT.md` §9 (frozen backlog T-001…T-054). One task per change set; every new test demonstrated RED before GREEN; never weaken an assertion to pass; BLOCKED requires root-cause hypothesis.
**Branch:** `test-audit-remediation` (worktree off `2a75496a` — the main tree belongs to the concurrent session and is never touched).
**Baseline (worktree, clean HEAD):** recorded below before the first task.

| Task | Status | Commits | Evidence |
|---|---|---|---|
| T-001 | DONE | (this commit) | RED: `playwright test --list` imported e2e/prices-performance.test.js which printed "Starting Next.js server for testing..." and spawned `npm run dev` during LISTING (rtk tee 1786165991_playwright.log); full-run crash 7s in at audit time (runs/playwright-r1.log, spawn /bin/sh ENOENT). GREEN: with `testIgnore: ["**/*.test.js"]` in both configs, `--list` exits clean: "Total: 419 tests in 123 files", no side effects. |

## Baseline

_(pending — pytest gate + vitest on clean 2a75496a; running via wt-baseline.sh)_

## Entries

### T-001 — un-break the Playwright runner
- **AC:** `npx playwright test --list` exits 0 and lists ~123 specs; no spec self-spawns a server. **Met.**
- **Files:** `web/playwright.config.ts`, `web/playwright.no-server.config.ts` (added `testIgnore: ["**/*.test.js"]` + rationale comment). The legacy script is retained on disk (it is a manual perf harness, not a spec); it is simply no longer collected.
- **Note:** the red demonstration itself proved the blast radius — during `--list` the file spawned a real `npm run dev`; stray-process sweep afterwards confirmed clean (no listeners on 3000/8321/8765).
