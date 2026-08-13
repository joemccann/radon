# Supplemental HIGH_BUG/BUG reconciliation

## Dependency graph

| Task | depends_on | Result |
|---|---|---|
| T7A - security/chat/boundary tranche | `[]` | complete; see `security-remediation-status-supplemental-security.md` |
| T7B - route/API tranche | `[]` | complete; see `security-remediation-status-supplemental-routes.md` |
| T7C - exact reconciliation | `[T7A, T7B]` | complete |

## Exact counts

| Ledger | Fixed | Duplicate | Deferred | Actionable | Total |
|---|---:|---:|---:|---:|---:|
| Security/chat/boundary | 31 | 6 | 0 | 0 | 37 |
| Route/API | 23 | 0 | 0 | 0 | 23 |
| **Total** | **54** | **6** | **0** | **0** | **60** |

## ID coverage

- HIGH_BUG: `HB-046` through `HB-061`, `HB-095` through `HB-098`, `HB-100`, `HB-101`, `HB-116` = **23**.
- BUG: `BUG-001` through `BUG-003`, `BUG-011`, `BUG-038` through `BUG-061`, `BUG-069`, `BUG-080`, `BUG-095`, `BUG-099`, `BUG-108`, `BUG-129` = **37**.
- Every ID is mapped exactly once in the two ledgers; remaining actionable = **0**.

## Verification summary

- Security/chat focused: **153 Web + 48 Python + 59 boundary/newsfeed tests passed**; TypeScript passed.
- Route/API focused: **155 Web + 9 Python tests passed**; TypeScript, Python compile, and diff check passed.
