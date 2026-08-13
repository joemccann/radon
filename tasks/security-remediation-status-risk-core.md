# Risk-core remediation status

Source inventories: `tasks/security-remediation-inventory-high-bug.md` and `tasks/security-remediation-inventory-bug.md`.

| Finding | Status | Patch | Regression |
|---|---|---|---|
| HB-109 / BUG-110 | Fixed | Combo sells are close-outs only through held BAG units; partial closes scale basis and excess enters full risk math. | `web/tests/position-trade.test.ts` partial/oversized combo cases |
| HB-110 | Fixed | Position trades preserve each leg's effective expiry in payload and risk identity. | `web/tests/position-trade.test.ts` calendar expiry case |
| HB-111 | Fixed | Existing option and stock short obligations consume portfolio collateral before new shorts. | `web/tests/order-risk.test.ts` option/share collateral reuse cases |
| HB-112 | Fixed | Coverage is expiry-aware; reverse calendars remain unbounded and other mixed-expiry risk is broker-indeterminate. | `web/tests/order-risk.test.ts` reverse-calendar case |
| HB-113 | Fixed | Same-expiry bounded structures evaluate aggregate payoff at every strike kink. | `web/tests/order-risk.test.ts` short-put-butterfly case |
| HB-115 / BUG-114 | Fixed | What-if identity includes signed premium and a resolved portfolio-state revision; edits abort/debounce/refetch. | `web/tests/whatif-margin.test.tsx` price and portfolio mutation cases |
| HB-114 | Fixed | Option close-outs carry exact contract identity and project the retained portfolio; removing a protective wing exposes and blocks newly naked short risk. | `web/tests/order-risk-chokepoint.test.tsx` protective-long close regression; focused risk suites 156 passed |
| BUG-111 | Fixed | Locate telemetry follows resolved projected naked risk, not any ticker-matching position. | `web/tests/short-availability.test.ts` unrelated-position and covered cases |
| BUG-112 | Fixed | Risk-gate parent notification runs after commit. | `web/tests/order-risk-chokepoint.test.tsx` render-update regression |
| BUG-113 | Fixed | Unknown stock basis preserves coverage boundedness but withholds dollar max loss. | `web/tests/order-risk.test.ts` missing-basis case |
| BUG-115 | Fixed with backend owner | Web retains signed credit premium in the complete what-if key/body; Python BAG validation is owned by backend remediation. | `web/tests/whatif-margin.test.tsx`; `scripts/tests/test_ib_whatif_margin.py` |
| BUG-116 | Fixed | OPEN/CLOSE intent compares order quantity with matching held quantity. | `web/tests/orders-display.test.ts` over-close case |
| BUG-117 | Fixed | Stock intent aggregates stock legs inside multi-leg positions. | `web/tests/orders-display.test.ts` covered-call stock case |
| BUG-119 | Fixed | Portfolio contract conversion prefers per-leg expiry. | `web/tests/pricesProtocol.test.ts` calendar/diagonal case |
| BUG-018 | Fixed | Kelly TypeBox, wrapper, and Python CLI boundaries enforce finite probability, odds, fraction, and bankroll domains. | `lib/tools/__tests__/input-domain-guards.test.ts`; `scripts/tests/test_kelly_domain_guards.py` |
| BUG-019 | Fixed | Scanner `top` and VCG `days` are bounded positive integers in schemas and wrappers. | `lib/tools/__tests__/schemas.test.ts`; `lib/tools/__tests__/input-domain-guards.test.ts` |
| BUG-020 | Fixed | VCG proxy is allowlisted at TypeBox, wrapper, and Python boundaries; non-HYG exploratory scans cannot publish the shared canonical snapshot. | `lib/tools/__tests__/schemas.test.ts`; `scripts/tests/test_vcg_input_guards.py` |
| HB-017 | Fixed | Browser crashes invalidate the cached handle, retry once, and terminate the daemon after bounded consecutive failures. | `web/tests/newsfeed-scraper.test.ts` browser lifecycle/scheduler cases |
| HB-041 / SEC-053 | Fixed | Partial Playwright initialization closes launched resources; storage state is atomically replaced at mode 0600. | `web/tests/newsfeed-scraper.test.ts` initialization and permission cases |
| HB-042 | Fixed | Startup seeding initializes only ENOENT/verified-empty files and propagates other read failures. | `web/tests/newsfeed-scraper.test.ts` existing-file read error case |
| HB-043 | Fixed | Existing post history returns empty only for ENOENT; invalid/corrupt data is quarantined and fails closed. | `web/tests/newsfeed-scraper.test.ts` malformed-history case |
| HB-044 | Fixed | Whole-file live/archive persistence is serialized with a cross-process lock and uses flushed same-directory atomic replacement. | `web/tests/newsfeed-scraper.test.ts` concurrent snapshot case |
| BUG-031 | Fixed | A durable DB-dirty marker retries the full merged dataset and prevents a healthy heartbeat until success. | `web/tests/newsfeed-cycle-ordering.test.ts` dirty DB retry case |
| BUG-032 | Fixed | A durable media-dirty marker retries idempotent upload without requiring another image change. | `web/tests/newsfeed-cycle-ordering.test.ts` dirty media retry case |
| BUG-033 | Fixed | Relative-image migration validates and atomically replaces each file while retaining `.bak`. | `web/tests/newsfeed-migrate-relative-image-urls.test.ts` apply/backup case |
| BUG-034 | Fixed | Generic-attribution scrub validates and atomically replaces live/archive files while retaining `.bak`. | `web/tests/newsfeed-scrub-generic-image-attributions.test.ts` apply/backup case |
| BUG-035 | Fixed | Taxonomy updates use a cross-process lock and atomic JSON replacement. | `web/tests/newsfeed-taxonomy.test.ts` concurrent-process case |
| HB-032 | Fixed | Required UW failures remain typed/degraded, affected tickers are skipped, provider details are scrubbed, and degraded discovery cannot mirror or alert. | `scripts/tests/test_discover.py::test_required_uw_failure_cannot_publish_or_alert`; discovery suites 27 passed |
| SEC-037 | Fixed | Drift-audit repository reads walk beneath an opened root with no-follow descriptors and reject nested/final symlinks plus traversal. | `cloud/tests/test_drift_audit.py`; 31 passed |
