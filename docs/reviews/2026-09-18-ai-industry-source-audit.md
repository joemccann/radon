# AI Industry: source, history and chart audit

Audit date: 2026-09-18 UTC. Baseline: production snapshot generated
2026-09-17T07:32:36.836829Z; code base `b33032b4`. Scope includes all 16
registered sources, 18 indicators, source transformations and chart selection.

## Verdict

The page is a research dashboard, not a complete measurement of the AI economy
and not a trading signal. Several flat lines are genuine repeated observations
of unchanged published prices or benchmark results. Others were made misleading
by selecting the wrong stored series, omitting metric history, and hiding
sampling. Neither a flat quote nor a repeated benchmark proves stable demand,
utilization or performance. Empty measurement states must remain explicit.

Remove the empty M1 market-pricing module and its unconnected IB/UW source cards.
They had no observations or collection integration; their “unavailable” badges
did not establish an IB/UW outage. Ticker research links and the warning that a
theme is not measured portfolio exposure remain useful. Actual pricing, flow,
OI and volatility belong in the existing ticker research workbench until this
page has a defined, implemented market-context measurement.

## Method and evidence boundary

- Read 356,644 stored vintages into an isolated SQLite evidence copy; examined
  3,342 source/method/cohort/unit series after vintage selection.
- Inspected registry, collectors, derived metrics, persistence, bounded API
  snapshot and chart consumers. Replayed primary responses and researched
  publisher archives; authenticated probes used configured provider credentials
  without exposing them or modifying broker state.
- Verified public payloads for Vercel, GPU Rental Prices, NOAA, Ramp, Open Design
  and LiquidCompute; authenticated probes covered OpenRouter, Artificial
  Analysis, Vast, EIA and all ten SEC issuers. Reviewed issuer releases separately.
- Raw publisher bytes are archived by SHA-256. Checked-in disclosure imports
  retain exact quotes, URLs, periods and definitions; a raw-byte hash is included
  only when those bytes were retrieved. This is not a claim that every individual
  historical value received independent manual review.
- `python -m scripts.ai_cycle.audit --copy-to /tmp/new-ai-audit.sqlite` provides
  a repeatable coverage inventory without provider calls or production writes.
  It reports missing dates within each observed daily series, constant values,
  coverage, incomplete flags and raw-hash counts. Model membership changes are
  not automatically data gaps; cadence/cohort context remains necessary.

## Every source

