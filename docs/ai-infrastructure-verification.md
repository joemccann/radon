# AI infrastructure: source and release verification

## Financial mapping review, 2026-09-07

The first extraction of the five-issuer cash-flow panel was reconciled to the original cash-flow statements. These are reference observations for validating the taxonomy mapping, not hardcoded production values. Collection reads fresh SEC facts. Mapping review does not mean every historical or future row was manually reviewed; accession, fiscal period, raw snapshot and reviewed mapping version remain attached to each row.

| Issuer | Period | OCF, USD millions | Cash capex, USD millions | Definition / primary evidence |
|---|---|---:|---:|---|
| MSFT | Year ended 2026-06-30 | 182,935 | 115,948 | Cash additions to property and equipment. Finance lease additions are separate. [Cash-flow statement](https://www.microsoft.com/en-us/investor/earnings/fy-2026-q4/press-release-webcast) |
| AMZN | Six months ended 2026-06-30 | 71,419 | 98,411 | Gross purchases of property/equipment, `PaymentsToAcquireProductiveAssets`. Proceeds/incentives of 2,101 are separate; gross capex is not the issuer-net FCF denominator. [Cash-flow statement](https://ir.aboutamazon.com/news-release/news-release-details/2026/Amazon-com-Announces-Second-Quarter-Results/default.aspx) |
| GOOGL | Six months ended 2026-06-30 | 84,859 | 80,598 | Cash purchases of property/equipment. [10-Q cash-flow statement](https://www.sec.gov/Archives/edgar/data/1652044/000165204426000071/goog-20260630.htm) |
| META | Six months ended 2026-06-30 | 64,088 | 49,113 | Cash purchases of property/equipment; principal payments on finance leases excluded and separately disclosed. [Cash-flow statement](https://investor.atmeta.com/investor-news/press-release-details/2026/Meta-Reports-Second-Quarter-2026-Results/default.aspx) |
| ORCL | Year ended 2026-05-31 | 31,977 | 55,663 | Cash capital expenditures, before financing/customer-prepayment adjustments. [Cash-flow statement](https://investor.oracle.com/investor-news/news-details/2026/Oracle-Announces-Record-Q4-and-FY-2026-Results-Driven-by-Cloud-Infrastructure--Cloud-Applications/) |

The schema and transformations must distinguish six-month cumulative figures from quarter cash flows. The panel's primary derived ratio is TTM on comparable definitions; a six-month ratio cannot substitute. Oracle and Microsoft fiscal years differ from calendar reporters, so a basket is unavailable until periods are synchronized. Company cash capex is not AI-only spending.

## Source-access acceptance

- Vercel and GPU Rental Prices returned HTTP 200 through the implemented direct transport. The latter had returned 403 during the earlier plan review; the current successful probe establishes retrieval at this check, not a permanent SLA.
- SEC companyfacts returned HTTP 200 for all seven configured issuers. A contact-bearing user agent is required for scheduled use.
- OpenRouter, Artificial Analysis and Vast credentials were absent from the local environment inspected. Unauthenticated 401s are not evidence of invalid configured keys. No successful authenticated collection is claimed.
- EIA sub-BA data can be read for DOM. Weather-normalized residuals remain withheld until actual station coverage, seasonal history and control validation exist.
- Public pages, a purchased API key and public redistribution rights are distinct. Artificial Analysis is gated for internal use unless the configured entitlement supports the intended distribution.

Raw source payloads and live verification outputs are local artifacts, never credentials or fabricated fixtures. Tests use synthetic observations explicitly labeled as fixtures. The browser handoff distinguishes fixture scenarios from actual collected source snapshots.

## Release verification

Local application Python suite: **12,392 passed, 19 skipped, 16 subtests passed**, covering the complete configured suite in two disjoint partitions: 634 subscription tests and 11,758 remaining application tests. The final runs completed without failures. The initial loaded-host run exposed an order-worker test scheduling assumption; the regression now explicitly waits for worker startup and always releases its blocking event, preserving timeout and duplicate-SELL assertions.

Production build and TypeScript checks passed. Production Playwright verification passed six scenarios; a separate actual-source sweep passed with 15 captured views, zero page errors and zero horizontal overflow. Browser tests isolate account and broker data from the verified AI observations.

Full cloud suite: **1,873 passed, six skipped**, with Caddy v2.11.4 verified against release checksums. The new collector is included in bootstrap installation, the explicit auto-sync inventory and source-health catalogs. Final focused AI, order-worker, code-map and watchdog contracts: **140 passed**. Full root Vitest: **9,103 passed across 924 files**. Final TypeScript check passed. Exact-head CI is tracked on the pull request; all applicable checks must finish green before handoff. The AI-cycle shadow state remains experimental: prospective history and outcome labels are required to estimate false-alert rates, precision and lead time. A successful software test is not evidence of predictive value.

## EIA hour-ending semantics review

The current [official Form EIA-930 instructions](https://www.eia.gov/survey/form/eia_930/instructions.pdf), General Instructions on page3, specify hour-ending timestamps in UTC. Page4 includes hourly actual demand by sub-region within the daily files. A direct HTTP200 read of the [sub-BA API metadata](https://api.eia.gov/v2/electricity/rto/region-sub-ba-data/) confirms Form EIA-930 as the source and distinguishes UTC `hourly` frequency from `local-hourly`.

`parse_eia` now maps the API timestamp to `period_end` and subtracts one hour for `period_start`; the unchanged numeric measurement remains MWh over that one-hour interval. A timestamp04:00UTC therefore means03:00–04:00UTC. Requested completed-day boundaries are01:00 on the first day through00:00 on the following day after the final operating day. The parser records `eia-hour-ending-v2`, the timestamp convention, original publisher period and official methodology URL.

Regression fixtures pin an ordinary hour, the UTC midnight boundary and daily request bounds. Tests were added without starting pytest concurrently with root's full suite. Archived raw responses were reparsed into149new method-version rows in isolated `implementation/ai-cycle-hour-ending-v2.sqlite`, preserving149version1rows for audit. A fresh `implementation/ai-cycle-verified.sqlite` excludes the incorrect pre-release EIA method from displayed data while retaining all other sources. The original database and source-response archives remain intact; no production data was changed. `implementation/snapshot.json` now points to evidence from the corrected verified database.

## Integration with updated main

Main advanced to `00b6aa22` during review. The merge affected only generated code maps; both branches' source changes were retained. The updated full application partition completed with 11,759 passing tests and one documentation-ownership failure, alongside the unchanged 634-test subscription partition. The missing cloud owner documentation was added; all 146 ownership, code-map and merged Equibles regressions then passed. This covers 12,394 application tests after integration, with 19 skipped and 16 subtests. No test gate was weakened.

## Pull request release gate

[PR #346](https://github.com/joemccann/radon/pull/346) passed every applicable check on `5fa75a9b910ff9cd31a4ea5c90f49a08e135db1e`, including both coverage gates, image builds, the production perimeter and Playwright. Every subsequent release head is checked again before notification. The standalone visual handoff contains 15 embedded actual-source captures; seven selectors and desktop/mobile report layouts passed with zero errors or overflow. Source eligibility and predictive limitations remain unchanged.

## CI-discovered Compose validator repair

The final-head cloud suite exposed a pre-existing `printf | grep -q` race under `pipefail`. Early grep exit can signal the producer and invert a matching validation result. Deterministic two-megabyte/small-pipe regressions reproduced six failures across all three validator copies, including rejected valid bodies and accepted privileged bodies. The repair feeds validators directly while preserving every rule and the three-copy parity contract. Focused verification passed 125 tests, including nine large-body regressions and the originally failing refresh scenario. All three shell files pass syntax checks and validator parity. The final full cloud rerun passed **1,882 tests, six skipped**. The latest-head CI remains the final release gate.
