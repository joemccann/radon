# AI boom leading indicators
**Working document for Joe McCann / Radon**  
**As-of:** 27 Aug 2026 (PT)  
**Author:** AI Boom Watch  
**Type:** quant memo. Primary sources only. Confidence labeled. Not a forecast.

**How to use this:** pane-1 (OpenRouter levels, Vercel mix/spend, Portkey levels if the scrape holds, gpurentalprices GPU $, residual load) is the tradeable tape. Capex and interconnect queues confirm 2–4 quarters late. Cash conversion is already flashing *financed build*, not *demand peak*. Do not blend these into one 0–100 index.

---

## 0. Bottom line

A turn is a **joint** event: utilization growth decelerates **and** the scarcity premium collapses **and** cash conversion stays broken while guidance is still held or cut. One of three is noise.

There is **no public hourly data-center-only MW series** and **no public fleet GPU-utilization series**. Tokens are not comparable across labs. Interconnect GW is mostly vapor.

**As of this print the financial tape already shows spend > cash** (Amazon TTM FCF −$7.6B; Alphabet Q2 FCF −$5.9B; Oracle FY26 FCF −$23.7B; Meta Q2 FCF $0.8B) **while NVIDIA guided Q3 FY27 to $108.0B ±2% (26 Aug 2026) and hyperscaler FY capex guides are still up or “unchanged.”** That coexistence is the mid-cycle signature, not a peak call. Confidence: **high** on the prints, **high** that they are not yet a demand rollover.

---

## 1. What to track (and what is not a cycle signal)

| Series | Why it matters | Cycle role | Verdict |
|---|---|---|---|
| **LLM tokens / inference volume** | Closest public *use* meter | Coincident demand, severe mix bias | Track **paid** tokens + spend together. Level alone is not a signal. |
| **Model API $ / quality / speed** | Unit economics. Price war vs congestion. | Leading-to-coincident for *margins*, not volume | Track $ per *fixed quality bucket*, not headline list price. |
| **Consumer AI web/app traffic** | Attention, not FLOPS | Coincident consumer | Use **absolute** visits/MAU across ChatGPT+Gemini+Claude, not share. |
| **Data-center electricity** | Physical realization of racks | Lagging vs chips; coincident vs weather if unadjusted | Only **weather-residual** load in known DC pockets. Raw ISO load is not AI. |
| **Grid interconnect queues** | Intent, 2–5 years forward | Leading **and** mostly vapor | Track **energized / dated-contract MW**, not requested GW. |
| **GPU / accelerator shipments** | The actual scarce good | Coincident ($, mix-biased); units rarely disclosed | NVIDIA DC $ + mix + guide. Units are inferred. |
| **GPU cloud rental prices** | Only high-freq scarcity proxy | Leading-to-coincident | **gpurentalprices** daily ledger + Vast book + Lambda list. Not hyperscaler util. |
| **HBM / CoWoS commentary** | Binding constraint | Leading for *ceiling*, late for *glut* | Qualitative. Sold-out language disappearing is the tell. |
| **Hyperscaler capex** | Committed 12–24 months out | Lagging confirmation; guidance is a game | Pair cash PP&E with `CFO / cash capex`. |
| **Cash conversion** | Who is funding the build | Coincident *finance*, not demand | Primary ratio: TTM `CFO / cash capex`. |
| **Power-kit orders** (Vertiv, Eaton) | Long-lead physical | Leading vs cash capex | Orders / book-to-bill, not revenue. |
| **Server-channel inventory** (SMCI, Dell) | Stuffing vs ramp | Leading-to-coincident glut | Inventory days + orders vs shipments. |
| **Training FLOP / notable models (Epoch)** | Structural supply | Lagging / research | Do not trade. |
| **HF downloads, Arena Elo** | Weights and preference | **Not a signal** | Exclude. |

