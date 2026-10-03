# Ornn Data and the AI Investment Cycle

## Recommendation

**Request approval for a free 72-hour Premium research grant. The trial requires Ornn to approve the grant; it is not immediately available on demand. If approved, evaluate before paying. If approval is delayed or denied, continue with the free data rather than subscribe merely to bypass the wait. Consider one $500 month only when the specific data and usage-rights questions below are answered; do not commit to an ongoing subscription or buy Full yet.** Ornn is most compelling as a specialist view into compute rental economics, supplemented by workload-cost decomposition. The token-price charts alone do not justify the fee given Radon's existing data.

The case for paying is specific: better evidence about the price at which GPU capacity clears, how much tracked capacity is rented, and whether falling inference prices reflect efficiency, changing model selection, or deteriorating monetization. The case against paying is substantial overlap with public telemetry, short or interrupted histories, unresolved methodology language, and restrictive rights for derived indicators and product distribution.

Premium costs $500/month, or $6,000 over twelve months. An approved 72-hour research grant provides the Premium trial surface. Full is custom-priced. These terms match the supplied pricing screenshot and Ornn's current public pricing page. [1](https://data.ornn.com/pricing)

**Assessment:** high confidence in the published plan distinction and the existence of meaningful overlap with Radon; moderate confidence that Ornn could improve research efficiency and compute-market context; insufficient evidence that it predicts equity returns. Paid datasets were not accessed, an authenticated integration was not tested, and no historical trading edge was estimated.

This assessment uses public information accessed September 9, 2026, and the current Radon working tree. Repository observations describe implemented capabilities, including work in progress, rather than verified production feed health. No subscription or vendor outreach was undertaken.

## What the subscription actually includes

| Plan | Coverage | Practical fit |
|---|---|---|
| Index, free | Five GPU series with three months of daily history; four token labs with one month; public catalogs and headlines | Initial comparison against existing indicators |
| Premium, $500/month | Six GPU series; eleven token labs; available daily history; utilization, memory, workload, coding, frontier and other analytics; daily API and paid MCP tools; current forward marks | Appropriate evaluation tier for a daily or weekly investment process |
| Full, custom | Premium plus hourly compute, hourly power operations, detailed site records, exports and organization seats | Only when a demonstrated use case needs these additions |

