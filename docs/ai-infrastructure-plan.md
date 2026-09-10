# AI infrastructure: from activity to investable evidence

Design baseline, 7 September 2026. Reviewed [PR #126](https://github.com/joemccann/radon/pull/126) at `ce8d072e1cf3563e42b404d164ff75c676c38841`. This document preserves the approved research design and its initial access findings. Implementation and subsequent direct retrieval outcomes are recorded in [verification](ai-infrastructure-verification.md) and the [operations runbook](ai-infrastructure-operations.md). Source validity and predictive validity are separate release conditions.

## First principles

The economic chain is **useful work → willingness to pay → compute demand → delivered capacity → cash returns**. Equity prices capitalize expectations about that chain. Radon needs to measure changes at each link and then ask whether the option market and institutional flow already reflect them.

Three distinctions organize the workspace:

1. More tokens can reflect agent loops, longer reasoning, changed tokenizers or cheaper models. They do not establish more useful work or more revenue.
2. Cheaper inference can mean efficiency gains, competition or excess supply. A price cut alone has no universal bullish/bearish direction. Price and available capacity must be read against demand and delivery.
3. Negative free cash flow can fund profitable growth. Funding pressure needs a cash/lease/obligation bridge and future conversion evidence. Regional power load confirms physical activity with considerable confounding.

Retain the memo's separate measurement panes and its warning against a blended boom score. Replace its universal traffic-light thresholds with transparent hypotheses, explicit coverage requirements and shadow evaluation. None of these series has demonstrated trading edge in this review.

## What the PR needs corrected

| Memo assumption | Verified finding | Plan change |
|---|---|---|
| OpenRouter paid tokens plus inferred spend are an observable demand tape | Rankings expose total tokens for top 50 plus `other`, without billing or input/output/cache splits | Show public-token activity and visible non-free subset; unknown paid composition stays unknown. No “actual spend” from list prices |
| App history requires a community scrape | Official app-rankings now supports historical windows, returning aggregated app results | Use official day-window queries; label truncation and shared host lineage |
| GPU minimum price measures scarcity | Bundle, region, commitment and form factor change comparisons | Track matched offer cohorts and provider-balanced distributions; minimum only as a quote detail |
| NVIDIA's next-quarter guide is DC guidance | $108B is total-company guidance | Keep reported DC revenue separate from consolidated guidance |
| Cash ratios can mix quarterly and TTM periods | The memo mixes horizons and issuer-specific capex conventions | Uniform TTM primary ratio; separate quarter and lease-inclusive bridge |
| 13-week/90-day statistics are ready for all sources | GPU ledger currently reports 65 snapshots | Explicit warm-up state; no invented history or zero-filled gaps |
| Artificial Analysis price median is compute scarcity | Existing Radon implementation uses a 70/30 price blend, a changing available model basket, and guessed model IDs | Preserve v1 history; introduce validated membership and a versioned fixed-task/fixed-model successor |

Source checks: [OpenRouter](https://openrouter.ai/docs/cookbook/administration/data-api), [GPU ledger](https://gpurentalprices.com/data), [Lambda](https://lambda.ai/pricing), [NVIDIA release](https://investor.nvidia.com/news/press-release-details/2026/NVIDIA-Announces-Financial-Results-for-Second-Quarter-Fiscal-2027/default.aspx). Primary-statement links and subsequent access outcomes are in [source verification](ai-infrastructure-verification.md). Unaudited numerical claims in the original memo must not seed the database.

## Indicator specification

Priority P0 means first delivery; P1 means the next increment; P2 means experimental. Cadence is the proposed collection schedule, not a promise of provider latency. All growth comparisons use complete, compatible periods.

| ID / priority | Pull and source | Calculation / interpretation | Cadence and UI |
|---|---|---|---|
| D1 / P0 | [OpenRouter rankings-daily](https://openrouter.ai/docs/cookbook/administration/data-api): permaslug, total tokens, other, meta | Public routed tokens; visible non-free subset; other share. 7d mean and 28d sum / previous 28d sum − 1. Unknown long-tail paid fraction stays unknown | Daily completed UTC day; re-fetch trailing 7d for revisions. Demand lead chart with coverage underneath |
| D2 / P1 | Same official source, app-rankings queried one day at a time | Requests, tokens/request and top-app concentration within returned cohort. Matched-model growth as a diagnostic only; top-50 censoring is not zero use. Apps and models are the same host, never two independent confirmations | Daily; backfill within key quota. Demand drilldown, membership and truncation visible |
| D3 / P0 | [Vercel leaderboards export](https://vercel.com/docs/ai-gateway/leaderboards): model/lab shares by date and modality | Token, request and spend share in separate charts; spend share / token share is relative monetization, not dollar ASP. Changes in percentage points. Freeze model/lab classifications by vintage | Daily, 24h cache. Mix pane adjacent to D1; never scale shares into market totals |
| D4 / P2 | [Portkey rankings](https://portkey.ai/rankings/daily): publisher totals | Separate host activity. Spend remains publisher-labeled estimate until method/period confirmed. Potential corroboration only after repeated timestamped snapshots pass checks; cross-gateway traffic independence is not proven | Daily archive after parser/access validation. Experimental evidence drawer; excluded from default alerts |
| D6 / P2 | [OpenDesign Arena](https://open-design.ai/llm-arena-for-design/): public design-task leaderboard HTML | Per-model `avg_score`, `usd_per_artifact` and `avg_minutes` plus task-family scores (overall/web/mobile/desktop/dashboard/landing) when the page markup exposes them. Fetch time is `asof` unless Last-Modified is present. Missing or conflicting markup is unavailable; scores are never invented. LLM/model-quality only; never GPU scarcity | Snapshot collector. Demand pane `D6`; live collection by default, fail closed on transport/schema errors. Explicit offline HTML imports require a verified capture timestamp; the fixture is test evidence only |
| C1 / P0 | [Lambda](https://lambda.ai/pricing) direct list, [GPU Rental Prices](https://gpurentalprices.com/api/latest.json) secondary discovery and upstream links | H100 SXM/H200/B200 separate; match provider, region, VRAM, GPU count, interconnect, tenancy and term. Provider-balanced geometric price relatives; p25/median/p75 and eligible count. Spot separate | Daily. Compute price chart; explicit “asking price”. Aggregator collector currently unproven |
| C2 / P1 | [Vast search offers](https://docs.vast.ai/api-reference/search/search-offers) authenticated read-only search | Verified rentable offer count, unique machines and GPU count within fixed region/reliability filters. Median ask, dispersion, repeated availability. Disappearing offer is not a rented GPU. No fleet utilization percentage | Up to 2× daily after permission/entitlement proof. Availability below C1; own-rental fields excluded |
| C3 / P0 | Existing [Artificial Analysis model API](https://artificialanalysis.ai/data-api) plus first-party price references | Versioned fixed-model, fixed token-bundle cost. Preserve input/output/cache assumptions. Freeze cohort or suppress when required members missing; never let missing expensive models lower the index silently | Daily. Replace headline “compute premium” with “Inference price basket”; preserve v1 as legacy |
| C4 / P1 | Artificial Analysis benchmark definitions and licensed performance data | Cost per successful fixed task with accuracy, latency and context constraints. Track cheapest qualifying model separately from fixed-model price. Never divide price by arbitrary benchmark points. No qualifying model means unavailable | Daily snapshot/event refresh where entitled. Economics drilldown with benchmark version |
| H1 / P0 | NVIDIA IR/SEC; [Dell results](https://investors.delltechnologies.com/) | NVDA DC revenue separate from total-company guide and compute/network mix; Dell orders, recognized AI revenue and backlog as distinct observations. Reconcile backlog beginning + orders − recognized revenue + adjustments = ending only when definitions permit | Each earnings/filing event, daily new-filing check. Hardware timeline and explicit guidance revisions |
| H2 / P1 | NVDA/DELL/SMCI SEC filings; TSMC/Micron/SK hynix IR | DIO = average inventory / quarter COGS × actual period days; DSO = average AR / quarter revenue × days. Supply commitments and cancellation rights. HBM/packaging capacity commentary quoted with period/source, not fabricated units | Quarterly. Channel stress pane; inventory ramps require order/price confirmation |
| F1 / P0 | [SEC companyfacts](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) and issuer cash-flow exhibits: MSFT, AMZN, GOOGL, META, ORCL | TTM OCF / TTM cash PP&E; funding gap = cash PP&E − OCF. Aggregate = sum OCF / sum capex for synchronized periods, not mean issuer ratios. Separate financial leases and capex guidance definitions; capex is not AI-only | Quarterly with filing-event refresh. Finance table with fiscal dates, source and definition flags |
| F2 / P1 | Issuer IR/SEC: cloud growth, current RPO, backlog conversion, leases, debt and customer concentration | Track growth/rate vs dollars separately. Current RPO share and revenue conversion where disclosed; cash plus facilities versus dated obligations. Identify prepayments/customer-provided equipment and strategic counterparties; no invented AI revenue from cloud totals | Quarterly. Demand quality and funding drawer; counterparty overlap diagram when disclosed |
| P1 / P1 | [EIA hourly grid data](https://www.eia.gov/electricity/gridmonitor/), [PJM Data Miner](https://dataminer2.pjm.com/), [NOAA hourly weather](https://www.ncei.noaa.gov/products/land-based-station/integrated-surface-database) | Zonal/BA observed load less weather/calendar baseline, in MW with prediction interval. Use EIA region-sub-ba-data for parent=PJM, subba=DOM; a direct HTTP 200 returned hourly observations. EIA PJM aggregate is not DOM. Preserve MWh per one-hour interval, which is numerically equal to average MW only over that interval. Estimate nonlinear temperature, hour, weekday/holiday and season effects; test neighboring control regions. Residual is unexplained grid load, not AI MW | Hourly archive, weekly residual summary; daily QC. Physical pane; forecast/estimated/metered distinguished |
| P2 / P1 | Utility/PUC projects; [Vertiv IR](https://investors.vertiv.com/), [Eaton results](https://www.eaton.com/us/en-us/company/investor-relations/financial-reports/quarterly-earnings.html) | Separate requested, contracted, construction and energized MW by project; deduplicate phases/utility references. Orders/cancellations and B2B retain issuer horizon: Eaton rolling-12-month is not quarterly | Monthly project review, quarterly equipment updates. Delivery milestones, not queued-GW alarm |
| M1 / P0 | Existing IB price/option/portfolio feeds; [UW dark pool and OI](https://api.unusualwhales.com/docs) | Ticker relative return vs declared benchmark, matched-tenor IV vs realized vol, skew, earnings event pricing, flow and next-day OI changes. Expose already-priced evidence; do not infer buyer direction from unsigned dark-pool volume | Existing live subscriptions; OI on its actual release cadence. Ticker handoff and portfolio exposure rail |

Primary financial extraction starts with OCF and PP&E taxonomy tags, then issuer-specific extensions/exhibits. For Amazon validate productive-asset definitions rather than forcing a generic PP&E tag. Derive quarter flows from YTD differences and Q4 from FY minus nine months; do not add cumulative YTD facts. Use fiscal start/end, form, accession and filed time to select facts. Spot-check every first issuer extraction against its cash-flow statement before enabling derived values.

## Source verification and launch eligibility

**Evidence-backed endpoints:** Vercel ranged export returned HTTP 200, 3,123 rows across 341 dates (2025-10-01 through 2026-09-06). Daily metric share totals were approximately 100%. This validates availability and basic structure, not representativeness.

**Documented but not authenticated:** OpenRouter returned 401 without credentials; key-backed backfill remains to prove. Vast requires authenticated search. Artificial Analysis fields/rights depend on the package; free internal usage does not establish permission to redistribute in a public product. Existing configured credentials are not proof of successful retrieval in this review.

**Page verified, collector not established:** GPU latest JSON was readable through web retrieval, but direct Python request returned 403. The page reports 65 snapshots and a 2026-09-07 observation. Lambda's $3.99 H100 rate is per GPU in an eight-GPU bundle, not its one-GPU price. Portkey has no verified automation contract. Treat these as blocked collectors until a supported or permitted retrieval method works reliably.

**Physical evidence:** EIA DOM sub-BA returned HTTP 200 with a latest observed hour of 2026-09-06T04, not proof of a universal sub-hour lag. NOAA station probe returned HTTP 200 with no rows; weather coverage remains unverified and blocks residual promotion.

**Financial evidence:** primary issuer releases support selected corrections, not all memo numbers. Full reconciliation of the five-name financial panel is an implementation acceptance task. See [source verification](ai-infrastructure-verification.md) for primary statement links and the completed mapping review.

**Existing feeds:** IB/UW integration paths are established in Radon's code and their capabilities are documented; live account entitlements, quote freshness and OI response payloads were not exercised here. Preserve IB → UW → specialized official → Robinhood → Yahoo priority for equivalent market observations. Domain-specific nonmarket measurements use their originating publisher; IB cannot substitute for EIA load or an issuer cash-flow statement. Yahoo never becomes the scheduled primary.

## Measurement and alert contract

- Every series stores observation start/end, published_at, fetched_at, source URI, raw snapshot hash, unit/currency, methodology_version, cohort_version, lineage_group, coverage, license and verification state. First-seen history is not original publication history. Preserve revisions append-only; latest is a view.
- Distinguish observed, derived, estimated, stale, incomplete, unavailable and insufficient-history. A successful HTTP fetch never turns an old observation fresh. Show publication lag and sample age separately. Partial-day figures are previews excluded from triggers.
- Daily feeds: proposed stale threshold 48h after expected delivery; quarterly series remain “latest reported” until an expected release is missed. Trigger contracts are source-calendar aware. Missing data never becomes zero or healthy.
- Comparison defaults: 7d smoothing; current 28d vs previous 28d, nonannualized. 13-week change only with enough observations; electricity also YoY with a full matching history. No 2y z-score from 65 days. Robust percentiles require a declared minimum sample; insufficient observations stay unavailable.
- Proposed WATCH hypothesis: persistent comparable-host demand deterioration plus falling matched compute prices and rising available supply. Require four completed weekly evaluations and documented cohort coverage (initially ≥80% matched baseline coverage, plus ≥3 GPU providers); test these choices before deployment. Portkey cannot supply a confirming vote until source quality passes. Vercel mix cannot substitute for a volume decline.
- Split alternative explanations visibly: demand cooling; efficiency-led price decline; supply expansion; funding stress. A single green/red score hides those alternatives. “Confirmed deterioration” additionally requires a comparable guidance/order/conversion deterioration. Finance is context, never a compulsory late gate for an early research watch.
- If any required evidence is stale, censored or method-broken, show **Insufficient evidence** and suspend the affected alert. Clear only on documented fresh recovery using a preregistered reset rule; retain episode history.
- Validate with publication-time walk-forward analysis, source outages, model launches, cohort changes and multiple threshold choices. Report event counts, lead time, precision, false alerts and stability. Equity/option returns must include executable spreads and event overlap. Until enough independent episodes exist, label rules experimental and issue research notes only.

## Fit into Radon

Extend existing `/regime/llm` to **AI infrastructure**, retaining URL compatibility. `RegimePanel.tsx` already mounts `LlmTokenIndexCard.tsx`; `useLlmTokenIndex.ts` and `/api/llm-token-index` already bridge to a daily AA/Turso series. Avoid a second navigation destination and a second feed owner.

The main view has a compact evidence state, observation date and coverage row, then four selectable views: **Demand**, **Compute**, **Delivery**, **Finance**. Charts use their own units and time horizons. No dual-axis chart presenting the normalized index and its mechanically equivalent raw price as independent signals. The source drawer shows formulas, first/latest vintage, cohort members, exclusions, access and raw evidence. Trend color does not imply universal good/bad economics.

Add one small AI infrastructure handoff in `components/dashboard/ClearOverview.tsx`'s existing research rail. Show state and affected held/watchlist names only when exposure mapping is available; never fabricate current holdings. Preserve account overview priority. On mobile, this becomes a single research row and the detail view stacks charts and source evidence; keep existing four primary destinations plus complete menu.

Ticker research gains a dated mapping: compute supply (NVDA/AMD), cloud monetization (MSFT/AMZN/GOOGL/ORCL), servers (DELL/SMCI), power equipment (VRT/ETN), with evidence and uncertainty. These are proposed classifications, not verified current revenue betas. Portfolio sensitivity uses IB positions and current Greeks; full repricing is required for stress scenarios. Do not multiply token growth by stock delta and call it P&L.

The handoff remains **signal → structure → Kelly math → decision** through Radon's existing evaluation and OrderRiskGate. AI-cycle context cannot satisfy the specific dark-pool/OTC edge gate, supply a Kelly probability, or bypass the 2.5% bankroll cap.

## Delivery dependency graph

| Task | depends_on | Deliverable and acceptance |
|---|---|---|
| T1 | [] | Source registry, license/use classification, entitlement probes, exact schemas and golden snapshots. Quarantine memo numbers. Each enabled source has a reproducible successful fetch |
| T2 | [T1] | Append-only observations/revisions, cohort IDs, provenance and bounded daily/filing collectors; bulk backfill with checkpoints, no user-request scraping |
| T3 | [T2] | D1/D3/C1/C3/H1/F1 transforms and v1 price-index migration. Reconcile financial examples; test token censoring, missing models, mixed GPU bundles and period arithmetic |
| T4 | [T3] | AI infrastructure extension on `/regime/llm`, source drawer, mobile layout, research-rail handoff and existing ticker links |
| T5 | [T2,T3] | Shadow study; expanding-window evaluation using available-at-time observations. No claimed edge from reconstructed or too-short histories |
| T6 | [T4,T5] | Release measurement UI after quality checks; alerts remain experimental until validation supports them. Add P1 sources only after access/method checks |

Graph: **T1 → T2 → T3 → T4 → T6**, **T2 → T5**, **T3 → T5 → T6**. Suggested delivery: week 1 sources/provenance, week 2 P0 transforms, week 3 UI, week 4+ shadow monitoring. Backfill/access can extend this; historical validity cannot be manufactured to meet a date.

Implementation paths (proposed): add `scripts/ai_cycle/` collectors/transforms with shared bounded transport and Turso writer; expose a thin FastAPI read route and `/api/ai-cycle` Next bridge. Keep legacy `/api/llm-token-index` readable. New routes need capability catalog pins, no-store and dynamic contracts, assistant loader registration and route access checks. New UI consumes aggregate snapshots; realtime market data remains owned by RealtimePricesProvider.

Verification before shipping: focused Python/Vitest red-green tests; fiscal/YTD and missing-data fixtures; provider outage and schema-change tests; Playwright route/mobile/source-drawer cases and screenshots in both themes; full project suites before a code-bearing commit. Software test and build results belong in the release verification record; they do not establish source representativeness or predictive validity.

## Defer or exclude

Do not purchase sources merely to populate a tile. Defer Similarweb/Sensor Tower, credit/OAS, SemiAnalysis/TrendForce and paid AA history until a specific missing decision input justifies cost and rights. Exclude global-token counters, app visits as compute utilization, HF downloads, preference rankings, raw interconnect queues and FINRA short volume as short interest. Credit work, if added, needs issue/maturity-matched spreads and financing terms, not ETF prices mislabeled as borrower credit.

## Review outcome

The plan improves measurement before adding indicators: observed use, comparable compute cost and availability, delivery/conversion, funding, then market pricing. The implementation handoff includes actual collected-data UI captures and explicitly distinguishes synthetic outage scenarios. Evidence links remain visible; unproven collectors are explicitly labeled.