**Better leading series than “token totals” or “ISO load”:**
1. OpenRouter **paid** daily tokens **and** spend share (same host). **Levels.**
2. Vercel Gateway daily **share** of tokens / requests / spend (export, not the blog). **Mix. Do not splice to #1.**
3. Portkey Rankings hourly **levels** (requests / tokens / spend on *their* gateway). **Third host. Scrape-only. Do not splice to #1 or #2.**
4. GPU rental: **gpurentalprices** daily min $/hr (H100/H200/B200 firm vs spot) + Vast book + Lambda list cuts.
5. NVIDIA next-quarter DC guide + Hyperscale vs ACIE mix + inventory/AR (26 Aug 2026: DC $89.0B, inventories $31.6B).
6. Dell AI **orders vs shipments vs backlog**.
7. Vertiv / Eaton Electrical **orders** and book-to-bill.
8. Artificial Analysis **$ per Intelligence-Index task** for a *fixed* tier (cache/reasoning split).
9. Weather-residual EIA-930 / PJM-DOM / ERCOT nighttime baseload (shoulder months).
10. DC SASB CMBS concessions / neocloud new-issue spreads (Bloomberg; no FRED series).

---

## 2. How these are tracked today

Cadence key: **H** hourly/tick, **D** daily, **W** weekly, **M** monthly, **Q** quarterly, **I** irregular.

### 2.1 Tokens, pricing, attention