| Source | Baseline and verification | Backfill result / limit |
| --- | --- | --- |
| [OpenRouter](https://openrouter.ai/docs/cookbook/administration/data-api) | 34,994 latest observations; 2,605 series; Jan 2025–Sep 16 2026. UTC completed-day model totals plus residual and separate application aggregates. Rankings omit private traffic and are not global AI demand. | Re-requested June 15 and July 15 2025: publisher returned application aggregates but zero D1 model rows. Preserve these two gaps; no interpolation or zeros. Top-100 application truncation cannot establish market-wide concentration. Respect daily quota. |
| [Vercel AI Gateway](https://vercel.com/docs/ai-gateway/leaderboards) | 29,497 observations; 177 series; Oct 1 2025–Sep 16 2026. Tokens, requests and spend are different denominators. Live export checked. | Publisher rejects dates before Oct 1 2025. No comparable earlier gateway series established. Show leading token-share labs first, not alphabetically selected monetization series. |
| [GPU Rental Prices](https://github.com/adriannutiu/gpu-rental-prices/tree/main/data/snapshots) | 6,807 observations; 111 series; July 5–Sep 16 2026. 78 constant series. Public archive has 75 daily snapshots through Sep 17. Asking prices, not executed transactions. | Earliest repository snapshot July 5. Mirrors on HF/Kaggle/Zenodo do not provide independent evidence or an earlier comparable history. Repository [license](https://github.com/adriannutiu/gpu-rental-prices/blob/main/LICENSE) is CC BY 4.0; website bulk-data terms differ and must not be generalized. |
| [Lambda](https://lambda.ai/pricing) | No standalone observations. Lambda quotes occur within the GPU Rental Prices lineage. | Do not count an aggregator and its underlying Lambda quote as independent sources. No verified historical standalone collector added. |
| [Artificial Analysis](https://artificialanalysis.ai/data-api/docs) | 72 observations; 24 fixed-basket series; Sep 9–17. Basket versions differ; quoted model prices remained constant within a basket. Authenticated live catalog checked. | Current catalog API is not a historical fixed-basket archive. Do not splice annual reports or changing model baskets into a continuous price index. Latest basket must drive the chart. Redistribution remains entitlement-dependent. |
| [SEC EDGAR](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | 5,082 baseline latest observations; 237 series; Jan 2009–Aug 2026. All ten configured issuers checked. Fiscal periods, filing vintages, units and TTM reconciliation retained. | Companyfacts includes comparative periods before the XBRL mandate. Replayed 10,587 reported facts, including 131 pre-2009 vintages; earliest Dec 31 2006. Extend collector/store/service floors, not every issuer’s claimed coverage. Foreign-issuer/tag coverage remains uneven. |
| [Issuer disclosures](https://www.sec.gov/edgar/search/) | No baseline rows. Reviewed primary NVIDIA and Dell earnings releases rather than substituting total-company SEC revenue for AI hardware revenue. | Added 18 reviewed observations: eleven NVIDIA quarterly Data Center revenues starting January 2024; seven Dell AI-server order, revenue and backlog facts. Separate NVIDIA FY2027 framework; never add orders to revenue or treat backlog as shipments. See checked-in reviewed JSON for every primary URL and quote. |
| [Vast.ai](https://docs.vast.ai/api-reference/search/search-offers) | 27 observations; three series; Sep 9–17. Nine days of zero offers for the narrow US verified high-reliability H100 SXM filter. Authenticated queries with both existing and documented offer-type spellings returned zero offers. | A zero result is zero matching listings, not zero GPUs or full utilization. Current search has no historical fleet denominator; no credible precollection backfill. |
| [EIA](https://www.eia.gov/electricity/gridmonitor/) | 2,815 daily averages and peaks, every date Jan 1 2019–Sep 15 2026, plus 148 obsolete hourly records. DOM sub-balancing-authority load, not isolated data-center consumption. | Earlier July 2018 query returned no rows. Preserve documented 2019 floor. Require 24 unique UTC hour-ending readings; duplicate hours reject collection, partial days flag incomplete. Obsolete hour-start/raw-hour methodologies remain audit-only. |
| [NOAA](https://www.ncei.noaa.gov/access/services/data/v1) | 11,988 observations: four fixed stations × 2,997 dates, July 1 2018–Sep 13 2026, no interior date gaps. | Weather control, not AI load. Reporting lag is not zero temperature. Current fixed-station window already complete; no fabricated weather adjustment or causal attribution. |
| [Portkey](https://portkey.ai/rankings/weekly) | No observations. Public rankings exist but stable licensed completed-day export and estimation method were not verified. | Remains unavailable. Do not transform a live ranking into historical uncensored throughput. |
| [Ramp AI Index](https://ramp.com/data/ai-index) | 128 observations, four series, Jan 2024–Aug 2026. Previous default replayed a bundled seed instead of fetching current publisher tables. | Live HTML yields 152 rows: adoption Jan 2023–Aug 2026 (44 months), three spend cohorts Sep 2023–Aug 2026 (36 each). 24 earlier observations. Parse actual publisher RSC data without execution; fail visibly if schema changes. Adoption is businesses with positive AI transactions, not spend/employee. Unknown publication timestamps remain null. |
| [Open Design](https://open-design.ai/llm-arena-for-design) | 1,183 observations, 169 series, Sep 11–17. All 169 series unchanged across seven checks of the same published evaluation. | Repeated fetches are not new benchmark runs. Render unchanged-observation summary, retain experimental status, and expire stale captures. No comparable historical rerun archive established. |
| [LiquidCompute](https://liquidcompute.com/) | Ten observations, five series, Sep 15–16. Current `/api/market/ticker` response verified separately. | Experimental vendor index with opaque methodology and no verified public historical endpoint. Do not invent older index levels or equate it with executable rent. |
| Interactive Brokers | No AI-cycle observations or integrated measurement. | Remove misleading page-level source outage/empty M1. Existing research workbench remains the place to inspect live portfolio/quotes. No broker or execution changes. |
| Unusual Whales | No AI-cycle observations or integrated measurement. | Same M1 treatment; no assertion that UW itself is unavailable. No synthetic dark-pool/OI history. |

## Every indicator and chart

| Indicator | Presentation / completeness decision |
| --- | --- |
| D1 activity | Default to derived total tokens, then rolling measures. Raw residual “other” alone is not total usage. Keep known source gaps and complete-window requirements. |
| D2 application breadth | Select current aggregate methodology/cohort, not obsolete per-app samples. Truncated rankings constrain interpretation. |
| D3 gateway composition | Default to current leading-lab token shares. Keep spend/request/model series distinct and disclose omitted series. |
| D4 production throughput | Unavailable: no verified Portkey export. |
| D5 enterprise adoption | Retain all four Ramp histories and explain business adoption versus USD-per-employee cohorts. Live fetch replaces seed default. |
| D6 design capability | Overall score selected first; constant published evaluation summarized, not depicted as repeated benchmark runs. Experimental, with stale status preserved. |
| C1 compute asks | Constant quotes summarized; provider/GPU/term series not combined. Partial provider/term coverage remains incomplete. |
| C2 marketplace supply | Zero matching offers summarized with narrow cohort semantics; no utilization inference. |
| C3 inference prices | Match latest fixed basket exactly. No mixing basket versions. Constant prices summarized. |
| C4 throughput price | Unavailable until uncensored comparable throughput exists. |
| C5 vendor index | Experimental, current published levels only; no backfilled synthetic history. |
| H1 hardware monetization | Reviewed NVIDIA/Dell facts added with distinct orders, revenue, backlog and fiscal definitions. FY2027 NVIDIA cohort is separate. |
| H2 working capital | SEC balances exist; full comparable DIO/DSO reconciliation remains incomplete. Do not label balances as turnover ratios. |
| F1 cash conversion | Select derived TTM ratios first; never substitute cash balance or annual cash-flow values for ratios. |
| F2 commitments | Unavailable: no reviewed comparable RPO/lease conversion cohort. |
| P1 load/weather | Default to EIA DOM daily average MW, not NOAA temperature. Weather is context; no isolated AI-power claim. Partial hours and reporting lag visible. |
| P2 energized capacity | Unavailable: no verified project-level energized-MW cohort. Announced capacity is not energized capacity. |
| M1 market context | Remove empty page module and disconnected source cards. Preserve backend identifier for compatibility and contextual ticker links. |

### Legacy inference-price disclosure

The collapsed legacy panel uses `llm_token_index`, outside the 18-indicator
registry. A direct read returned **zero rows** during this audit. Its v1 script
takes a median of 70% input / 30% output quoted prices and skips missing models;
therefore membership can change without a methodology-version change. Existing
copy explicitly identifies this limitation and does not claim scarcity or
actual spend. No historical rows were fabricated from today's catalog. The
fixed-cohort C3 measurement is the appropriate comparable successor, not a
retroactive rewrite of this empty legacy table.

## Presentation defects corrected

History keys include source, methodology and cohort, but compaction compared
them to bare metric identifiers. Matching failed and fallback selected long
alphabetical series instead of the displayed metric. Explicit history identities
now drive selection, preserving financial period-duration suffixes. Up to eight
series are retained, rather than silently dropping one of Ramp’s four or two of
the five cash-conversion ratios. The response remains bounded to 900,000 bytes.

Coverage now distinguishes stored observations from rendered chart points.
Sampling retains global extrema as well as distributed dates and endpoints;
it does not promise preservation of every local turning point. Source-wide
coverage is not presented as the selected series’ history. Constant values are
summaries with observation dates and counts, not artificial sparkline slopes.

## Backfill artifacts and reproducibility

Isolated evidence stores hold the production baseline and validated Ramp, SEC
and issuer additions. No unrelated trading or portfolio data is changed.
After isolated validation, appended 301 vintages to production: 152 live Ramp
rows (24 earlier periods), 131 pre-2009 SEC vintages (93 latest comparative
observations), and 18 reviewed hardware facts. Raw source responses were
archived before insertion; no existing observations were overwritten.
Initial production cache rebuilt at `2026-09-18T05:21:05.307043Z` (679,532 bytes):
SEC 5,175 latest observations starting Dec 31 2006; Ramp 152 starting Jan 2023;
issuer disclosures 14 starting Jan 26 2025. Four further reviewed quarters
extend hardware history to Jan 28 2024; a second cache rebuild follows.
UI changes still require PR merge
and deployment; the backfilled observations already persist independently.
The reviewed issuer JSON is checked in at
`scripts/ai_cycle/fixtures/issuer_disclosures_reviewed_20260918.json`; import with
the existing `--import-disclosures` collector option. Ramp is collected live by
default; `--import-ramp` remains an explicit offline import. SEC historical
backfill starts at 2006-12-31 and retains publisher vintages.

Remaining unavailable measures are deliberate evidence gaps, not complete data.
Backfill cannot recover private OpenRouter traffic, historical Vast fleet size,
unpublished benchmark runs, or verified energized projects from generic charts.

## Verification

Red tests reproduced history-identity selection, missing Ramp history,
extrema-erasing sampling and incomplete EIA-day handling before fixes. Green
verification and browser/CI evidence are recorded in the PR and task review.
