# DeepSec remediation status

Source: `data/radon/reports/report.md` (resolved from `.deepsec/data/radon/reports/report.md`).

Draft PR: `https://github.com/joemccann/radon/pull/21`.

## Reconciliation

| Family | Inventoried | Source-actionable | Evidence |
|---|---:|---:|---|
| Security | 142 | 0 | `security-remediation-status-security.md` |
| High bug | 122 | 0 | backend, risk-core, desktop, mobile, web, and supplemental ledgers |
| Bug | 130 | 0 | backend, risk-core, desktop, mobile, web, and supplemental ledgers |
| **Total** | **394** | **0** | every report ID is represented in the status ledgers |

Security disposition is 95 fixed, 45 duplicate, and 2 deferred-external. The two deferred rows, SEC-054 and SEC-055, have complete source remediation; only operator-controlled CDN/origin and reachable-history purges remain.

## Verification

- Python application: 6,056 passed, 1 skipped, 90 deselected; 62.64% branch coverage.
- Cloud: 825 passed, 4 skipped.
- Vitest: 590 files and 6,073 tests passed; 83.67% statements, 76.56% branches, 86.78% functions, 86.87% lines.
- Browser: 8 desktop and 1 mobile Playwright tests passed; the option-ticket screenshot was inspected.
- Boundary: 6 browser-tool and 22 startup-protocol tests passed.
- TypeScript, ESLint (0 errors), production build, 158 output-trace audit manifests, and `git diff --check` passed.

## Operator follow-through

- Purge the removed dashboard plate objects from CDN and origin caches.
- Remove the disclosed account-bearing plate binaries from reachable repository history under the repository retention policy.