| Series | Publisher | URL | Cadence | Lag | Revise? | Access | Charts | Signal quality |
|---|---|---|---|---|---|---|---|---|
| Rankings + `rankings-daily` | OpenRouter | https://openrouter.ai/rankings · Data API https://openrouter.ai/docs/cookbook/administration/data-api | **D** (UTC; intra-day on current bucket) | Same day (“through 27 Aug 2026” on fetch) | Yes — snapshots restated; private/ZDR excluded | Public charts. API needs any OR key. 30/min, 500/day. CC BY 4.0. History from 2025-01-01 | Yes | **Only real public daily token meter.** OpenRouter-routed only. Tokenizers not comparable across rows. |
| OpenRouter **app/agent** daily tokens | Socialpranker/token-history | https://github.com/Socialpranker/token-history · raw `data/latest.json` → `data/apps/YYYY-MM-DD.json` | **D** (Actions twice daily) | Same day / T+0.5 | Yes — frontend scrape | **Public, no key, MIT.** Only **11** daily files: 2026-06-10–18, then **dead until 2026-08-27/28**. Cite OpenRouter. | Yes (JSON) | **The official model API does not keep this.** Scrapes `/api/frontend/v1/rankings/apps`. Live 28 Aug: Hermes Agent **1.65T** / 17.1M req; Claude Code 485B. Do not add to `rankings-daily`. **Do not interpolate the Jun 19–Aug 26 hole as zero.** |
| OpenRouter routed **price history** | jvrck/openrouterlist | https://github.com/jvrck/openrouterlist · `data/history/prices.json` · site https://openrouterlist.jvrck.com/ | **12h** (00:00 / 12:00 UTC) since **2024-09-21** | Same day | Change-point ledger (not every 12h tick) | **Public, MIT.** 984 models, 379 present / 605 gone as of 28 Aug 2026. | Yes | Longest public routed $/MTok history. `/api/v1/models` is current-only. Join to `rankings-daily` for spend. Do not dual-ingest `rjalexa/llmprices` (viz of this tape). |
| Intelligence Index, $ / task, tok/s, cache stack | Artificial Analysis | https://artificialanalysis.ai/ · https://artificialanalysis.ai/pricing · https://artificialanalysis.ai/data-api | Event; new SKUs ~24h; speed historically 14d median | Hours–1 day | Yes (index versions) | Freemium. Free API 100/day. **Pro $417/seat/mo** (listed) | Yes | Unit economics. **Not volume.** |
| Daily share of tokens / requests / spend (models, labs, apps, providers) | Vercel AI Gateway leaderboards | https://vercel.com/ai-gateway/leaderboards · export `GET https://vercel.com/api/ai/leaderboard-export` · docs https://vercel.com/docs/ai-gateway/leaderboards · CLI `vercel ai-gateway leaderboard` | **D** (cached 24h). Production Index is **M** commentary on the same tape | Same day / T+1 | Yes — prior months revised; shares restated | **Public, no key, CC BY 4.0.** History complete from **2025-10-01** | Yes + CSV/PNG | **#2 HF token source.** Production-app mix + volume-vs-spend. **Share only — no public absolute tokens.** Do not splice to OpenRouter levels. 27 Aug 2026 live board (3-month): DeepSeek V4 Flash **17.6%** tokens; Claude Opus 5 **21.2%** spend; open-weight **60.1%** / closed 39.9%. Export is share_percent only. |
| Hourly requests / tokens / spend by model (gateway) | Portkey Rankings | https://portkey.ai/rankings/daily · weekly / monthly views on same host | **H** (page says updated hourly). Daily / 30D / 90D / 1Y toggles | Same hour / T+1 | High — hourly restates; no vintage stamp | **Public charts. No public API** (`/api/rankings/daily` 404 on 27 Aug 2026). Ingest = scrape Next.js RSC payload. | Yes | **#5 HF token source. LEVELS on a third host.** Live 27 Aug: 380.3M req (−1.1% d/d), 4.4T tok (−13.5%), $3.8M spend (−19.1%). Top token print Vertex `claude-opus-5` 284.4B. Enterprise / Vertex-skewed. Do not splice to OR or Vercel. Analytics graph API is customer-only. |
| Usage-reports CSV, models, DC, chips | Epoch AI | https://epoch.ai/data · https://epoch.ai/data/ai_companies_usage_reports.csv | I (ZIP “updated daily” = file refresh) | Days for tables; months for token fields | Yes (estimates) | Public, CC BY | Yes | Warehouse for lab prints. Not a weekly tape. |
| “Tokens processed today” | tokensperday.com | https://tokensperday.com/ | Presents daily; **modeled** | Floor vintage on page was **16 Jul 2026** | High | Public | Yes | **Bibliography, not a meter.** |
| All-surface tokens | Google I/O | https://blog.google/innovation-and-ai/sundar-pichai-io-2026/ (19 May 2026) | Annual | ~3 months as of late Aug | Yes (all-surface definition) | Public | Narrative | **>3.2Q tokens/month**; APIs ~19B tok/min. Not comparable to API-only. |
| Enterprise ratios | OpenAI Signals | https://openai.com/signals/enterprise-data/ (12 Aug 2026; data June) | I | 6–8 weeks | Yes | Public | Yes | No global total. |
| Occupation / hour-of-day | Anthropic Economic Index | https://www.anthropic.com/research/economic-index-june-2026-report (26 Jun 2026) | I | Weeks | Yes | Public | Yes | No Claude T/day. |
| Fireworks host print | Fireworks | https://fireworks.ai/blog/series-d-announcement (15 Jul 2026) | I / marketing | ~6 weeks | High | Public | No | **>40T/day** one host; 95% custom. |
| chatgpt.com visits | Similarweb | https://www.similarweb.com/website/chatgpt.com/ | M (daily in paid) | 2–6 weeks | Yes | Freemium; AI Search from **$99/mo** listed | Yes | Attention, not tokens. Fetch 403’d; confirm in browser. |
| AI-app MAU / time | Sensor Tower | https://sensortower.com/blog/state-of-ai-2026 | Product can be D; report I (16 Jun 2026) | ~2 months on report | Yes | Quote-only | Yes | data.ai is folded in. Not tokens. |
| Workers AI inferences, DNS share | Cloudflare Radar | https://radar.cloudflare.com/ai-insights | 7d widgets; API timeseries | Days | **Yes — 18 Jun 2026 metric rewrite** | Public + Radar API | Yes | CF footprint, not market tokens. |
| State of AI 100T study | OpenRouter × a16z | https://openrouter.ai/state-of-ai · https://a16z.com/state-of-ai/ | One-shot (4 Dec 2025), weekly internals | Stale | N/A | Public | Yes | Mix study, not a 2026 series. |
| HF downloads | Hugging Face | https://huggingface.co/models | Rolling 30d | ~1 day | Yes | Public; org daily CSV is Team/Enterprise | Yes | **Weights, not inference.** |
| Arena Elo | Arena | https://arena.ai/leaderboard | Continuous votes | Hours–days | Yes | Public | Yes | **Preference, not volume.** |
| Helicone / Groq / Together public tokens | — | — | — | — | — | — | — | **No verified public market series.** Helicone → Mintlify maintenance. |