Pricing establishes the broad entitlements. [1](https://data.ornn.com/pricing) The detailed access guide distinguishes several less obvious limits: Premium requests for hourly granularity can return daily data; dedicated hourly endpoints reject Premium access; forward history is unavailable through the customer API even on Full. The sixth GPU is RTX PRO 6000 WS, not a new flagship accelerator automatically implied by “all six.” In-app CSV/XLSX export is Full-only, although the public Index page has a free-window CSV exception. Premium's JSON API is still usable for entitled daily queries. Cancellation currently requires writing to Ornn. [2](https://data.ornn.com/docs/access-tiers)

The important purchase distinction is **coverage versus access**. Paying removes entitlement windows; it does not create observations before a dataset existed, fill historical gaps, make daily data intraday, or grant unrestricted reuse.

## Dataset-by-dataset assessment

### GPU rental prices: the strongest potential differentiation

OCPI measures active on-demand GPU rentals using a GPU-unit-weighted mean with extreme observations capped at percentile bounds. It excludes reserved and long-term contracts. Contributor identities and the precise percentile parameter are not public. The methodology allows carry-forward during disruption, suspension, and corrections for material errors or falsification. Its governance text describes an oversight committee still being established, rather than documenting an already constituted committee with named members. [3](https://data.ornn.com/methodology)

This is potentially better evidence of marginal compute demand than advertised rental rates. A seller can leave a high asking price online without finding a customer; an occupied rental at a paid rate establishes a more meaningful economic observation. That distinction is the principal reason to investigate Ornn.

However, the published aggregate is not Radon's matched-provider price basket. Changes in region, provider quality, cluster configuration or customer mix can change an average even with the GPU type held constant. A broad contributor network helps only if the sample remains sufficiently representative through time. Request concentration statistics and comparable-panel results before assigning more confidence to the series.

Ornn's FAQ reports roughly 150 contributors per index and about 1,000 observed transactions daily, predominantly US volume. It describes H100 hourly history from January 2025, with earlier sparse observations, and B200 history from November 2025. These are vendor statements, not independently audited counts or a guarantee of usable history in every field. [4](https://data.ornn.com/faq)

### Utilization: valuable if its denominator holds up

Ornn defines utilization as the rented share of tracked capacity. That is rental occupancy, not GPU processor busy time or utilization of the world's installed accelerator fleet. [5](https://data.ornn.com/docs/analytics)

This could fill a real gap: Radon's current Vast observations measure available offers and machines, not total fleet occupancy. The purchasing question is whether Ornn measures a stable, observable supply denominator. An apparent occupancy increase could reflect stronger demand, but also providers removing idle inventory or changing their contribution coverage.

Ask for rented units, available units, provider counts, panel changes, and treatment of offline, reserved and maintenance capacity. Check whether price and occupancy come from the same underlying contributors. They can reinforce interpretation, but should not automatically count as independent confirmations.

### Forward curves: useful contract context, limited historical evidence

The forward endpoint exposes current manually published marks, with provenance and timestamps. Missing marks remain missing; the API does not supply historical mark vintages. [6](https://data.ornn.com/docs/api-reference/forward/get-the-forward-curve)

That makes the curve useful for interrogating term economics, but it should not be treated as a continuously traded futures curve with demonstrated depth. Ask whether a mark is an executable quote, an executed contract, a broker indication or an analyst assessment; how much business supports it; and which delivery, prepayment and reliability terms apply.

A falling term structure may reflect expected hardware obsolescence, commitment discounts, financing terms or expectations of greater supply. It does not uniquely forecast weaker AI demand. Begin by comparing contracts with equivalent specifications, not simply by calculating the steepest displayed slope.

### Token prices: informative composition, unresolved price provenance

OTPI aggregates token prices by lab using consumption weights. Its technical documentation describes posted prices weighted by paid activity, while the FAQ describes transacted prices including cache effects. This is a substantive ambiguity for anyone estimating revenue. Until reconciled with a worked example, label OTPI a vendor-calculated blended inference-price indicator rather than audited realized revenue per token. [7](https://data.ornn.com/docs/token-price-index), [4](https://data.ornn.com/faq)

The earliest four lab series begin July 4, 2025. April 4 through June 3, 2026 is an unrecoverable gap. Pre-gap history is archival reconstruction; live daily settlement starts June 4. Later labs begin in mid-June. These boundaries materially constrain historical comparisons. [7](https://data.ornn.com/docs/token-price-index)

A lab-level blend answers a different question from a fixed-model price comparison. It can rise because customers choose more expensive models, even if no model changes price. That is economically interesting, but it is not inflation for a constant unit of capability.

### Token volume: useful, but not independent marketwide demand

Ornn identifies OpenRouter as an upstream venue for token-volume settlement. Its dashboard covers eleven labs, while the REST volume endpoint covers Anthropic, OpenAI and Google. Dashboard selections can change the comparison denominator and baseline. The documentation includes designated open-weight labs while excluding free variants and certain open-weight releases from closed labs; the taxonomy needs to be preserved. [8](https://data.ornn.com/docs/token-volume-index)

The analytical limit is platform selection. Rising volume can reflect OpenRouter gaining share rather than aggregate AI demand accelerating. Public routing is not the same as direct enterprise API business, subscriptions, self-hosted inference or internal hyperscaler use. Splitting open and closed labs does not remove that selection effect. Radon already uses OpenRouter, so this is overlapping evidence unless Ornn demonstrates additional independent coverage.

### Workload economics: the most interesting LLM-specific addition

The workload dataset combines average spend per request, tokens per request, and an index intended to remove model-mix effects. It uses a decomposition across trailing seven-day windows. The data covers three closed labs, starts June 11, 2026, and documents a missing June 30 settlement. Ornn explicitly distinguishes venue-reported settlement from audited payments. [9](https://data.ornn.com/docs/workload-cost-index)

This helps answer a better investment question than “are tokens cheaper?”: **is the cost of actually using AI falling, or are more demanding workloads absorbing the savings?** Treat the mix-adjusted measure carefully. Holding model composition more constant does not fix task difficulty, answer quality, context length, retries or successful completion rates.

The workload docs describe approximately 36-hour lag, while the FAQ describes settlement about twelve hours after a UTC day's close. Those descriptions could use different time anchors; obtain explicit observation-window and publication timestamps before calling them contradictory or building event alignment. [9](https://data.ornn.com/docs/workload-cost-index), [4](https://data.ornn.com/faq)

### Coding activity: adoption proxy with a visible sample boundary

Coding metrics attribute public GitHub activity to tools including Claude Code, Codex, Copilot, Cursor and Devin. Attribution uses observable signatures such as branch conventions and, for Claude commit counts, co-author metadata. Private development and IDE activity are outside this panel; merge outcomes can change after the observation date. [10](https://data.ornn.com/docs/llm-coding-index)

Use this as a check on public coding-agent adoption. Do not equate PR counts with paid seats, retained users, useful productivity or model quality. Tool changes that add or remove attribution signatures can create artificial growth. Compare cohorts at equal age when examining merge rates.

### Memory: general commodity context, not an HBM feed

The public catalog contains fourteen commodity DRAM, flash and module products, including DDR3/4/5 categories. It does not list HBM, HBM3E, HBM4 or GDDR. The memory documentation describes weekday component spot prices. [11](https://api.ornnai.com/api/memory-types), [12](https://data.ornn.com/docs/memory-index)

This can inform a broader memory-cycle thesis, but it cannot directly settle whether accelerator HBM is scarce, what a supplier earns on HBM contracts, or when packaging constraints ease. For an AI-memory investment thesis, direct HBM allocation, capacity, contract and supplier-disclosure evidence remains necessary.

### Power and datacenters: useful context with different frequencies

Power coverage combines daily wholesale series, including ICE and ERCOT, with additional hourly EIA operations data on Full. Some ICE source publications can lag by weeks. These are regional market observations, not individual datacenter power agreements or bills. [13](https://data.ornn.com/docs/power-markets)

Full's site dataset contains curated locations, capacity and milestone information, including estimates and provenance. Its aggregation documentation warns that multi-chip sites can appear in multiple GPU categories. [14](https://data.ornn.com/docs/datacenter-map), [15](https://data.ornn.com/docs/api-reference/neo-cloud-sites/aggregate-sites-by-country-or-gpu-type)

Its value depends on distinguishing announced, contracted, constructed, energized and revenue-producing capacity. Those categories should remain separate. A sum of every site announcement can substantially overstate deliverable near-term supply, and a sum across overlapping GPU groups can double-count the same site.

### Model Frontier and Compute Buyers: supplementary research

Model Frontier combines capability benchmarks with measured and OTPI-rebased costs. Historical monthly pricing is narrower than the full current model view. Release dates combined with current scores do not establish point-in-time historical capability or cost; dated benchmark observations and price vintages are necessary. Compute Buyers compiles funded AI businesses and workload classifications from filings and announcements. [16](https://data.ornn.com/docs/api-reference/model-frontier/get-the-model-frontier-dataset), [17](https://data.ornn.com/docs/api-reference/compute-buyers/list-tracked-compute-buyers)

Both can help organize research. Neither proves profitable adoption: a benchmark score is not production ROI, and financing raised is not compute consumed or recurring revenue earned.

## Fit with Radon's existing indicators

The working tree contains sixteen AI-cycle indicators, plus a legacy inference-price basket. That existing coverage materially raises the bar for purchasing a second broad dashboard. The following mapping is based on source inspection, not a claim that every feed is currently operational. [R1](../../scripts/ai_cycle/registry.py)

| Existing Radon surface | What it already captures | Incremental Ornn case |
|---|---|---|
| D1–D3 demand | OpenRouter tokens/apps; Vercel model and lab shares | Better maintained settlement and workload breakdown, but shared upstream data is not an independent signal |
| D4–D5 commercial use | Portkey gated pending licensed access; curated Ramp paid-spend evidence | Does not replace a representative enterprise-spend panel |
| C1 GPU prices | H100/H200/B200 asks, with strict matched-comparison requirements | Transaction-based rental observations and deeper history |
| C2 supply | Vast available offers, machines and GPUs | Measured rental occupancy, if the panel and denominator are credible |
| C3 inference prices | Artificial Analysis fixed cohort, one million input plus one million output tokens | Consumption-weighted price and workload mix, as a separate series |
| C4 useful-task economics | Deliberately gated pending comparable quality, latency and context evidence | Frontier/workload inputs may help, but do not alone satisfy fixed-task comparability |
| H/F issuer fundamentals | SEC and reviewed delivery, inventory, cashflow and financing evidence | Context for hypotheses; no replacement for issuer accounting |
| P1–P2 power | EIA/NOAA and reviewed physical milestones | Broader regional context; possibly useful Full site records |
| M1 market confirmation | Market-price context and existing trading workflow | No substitute for IB/UW prices, options, flow and OI |

Radon's current C1 methodology matches provider, region, GPU specification, tenancy and term, requires sufficient common coverage, and separates hardware generations. An Ornn aggregate should remain a distinct measurement, not silently replace that basket. [R2](../../scripts/ai_cycle/transforms.py)

The legacy token basket uses a median of 70% input and 30% output list prices across available models, normalized to its first observation. The newer C3 uses a stable model cohort and a fixed input/output bundle. OTPI's consumption weights answer a different question; movement between them can be informative, but calling either one the other's improved version would obscure the difference. [R3](../../scripts/llm_token_index.py), [R4](../../scripts/ai_cycle/collectors.py)

Operational documentation shows short GPU-ask history and current-only AA/Vast snapshots, while several stronger measures remain intentionally unavailable. That creates room for a paid source to improve completeness, provided its dates and underlying definitions are better. [R5](../ai-infrastructure-operations.md)

## How this helps understand the AI trade

### Separate adoption, spending and compute consumption

The following are analytical identities and illustrative scenarios, not claims about current Ornn observations.

For an exactly matched universe:

**Inference spending = paid token quantity × effective dollars per token.**

If effective price falls 40% and volume rises 60%, spending changes by `0.60 × 1.60 = 0.96`, or **−4%**. Volume must rise more than **66.7%** merely to offset that price decline. Huge token growth can coexist with flat or falling revenue.

Likewise, **required GPU-hours ≈ workload volume ÷ productive throughput per GPU-hour**, subject to workload and service-level comparability. If standardized throughput doubles while volume doubles, compute hours can remain flat. More tokens do not mechanically imply more accelerators or more electricity.

Finally, **cost per successful task** must include failed attempts, retries, tool calls and the service-quality constraint. A cheaper token paired with a much longer reasoning trace may produce a more expensive task. Workload cost and frontier data are valuable because they can help investigate these differences; they cannot eliminate the need for comparable tasks.

### Read price and occupancy together

| Observations, sustained across comparable samples | Candidate interpretation | Evidence needed before acting |
|---|---|---|
| GPU rents rise; occupancy rises; standardized workloads rise | Demand pressing against available supply | Stable contributor panel, broader deployment evidence, issuer ability to monetize |
| GPU rents fall; available capacity rises; paid demand weakens | Possible excess supply or demand deterioration | Exclude new-provider entry, outages, seasonal effects and hardware migration |
| GPU rents fall; workloads rise; task costs fall | Efficiency-driven expansion or supply catching up | Revenue, useful-task output, new-capacity additions and margin evidence |
| Older GPUs weaken while newer GPUs remain tight | Generation substitution rather than broad AI contraction | Matched workloads, performance per dollar, fleet age and renewal terms |
| Token prices fall; tokens/request rise; request cost stays high | Heavier agentic workloads absorbing unit savings | Task success, customer willingness to pay and application-level margins |

These are competing hypotheses, not deterministic trading rules. The best use of Ornn is to discriminate among them and identify what additional evidence would falsify the preferred explanation.

### Map the economics to the business, not just the theme

| Business exposure | Where Ornn could help | Main missing bridge |
|---|---|---|
| Accelerator suppliers, such as NVIDIA and AMD | Economics and attractiveness of adding compute capacity; old/new generation divergence | Shipments, customer capex, inventory, product mix and competitive substitution |
| Neocloud operators, such as CoreWeave | Marginal rental demand, renewal economics and utilization scenarios | Contract protection, financing, delivery timing, counterparty and residual-value risk |
| Hyperscalers and model platforms | Competition between inference selling prices and compute input costs | Own-fleet economics, enterprise discounts, internal traffic and non-AI business mix |
| Memory suppliers | Broad component-cycle context | HBM-specific prices, contract mix, capacity and qualification |
| Power, cooling and electrical infrastructure | Location and timing of buildout pressure | Energized demand, PPAs, backlog conversion, project delays and regulatory economics |
| AI applications and software | Workload intensity, task cost and the distribution of efficiency gains | Retention, monetization, pricing power, support cost and competitive price pass-through |

The table is an exposure framework, not a list of recommended positions. A favorable industry trend can already be reflected in valuation or accrue to a different part of the supply chain.

CoreWeave illustrates why the bridge matters. Its August 11, 2026 results reported about $104 billion of backlog, 1.5 GW active power and 3.7 GW contracted power. Backlog recognition remains subject to delivery and service availability. These measures describe different stages of economic realization, and none equals today's spot GPU-rental market. [18](https://investors.coreweave.com/news/news-details/2026/CoreWeave-Reports-Strong-Second-Quarter-2026-Results/default.aspx)

For a rental operator, a simplified gross-revenue measure is **rental dollars per GPU-hour × billed occupancy × installed GPUs × hours**. It is not margin. Power, networking, colocation, maintenance, depreciation, financing and residual value still matter. For a model provider, convert GPU consumption into dollars per request before comparing it with inference revenue; OTPI and OCPI have different units and cannot be subtracted directly.

Ornn can improve thesis monitoring and downside scenario design. It does not supply a calibrated probability of a stock move, options convexity, institutional positioning or entry timing. Radon's existing evaluation gates should continue to own trade selection and sizing.

## Advantages, disadvantages and alternatives

**Advantages:** potentially better observation of paid GPU rentals; a coherent view across compute inputs and inference outputs; convenient daily history and APIs; useful workload decomposition; and a common vocabulary for monitoring the same thesis over time. A maintained specialist dataset can be worth paying for even without exclusive alpha if it consistently saves analyst time.

**Disadvantages:** substantial dependence on sampled venues; incomplete coverage of direct enterprise and hyperscaler activity; short and interrupted histories; possible composition shifts; heterogeneous definitions across product families; uncertainty about OTPI price construction; and licensing that complicates reuse in a broader analytics product. Several attractive extras are context rather than unique transaction data.

| Alternative or complement | What it adds | Purchasing implication |
|---|---|---|
| OpenRouter Data API | Public aggregate model/app activity, classifications and other datasets; ordinary valid API key; CC BY 4.0 terms for published aggregates | Strong baseline before paying for token dashboards [19](https://openrouter.ai/docs/cookbook/administration/data-api) |
| OpenRouter session-cost data | Median spend by application/harness, model and session characteristics | Useful partial comparison for workload economics; not the same estimator [20](https://openrouter.ai/docs/api/api-reference/datasets/cost-per-session-by-harness-and-model) |
| Artificial Analysis | Capability, token pricing and measured performance; free internal API tier with 100 requests/day | Complement actual demand with quality and efficiency evidence [21](https://artificialanalysis.ai/data-api) |
| SemiAnalysis GPU index | Public hourly H100/A100/B200 composites using venue prices, with H100 contract-survey inputs | Cross-check compute trends while preserving its different methodology [22](https://gpu-index.semianalysis.com/) |
| SemiAnalysis institutional models | Infrastructure TCO and token-economics scenarios | Consider if the main need is issuer economics rather than another feed; separately sold institutional products [23](https://semianalysis.com/ai-cloud-tco-model/), [24](https://semianalysis.com/tokenomics-model/) |
| Shadeform | GPU offers with hardware, interconnect, region and availability fields | Useful specification-matched quote baseline, not transaction or fleet-utilization truth [25](https://docs.shadeform.ai/api-reference/instances/instances-types) |
| EIA and company disclosures | Grid operations, actual investment, revenue and cash generation | Preserve as independent anchors [26](https://www.eia.gov/opendata/), [18](https://investors.coreweave.com/news/news-details/2026/CoreWeave-Reports-Strong-Second-Quarter-2026-Results/default.aspx) |

OpenRouter's public aggregates exclude private activity, and tokenizers differ across providers. Its daily history starts January 2025. Some category-filtered results are sampled weekly estimates whose final bucket can extend beyond a requested end date. These limits matter both for direct use and for any downstream vendor relying on similar telemetry. [19](https://openrouter.ai/docs/cookbook/administration/data-api), [27](https://openrouter.ai/docs/api/api-reference/datasets/daily-token-totals-for-top-50-models)

SemiAnalysis's composite and OCPI should be compared as different estimators, not expected to agree in level. The former weights providers and incorporates quoted/survey observations; the latter describes executed on-demand rentals. Disagreement can reveal segmentation rather than a bad feed. [22](https://gpu-index.semianalysis.com/), [3](https://data.ornn.com/methodology)

## Licensing and LLM integration

The August 19, 2026 Premium addendum is more permissive than the general May terms: it expressly allows internal research, monitoring and own trading within the named organization. It still restricts derived indices, competing data/analytics products and outside distribution, including transformed data. Full is not automatically a redistribution or index license. On cancellation, deletion/return obligations apply except for specified legal retention. [28](https://data.ornn.com/premium-data-use), [29](https://data.ornn.com/legal/terms-of-use.pdf)

**Practical recommendation:** private review of attributed Ornn measurements has a clear permitted-use basis. Obtain written scope for feeding them into a Radon composite or score, retaining research vintages, supplying them to an external LLM processor, or showing resulting content to other users. Do not assume aggregation removes the restriction. This is a procurement interpretation of published terms; the agreement should name the actual intended workflow.

The technical LLM connection is straightforward in principle: Ornn offers a read-only MCP server at `https://data.ornn.com/mcp`, with account authentication and tier-based access. Some larger datasets, including Model Frontier and Compute Buyers, are REST-only; power also lacks an MCP tool. [30](https://data.ornn.com/docs/mcp-server)

For Radon, the better research pattern would be structured observations plus deterministic calculations, followed by LLM interpretation. Each response should carry the source, observation period, publication time, unit, sample coverage and caveats. The assistant should explain what changed and what alternative explanations remain, rather than treating the vendor's index name as evidence of marketwide truth.

Useful prompts would include: “Did comparable GPU rental prices and occupancy weaken together?”; “How much of lower request cost is model selection rather than same-model change?”; and “Which issuer disclosures contradict the inference-demand narrative?” Those questions encourage evidence reconciliation rather than a single bullish/bearish AI score.

## A purchase test that can actually decide renewal

### Trial: determine whether the product is usable

First request the research grant and obtain Ornn’s approval. Prepare the evaluation questions before activating the approved trial, then use the 72 hours to inspect samples and establish definitions. Check actual series starts, gaps, publication lags and permitted history access. Inspect the paid workload fields and whether utilization includes denominator or coverage diagnostics. Verify the response granularity rather than trusting request parameters. Avoid treating a successful HTTP response as evidence that a requested date range was fully delivered.

The trial should answer three questions: Is the distinctive information present? Can it be interpreted defensibly? Is the intended use permitted? A three-day window cannot establish uptime over a month or trading predictiveness.

### One month: test incremental research value

Keep Ornn alongside the current sources. Before observing results, specify three hypotheses: whether paid GPU rentals add information beyond asks; whether occupancy improves supply interpretation; and whether workload decomposition changes conclusions about inference monetization. Log cases where Ornn changes the conclusion, highlights conflicting evidence, or materially reduces research time.

Renew only if the month produces a repeatable benefit: reliable entitled data, no unresolved critical interpretation issue, and either meaningful time savings or documented insight absent from the existing stack. Several independent research cases are preferable to one dramatic anecdote. A quiet month is also informative if the feed adds no decision value.

Use separate labels for operational utility and predictive performance. Thirty days can establish the former reasonably well; it cannot establish durable alpha. Longer validation should compare point-in-time observations against a simple baseline, account for earnings and market factors, and reserve subsequent periods for genuinely out-of-sample assessment.

### Price discipline

At $500/month, saving five hours of work valued at $100/hour covers the subscription cost; saving two hours at $250/hour does the same. These are opportunity-cost illustrations, not investment-return estimates.

The annual $6,000 fee is 60 basis points of a hypothetical $1 million portfolio or 12 basis points of $5 million, before integration costs. A portfolio's size does not make the data predictive. It merely changes how consequential the research expense is.

Do not pay for Full until daily data has established its value and a specific additional use case requires intraday compute, site records or organization access. For a weekly AI-cycle process, hourly observations can add cost and noise without improving the decision.

## Integration and validation requirements

These are proposed requirements, not implemented changes. Any derived indicator work is conditional on the required license scope.

1. Keep separate series for GPU rental prices, rental occupancy, forward marks, token prices, token quantities and workload costs. Do not blend unlike units into an unexplained composite.
2. Preserve the observation period, source publication time, first-seen time, methodology version, original units, actual returned range and coverage flags. Distinguish observed, estimated, reconstructed and carried-forward values.
3. Record shared upstream lineage. Ornn token volume and Radon OpenRouter demand may describe the same underlying activity; duplicated packaging is not independent confirmation.
4. Suppress unsupported historical joins. Keep the OTPI gap visible, segment archival reconstruction from live settlement, and do not manufacture forward-mark history from today's curve.
5. Align calendars explicitly: compute settlement, UTC token days, weekday memory prints, weekly coding cohorts and delayed power observations do not share one information clock.
6. Keep issuer fundamentals and financial-market evidence independent. A compute-market observation should lead to a falsifiable company-level hypothesis, then enter the existing research and trading process.

Radon already defines information availability conservatively using fetch/publication timestamps, and its shadow logic requires observations collected in time. A historical purchase today must not be backdated into that live record. A separately labeled historical study can use supplied vintages if the evidence supports them. [R6](../../scripts/ai_cycle/model.py), [R7](../../scripts/ai_cycle/snapshot.py), [R8](../../scripts/ai_cycle/shadow.py)

## Questions for Ornn before paying beyond a trial

| Question | Why it changes the purchase decision |
|---|---|
| Provide one versioned OTPI example showing prices, token classes, requests, caching and weights. Which fields are realized, posted or inferred? | Determines whether revenue-like interpretation is justified |
| What are contributor concentration, regional coverage and comparable-panel history for each GPU? | Tests whether the apparent signal is composition drift |
| What exactly enters rented and available capacity? Are underlying denominator counts and changes provided? | Determines whether occupancy is usable supply/demand evidence |
| Can you provide a field-level start-date, gap, reconstruction and revision manifest? | Establishes honest backtest scope |
| Are forward marks executed, quoted or assessed, and can original historical vintages be licensed? | Determines whether term-structure research is feasible |
| Which workload request counts are directly observed, and what percentage of activity has complete price coverage? | Tests the strength of the most interesting paid LLM dataset |
| What written rights cover private Radon transformations, LLM processing, archived vintages and any shared output? | Separates a $500 personal research tool from a product-data license |
| How do cancellation, data deletion, support and delayed-source handling work in practice? | Establishes full operating cost and continuity |

**Purchase conclusion:** Ornn deserves a serious trial because transaction-oriented compute data and workload decomposition could improve Radon's AI-cycle understanding. It does not yet deserve an automatic recurring budget on the strength of its feature list. Pay for demonstrated incremental evidence and saved work, with the intended-use rights agreed; retain the free and existing sources as independent checks.

## Sources

Public sources were accessed September 9, 2026. Pages are undated unless a publication or effective date is specified. Vendor descriptions are attributed claims; dataset accuracy and paid-service performance were not independently audited.

1. Ornn. [Plans and pricing](https://data.ornn.com/pricing). Supplied screenshot independently matches the three displayed tiers.
2. Ornn. [Access tiers](https://data.ornn.com/docs/access-tiers). API, history, cadence, export and cancellation details.
3. Ornn. [OCPI methodology](https://data.ornn.com/methodology). Initial publication listed July 24, 2026.
4. Ornn. [FAQ](https://data.ornn.com/faq). Contributor and history claims; token methodology; settlement and licensing.
5. Ornn. [Analytics](https://data.ornn.com/docs/analytics). Volatility and rental-utilization definitions.
6. Ornn. [Published forward-curve endpoint](https://data.ornn.com/docs/api-reference/forward/get-the-forward-curve). Current marks, provenance and missing history.
7. Ornn. [Token Price Index](https://data.ornn.com/docs/token-price-index). Construction, coverage and archival/live boundary.
8. Ornn. [Token Volume Index](https://data.ornn.com/docs/token-volume-index). OpenRouter source, lab classes, dashboard/API differences.
9. Ornn. [Workload Cost Index](https://data.ornn.com/docs/workload-cost-index). Decomposition, history, missing day and settlement status.
10. Ornn. [LLM coding analytics](https://data.ornn.com/docs/llm-coding-index). Attribution and sample limitations.
11. Ornn. [Public memory catalog](https://api.ornnai.com/api/memory-types). Fourteen listed products; no HBM series.
12. Ornn. [Memory Price Index](https://data.ornn.com/docs/memory-index). Product categories and publication cadence.
13. Ornn. [Power Markets](https://data.ornn.com/docs/power-markets). Upstream data and lag.
14. Ornn. [Datacenter sites](https://data.ornn.com/docs/datacenter-map). Curated site fields and estimates.
15. Ornn. [Site aggregation endpoint](https://data.ornn.com/docs/api-reference/neo-cloud-sites/aggregate-sites-by-country-or-gpu-type). Multiple GPU categories and double-counting risk.
16. Ornn. [Model Frontier endpoint](https://data.ornn.com/docs/api-reference/model-frontier/get-the-model-frontier-dataset). Benchmark and cost provenance.
17. Ornn. [Compute Buyers endpoint](https://data.ornn.com/docs/api-reference/compute-buyers/list-tracked-compute-buyers). Funding and workload classification.
18. CoreWeave. [Second-quarter 2026 results](https://investors.coreweave.com/news/news-details/2026/CoreWeave-Reports-Strong-Second-Quarter-2026-Results/default.aspx). August 11, 2026.
19. OpenRouter. [Data API](https://openrouter.ai/docs/cookbook/administration/data-api). Public aggregate access, limits and licensing.
20. OpenRouter. [Session costs](https://openrouter.ai/docs/api/api-reference/datasets/cost-per-session-by-harness-and-model). Harness/model cost comparisons.
21. Artificial Analysis. [Model Data API](https://artificialanalysis.ai/data-api). Free access and capability coverage.
22. SemiAnalysis. [GPU Spot-Contract Composite Index](https://gpu-index.semianalysis.com/). Public methodology and contract context.
23. SemiAnalysis. [AI Cloud TCO Model](https://semianalysis.com/ai-cloud-tco-model/). Institutional infrastructure economics.
24. SemiAnalysis. [Tokenomics Model](https://semianalysis.com/tokenomics-model/). Institutional application/compute economics.
25. Shadeform. [Instance types API](https://docs.shadeform.ai/api-reference/instances/instances-types). Prices, specifications and availability.
26. US Energy Information Administration. [Open Data](https://www.eia.gov/opendata/). Official energy datasets.
27. OpenRouter. [Daily token totals](https://openrouter.ai/docs/api/api-reference/datasets/daily-token-totals-for-top-50-models). History, filtering and sampling boundaries.
28. Ornn. [Premium Data Use Addendum](https://data.ornn.com/premium-data-use). Effective August 19, 2026.
29. Ornn Data LLC. [Terms of Use](https://data.ornn.com/legal/terms-of-use.pdf). Effective May 29, 2026.
30. Ornn. [MCP server](https://data.ornn.com/docs/mcp-server). Read-only connector and dataset availability.

### Radon source references

- R1. [Indicator registry](../../scripts/ai_cycle/registry.py).
- R2. [Comparability transforms](../../scripts/ai_cycle/transforms.py).
- R3. [Legacy token index](../../scripts/llm_token_index.py).
- R4. [Current collectors](../../scripts/ai_cycle/collectors.py).
- R5. [AI infrastructure operations](../ai-infrastructure-operations.md).
- R6. [Observation model and information availability](../../scripts/ai_cycle/model.py).
- R7. [Snapshot comparability and shadow timing](../../scripts/ai_cycle/snapshot.py).
- R8. [Shadow research rules](../../scripts/ai_cycle/shadow.py).