### 2.2 Power and interconnects

| Series | Publisher | URL | Cadence | Lag | Revise? | Access | Charts | Signal quality |
|---|---|---|---|---|---|---|---|---|
| Form EIA-930 / Hourly Grid Monitor | EIA | https://www.eia.gov/electricity/gridmonitor/ · API https://www.eia.gov/opendata/browser/electricity/rto | **H** (same-day ≤60 min); daily file 07:00 ET | 0–60 min / T+1 | High on same-day | **Public, free** (API key, no fee) | Yes | Best legal US load tape. **Cannot isolate AI.** There is **no** “Electric Power Weekly.” |
| ISO 5-min/hourly load, LMP, mix | ERCOT/PJM/CAISO/MISO/SPP + GridStatus | https://www.gridstatus.io/datasets · https://opensource.gridstatus.io/ | **H** / 5-min | Near real-time | Medium (settlements) | Native free. GridStatus Free: 250 req / 500k rows/mo. **No $ on live pricing page.** | Yes | Markets, not DC MW. Queue app is **generation**. |
| Electric Power Monthly / Monthly Update | EIA | https://www.eia.gov/electricity/monthly/ | **M** | ~2 months (June 2026 on 27 Aug fetch) | Material | Public XLS/CSV | Yes | State commercial sales (VA, TX, GA, OH) least-bad official monthly. Still mixed. |
| STEO | EIA | https://www.eia.gov/outlooks/steo/ (11 Aug 2026; next 9 Sep) | **M** | Weeks | Yes | Public | Yes | Aug-26 cut Texas 2027 load growth 14% → 6% after 3 Aug pause. Forecast, not a meter. |
| Energy and AI / Key Questions | IEA | https://www.iea.org/reports/key-questions-on-energy-and-ai (16 Apr 2026) | I / annual-ish | Months–year | Yes | Free HTML; XLSX behind account | Yes | **485 TWh (2025) → 950 TWh (2030)** central. Scenario bible. Not a cycle print. |
| US DC Energy Usage 2025 Update | LBNL | https://eta-publications.lbl.gov/publications/united-states-data-center-energy-2025 (Jun 2026) | I | Years | Medium | Public | In report | Bottom-up from IT shipments. 2030 ref **649 TWh**. |
| Queued Up 2026 | LBNL | https://emp.lbl.gov/queues | **Annual** (through end-2025) | 5–12 months | Medium | Public Excel | Yes | **Generation/storage only. Large-load queues excluded.** 13% of 2000–20 requests reached COD. |
| Powering Intelligence 2026 | EPRI | https://powering-intelligence.epri.com/summary-projections.html | I | — | — | Public HTML | Yes | Includes crypto. Warns FERC 714 double-counts. |
| ERCOT Large Load / Batch Zero | ERCOT | https://www.ercot.com/services/rq/large-load-integration/ | I (decks) | Weeks–months | High | Public PDFs | In PDFs | **~410–438 GW** requested vs **85.5 GW** peak. Vapor. ≥75 MW, $50k/MW security in Apr-26 deck. |
| PJM LTLF + Large Load Adjustments | PJM | https://www.pjm.com/planning/resource-adequacy-planning/load-forecast-dev-process.aspx | Annual + intra-year LAS | Months | Medium | Public PDFs | In PDFs | Haircut utility submissions. Forecast, not realized. |
| SPP HILL | SPP | https://www.spp.org/markets-operations/high-impact-large-load-hill-integration/ | Process | — | — | Public | No MW tape | FERC effective 15 Jan 2026. No verified public energized-AI series. |
| Dominion GS-5 / IRP | Dominion | GS-5 PDF on sustainability.dominionenergy.com | I | — | — | Public PDF | No | **25 GW** dated through 2031; **+45 GW** undated. |
| Georgia Power large-load | GA PSC | Docket 55378 / 56002 quarterly | **Q** | ~1q | Redactions | Public | No | 31 Mar 2026: **12.4 GW committed** vs **76.2 GW** pipeline. |
| Oncor LC&I | Oncor 10-Q / earnings | Confirm on https://www.oncor.com | **Q** | Weeks | Medium | Public | No | **~282 GW** DC requests (30 Jun 2026, earnings reprint). Queue, not energized. |

### 2.3 GPUs, HBM, channel

| Series | Publisher | URL | Cadence | Lag | Access | Signal |
|---|---|---|---|---|---|---|
| DC revenue, mix, guide, inventory | NVIDIA | https://investor.nvidia.com · Q2 FY27 PR 26 Aug 2026 https://www.sec.gov/Archives/edgar/data/1045810/000104581026000073/q2fy27pr.htm | **Q** (~31d lag) | Public | **Best official GPU-cycle print.** DC $89.023B; Hyperscale $48.7B / ACIE $40.3B; Q3 guide $108.0B ±2%, **no China DC compute** in outlook. Inventories $31.575B. **No unit series.** |
| CoWoS / packaging language | TSMC | https://investor.tsmc.com Q2 2026 transcript (16 Jul 2026) | **Q** | Public | Tight packaging “limits customers’ growth.” No official CoWoS wafer-start series. |
| HBM commentary | SK hynix, Samsung | skhynix / samsung semiconductor news (29–30 Jul 2026) | **Q** | Public | HBM4 mass ship (SK). No unit table. |
| Accelerator & HBM Model, ChipBook, ClusterMAX | SemiAnalysis | https://semianalysis.com/accelerator-hbm-model/ · https://www.clustermax.ai/ | Q models; ~biweekly essays; ClusterMAX versioned | **Institutional, no list $.** Newsletter ≠ models. | Best commercial shipment construct. Not a public tape. |
| AI Server / HBM packages | TrendForce | https://www.trendforce.com/membership/DRAMeXchange | Biweekly / monthly / Q | **Listed:** AI Server $55k; HBM $30k; AI Infra Bulletin $12k | Paid units/ASP. Analyst revisions. |
| DC IT semis | Dell’Oro | delloro.com DC IT semis page | **Q** + Jan/Jul 5y | Contact sales. No list $. | Units + $ + share. |
| On-demand GPU $ | Lambda | https://lambda.ai/pricing | Live list | Public | **H100 SXM $3.99/GPU-hr; B200 SXM6 $6.69.** No spot tier on page. |
| Daily GPU $/hr ledger (34 providers) | GPU Rental Prices | https://gpurentalprices.com · `GET https://gpurentalprices.com/api/latest.json` · CSV on /data · GH `adriannutiu/gpu-rental-prices` | **D** (append-only). 54 snapshots 2026-07-05 → 2026-08-27 | Same day | Low on today's print; history cannot be backfilled | **Today's snapshot public, CC BY 4.0, no key.** Full ledger licensed (`data@gpurentalprices.com`). GH rolling window + HF/Kaggle mirrors. | Yes | **Best public daily GPU $ tape.** 293 offers, 34/34 providers ok 27 Aug. Firm H100 $1.99 Voltage Park. Spot vs on-demand in `kind`. Not hyperscaler util. |
| Listing-level GPU $ (incl. Vast) | alex-hubbard/gpu_price_tracker | https://github.com/alex-hubbard/gpu_price_tracker · HF `afhubbard/gpu-prices` | **2×/day** (09:00 / 21:00 UTC) since Jan 2026 | Hours | Quality tag (`ok` vs stale) | **Living off-repo.** Code last git-push 8 Jul 2026; Actions still green 27 Aug. MIT / CC BY 4.0. DuckDB parquet on HF/S3. | Streamlit | Offer-level complement to gpurentalprices (which skips Vast marketplace). Filter `quality='ok'`. |
| Marketplace GPU $ | Vast.ai | https://vast.ai/pricing · docs.vast.ai | **Tick / live** | Public API | Hosts set prices. Scarcity proxy. Do not freeze one $. |
| RunPod / AWS P5 | runpod.io/pricing · aws.amazon.com/ec2/pricing | Live | Public | RunPod $ on console, not a static table. **AWS P5 $55.04 not pulled from AWS HTML — unverified here.** |
| Fleet utilization % | — | — | — | — | **Does not exist in public.** Infer from rental book + NVDA guide. |

### 2.4 Finance (latest primary prints)

Fiscal calendars differ. Do not stack “2026 capex” without a date map. MSFT FY ends 30 Jun; NVDA last Sunday in Jan; ORCL 31 May; Dell late Jan; SMCI 30 Jun.

| Issuer | Latest | Capex / related | Cash conversion | IR / SEC |
|---|---|---|---|---|
| MSFT | FY26 Q4, 29 Jul 2026 | Cash PP&E $35.8B Q4 / $115.9B FY; +finance leases $41B Q4; calendar-2026 **~$175B** after 15→25y life and lease reclass; FY27 Q1 **>$50B** | CFO $55.4B Q4; FCF $19.6B (−23% y/y) | https://www.microsoft.com/en-us/investor · 8-K https://www.sec.gov/Archives/edgar/data/789019/000119312526323632/msft-20260729.htm |
| GOOGL | Q2, 22 Jul 2026 | Capex **$44.9B**; FY guide **$195–205B** (from $180–190B); ~60/40 servers vs DC+network | OCF $39.1B; **FCF −$5.9B** | https://abc.xyz/investor/ · 10-Q https://www.sec.gov/Archives/edgar/data/1652044/000165204426000071/goog-20260630.htm |
| AMZN | Q2, 30 Jul 2026 | **No FY capex in the 8-K.** Jassy **$220B** is call commentary (CNBC), not the exhibit. TTM net PP&E $169.0B | TTM OCF $161.4B; **TTM FCF −$7.6B**; ratio **0.96** (crossed 1.0 this quarter) | https://ir.aboutamazon.com · EX-99.1 https://www.sec.gov/Archives/edgar/data/1018724/000101872426000024/amzn-20260630xex991.htm |
| META | Q2, 29 Jul 2026 | Capex +leases; FY **$130–145B** | CFO $31.9B; **FCF $784M** (ratio ~1.03) | https://investor.atmeta.com |
| NVDA | FY27 Q2 ended 26 Jul, **26 Aug 2026** | DC $89.0B; Q3 **$108.0B ±2%**, China-ex | AR $63.1B; inv $31.6B; Q2 CFO down vs Q1 (working capital) | investor.nvidia.com |
| ORCL | FY26 Q4, 10 Jun 2026 | RPO **$638B**; $75B of that is prepaid / customer-supplied GPUs; FCF **−$23.7B** | CFO/capex **0.57** | investor.oracle.com |
| CRWV | Q2, 11 Aug 2026 | Backlog **~$104B** (+$25B footnote early Q3) | Q2 CFO $679M vs PP&E $6.42B | investors.coreweave.com |
| VRT | Q2, 29 Jul 2026 | Sales $3.27B; FY $13.8–14.2B | Orders are the lead (confirm $ in 8-K tables) | investors.vertiv.com |
| ETN | Q2 2026 | Electrical book-to-bill **1.2**; 12-mo orders smoothed | 12-mo order *derivative* is the tell | eaton.com IR |
| SMCI | FY26 Q4 ended 30 Jun (call 11 Aug) | Channel inventory (Q2 PR $10.595B; Q4 $12.896B **medium confidence**, IR timed out) | Inventory/sales + OCF | ir.supermicro.com |
| Dell | FY27 Q1 ended 1 May; **Q2 reports 1 Sep 2026** | Q1 AI orders $24.4B > shipments $16.1B | Orders vs shipments | investors.delltechnologies.com |
| EQIX / DLR | Q2 2026 | EQIX bookings $424M; DLR backlog / pre-lease | Bookings lead commence 3–24m | investor.equinix.com · DLR GNW 23 Jul 2026 |

**Epoch composite (free CSV):** https://epoch.ai/data-insights/hyperscaler-capex-vs-cash-flow — `ocf_vs_capex_log_data.csv`, updated 16 Jun 2026 (one quarter stale vs the prints above).

**Not reusable series:** a16z / Sequoia essays; FRED HY OAS (not DC-specific); GPU-loan prices (none public).
