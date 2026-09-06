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

---

## 3. Top sources (daily / high-frequency + charts)

Ranked for a desk that needs a **tape**, not a white paper. Token tape is **three hosts**: OpenRouter (levels) + Vercel (mix/spend) + Portkey (levels, scrape). Do not splice any of them. Then power, then GPU scarcity (gpurentalprices ledger).

### 1. OpenRouter Rankings + Data API
- **What:** Daily UTC tokens (prompt+completion) for top-50 models + `other`; market share, tasks, languages, spend share. Same meter as the page. **App/agent history is not in this API** — use `Socialpranker/token-history` (below).
- **URL:** https://openrouter.ai/rankings · `GET https://openrouter.ai/api/v1/datasets/rankings-daily?start_date=&end_date=` (max 366d, from 2025-01-01). Docs: https://openrouter.ai/docs/cookbook/administration/data-api
- **Cadence:** Daily; current day moves intra-day. Page: Today / 7d / 30d / trending.
- **Cost:** Charts free. API = any OpenRouter key. Rate 30/min, 500/day. CC BY 4.0.
- **Can tell you:** When *this* price-sensitive, mostly-developer/agent slice rolls over. Paid vs `:free` split. Mix to OSS / Flash.
- **Cannot:** First-party ChatGPT / Claude / Gemini / Search, China apps, private/ZDR, enterprise direct. Cross-row token equality (their docs: each row uses the upstream tokenizer). Users or dollars unless you join list prices (use `jvrck/openrouterlist` for routed $ history). App/agent levels — official `app-rankings` is a window, not a daily git tape.
- **Turn:** 4 weeks of declining **paid** top-50+other tokens **and** spend share down. Cheap OSS flood with spend up is not a peak.
- **Confidence:** **High** as an OpenRouter meter. **Low** as a global cycle index.
- **App tape (same host, different grain):** `Socialpranker/token-history` — twice-daily scrape of OpenRouter `/api/frontend/v1/rankings/apps`. Latest pointer `data/latest.json` → `apps/2026-08-28.json` (fetched 01:37Z). Hermes Agent 1.65T tokens / 17.1M req; Claude Code 485B. **Only 11 files in git: 2026-06-10–18 and 2026-08-27/28.** MIT. Frontend endpoint can break; collector fails loud. Do not sum apps onto model `rankings-daily`. Do not fill the two-month hole.
- **Price tape (same host):** `jvrck/openrouterlist` `data/history/prices.json` — change-point $/MTok from 2024-09-21, 12h catalog snapshots. 984 models as of 28 Aug 2026. MIT. `meni432/ModelGraveyard` is the event log (add/remove/price/sunset, 6h; 415 live / 129 dead; README says MIT, no LICENSE file).

### 2. Vercel AI Gateway leaderboards + export API
- **What:** Daily **share** of production traffic on AI Gateway: models / labs / apps / providers × tokens, requests, spend, imageCount, videoCount. Text / image / video. Open-vs-closed is on the page. Same tape as the monthly Production Index, without waiting for the blog.
- **URL:** https://vercel.com/ai-gateway/leaderboards
  Export (no auth, 24h cache): `GET https://vercel.com/api/ai/leaderboard-export?dataset=models&modality=text&format=json&from=2025-10-01&to=2026-08-27`
  Docs: https://vercel.com/docs/ai-gateway/leaderboards (updated 24 Aug 2026)
  CLI: `vercel ai-gateway leaderboard models|labs|apps|providers`
  Monthly write-up: https://vercel.com/blog/ai-gateway-production-index-july-2026
- **Cadence:** Daily rollups, complete from **2025-10-01**. Page: last 3 months default. Production Index is monthly (July post covers June).
- **Cost:** Free. CC BY 4.0. No API key. CLI needs a Vercel login only if you want the table command, not the HTTP export.
- **Can tell you:** Production-app mix (not chat-playground). **Volume vs spend divergence** (OSS/Flash take tokens, frontier takes dollars). Open-weight share. Image/video as separate modalities. Agent-loop concentration (Index: 22% of requests / 59% of tokens).
- **Cannot:** Absolute tokens or dollars. First-party ChatGPT / Claude / Gemini apps. Non-Gateway production. Cross-host levels (do not add to OpenRouter). Apps list is opt-in.
- **Turn:** 4 weeks of frontier **spend share** down **and** open-weight token share still rising is a *revenue* bust / mix shift, not a token bust. Need OpenRouter **paid levels** to call utilization.
- **Print 27 Aug 2026:** Live 3-month board (https://vercel.com/ai-gateway/leaderboards): DeepSeek V4 Flash **17.6%** tokens, Step 3.7 Flash 10.8%, Claude Opus 5 6.4% tokens / **21.2% spend**, open-weight **60.1%** / closed 39.9% (June Index was 29% open). Same-day export (`models`, 20–27 Aug) matches: Flash 17.61% tokens, Opus 5 21.17% spend. Docs last updated 24 Aug 2026.
- **Confidence:** **High** as a Gateway mix/spend tape. **Low** as a global volume index.

### 3. EIA-930 Hourly Electric Grid Monitor (optionally via GridStatus)
- **What:** Hourly BA demand, forecast, net gen, fuel mix, interchange. Sub-BA demand for some BAs from 2018-07-01. **Not** prices (EIA FAQ).
- **URL:** https://www.eia.gov/electricity/gridmonitor/ · Form: https://www.eia.gov/survey/#eia-930 · APIv2: https://www.eia.gov/opendata/browser/electricity/rto
  Wrapper: https://www.gridstatus.io/datasets · OSS scraper: https://opensource.gridstatus.io/
- **Cadence:** Hourly, same-day within 60 min; daily file 07:00 ET. GridStatus 5-min on native ISO markets.
- **Cost:** EIA **free**. GridStatus Free 250 req / 500k rows/mo; Pro/Ent **no $ listed** on the live page (do not use old “$500/mo” third-party quotes).
- **Can tell you:** Weather-adjusted residual in DC pockets (PJM DOM/AEP, ERCOT West/North) on shoulder-month nights. Congestion/basis into those pockets (GridStatus/ISO).
- **Cannot:** AI vs colo vs crypto vs industrial. Behind-the-meter gas (IEA: many US onsite-gas DC projects will not fully hit BA meters). Queued MW. Utilization of IT load.
- **Turn:** Persistent residual flattening in a known DC pocket **plus** a chip/guide tell. A cool July week is weather.
- **Confidence:** **High** on the product. **High** that it is a weak AI isolator.

### 4. GPU rental book + Artificial Analysis unit economics
Two feeds, one pane. Together they are the only other **public, charted, daily-or-better** scarcity/price tape.
- **gpurentalprices daily ledger (preferred ingest):** https://gpurentalprices.com/data · `GET https://gpurentalprices.com/api/latest.json` (27 Aug 2026: `date=2026-08-27`, 293 offers, 34 providers, 0 stale). GitHub `adriannutiu/gpu-rental-prices` (`data/latest.json` + 54 daily snapshots from 2026-07-05). Today's snapshot CC BY 4.0. Full history licensed. Live firm H100 **$1.99/hr** Voltage Park; Lambda list still **$3.99**.
- **Vast.ai live book:** https://vast.ai/pricing · API `vastai search offers`. Hosts set prices; on-demand vs interruptible. Still the tick-level fringe.
- **Lambda list:** https://lambda.ai/pricing — H100 SXM **$3.99**, B200 SXM6 **$6.69** / GPU-hr (fetched 27 Aug 2026).
- **Artificial Analysis:** https://artificialanalysis.ai/pricing — list + cache hit/write + reasoning + answer; Intelligence Index; tok/s. Pro **$417/seat/mo** for history/export.
- **Turn:** H100/B200 on-demand available + Vast −30% from 90d high + AA $ per Index-task **up** on the same tier (or speed down). List cuts are rare; availability flips first.
- **Confidence:** **High** on Lambda list and Vast-as-market. **High** that this is not utilization %.

### 5. Portkey LLM Rankings (third token host; scrape)
- **What:** Hourly gateway totals: requests, tokens, spend, plus tokens-by-model and spend-by-model.
- **URL:** https://portkey.ai/rankings/daily
- **Cadence:** Page says updated hourly.
- **Cost:** Charts free. **No public export.** `GET https://portkey.ai/api/rankings/daily` → 404 on 27 Aug 2026. Customer analytics API is tenant-only.
- **Can tell you:** When a *third*, more-enterprise host (Vertex/Claude-heavy) rolls over in **levels**. 27 Aug print: 380.3M req (−1.1% d/d), 4.4T tok (−13.5%), $3.8M spend (−19.1%). Top token row: Vertex `claude-opus-5` 284.4B.
- **Cannot:** Official vintage, first-party ChatGPT/Claude/Gemini, OpenRouter or Vercel traffic. Cross-host sums.
- **Turn:** 4 weeks of declining tokens **and** spend on this host, confirmed by OpenRouter paid levels. A one-day −13% token print is mix or a tenant, not a peak.
- **Confidence:** **High** that the page is a real meter of Portkey traffic. **Low** as a global cycle index. **Medium** ingest risk (scrape).

**Not in the top five, but the best *non-daily* complements:** NVIDIA IR; EDGAR companyfacts; SemiAnalysis ChipBook if you pay (no list price); TrendForce HBM $30k / AI Server $55k. The monthly Vercel Production Index is commentary on source #2, not a separate series.

---

## 4. How to build the indicator set

### 4.1 Ingest
Store tidy rows: `date, source, series_id, value, unit, vintage`. Keep first-print vs current for anything you trade.

| Freq | Pull | Path |
|---|---|---|
| Daily / intra-day | OpenRouter `rankings-daily` (sum top-50 + other; split paid vs `:free`; join `openrouterlist` prices for spend) | REST, 366d max per call |
| 2× daily | OpenRouter **app/agent** tokens via `Socialpranker/token-history` (`data/latest.json` → `apps/YYYY-MM-DD.json`). Host-tag. Do not add to model totals | Raw GitHub, no key. Frontend scrape. |
| 12h | OpenRouter routed $ change-points (`jvrck/openrouterlist` `data/history/prices.json`) | Raw GitHub, MIT |
| Optional 2×/day | First-party list $ via Model Price Watch (not volume) | `GET https://modelpricewatch.com/api/v1/price-history.json` no key |
| Daily | Vercel `leaderboard-export` (models+labs × tokens/requests/spend; text/image/video). Store share_percent, never invent a level | `GET https://vercel.com/api/ai/leaderboard-export` no key, 24h cache |
| Hourly / daily snapshot | Portkey `/rankings/daily` HTML (requests, tokens, spend, by-model). Store host-tagged levels. Do not splice | Scrape Next.js RSC; no public API as of 27 Aug 2026 |
| Daily / intra-day | EIA APIv2 `electricity/rto` + bulk “U.S. Electric System Operating Data” | Free API key |
| Daily | gpurentalprices `latest.json` (min firm + spot $/hr for H100/H200/B200). Archive each day yourself — site history is licensed | `GET https://gpurentalprices.com/api/latest.json` no key. Mirror: `adriannutiu/gpu-rental-prices` |
| 2× daily | Hubbard listing-level GPU $ (incl. Vast) if you want marketplace, not just published lists | HF `afhubbard/gpu-prices` parquet, `quality='ok'` |
| Daily | Vast search-offers (median H100 and B200, on-demand vs interruptible) | API |
| Daily | Lambda / RunPod / AWS price-list pages (snapshot the HTML/API) | Scrape or official price list |
| Daily | AA free API (indices + input/output $). Pro if you want blended + history | 100 req/day free |
| Weekly | Weather-normalize EIA-930 residuals (HDD+CDD + industrial + crypto hashrate where relevant) for ERCOT, PJM-DOM. Optional overlay: `vxguo1/powertracker` AI-campus → BA join (`data/sites/data_centers.csv`). **No license.** | Your model + public CSVs |
| Weekly | Optional: Similarweb chatgpt.com if subscribed | Paid |
| Monthly | EIA EPM Ch.5 commercial sales VA/TX/GA/OH; EIA STEO. Read Vercel Production Index as commentary only | HTML/XLS |
| Quarterly | EDGAR companyfacts JSON for MSFT, GOOGL, AMZN, META, NVDA, VRT, ETN, SMCI, ORCL, AVGO — `PaymentsToAcquirePropertyPlantAndEquipment` (Amazon: `PaymentsToAcquireProductiveAssets`), `NetCashProvidedByUsedInOperatingActivities`, inventory | https://data.sec.gov/api/xbrl/companyfacts/CIK*.json |
| Quarterly | IR EX-99.1 / slides for definitions terminals mangle (MSFT leases, AMZN TTM FCF bridge, ORCL net-cash-outlay, NVDA mix, Dell triangle) | HTML/PDF |
| Quarterly | TSMC packaging language; SK hynix/Samsung HBM language (manual flag: sold-out / meeting-demand) | Transcripts |
| If paid | SemiAnalysis ChipBook / Accelerator model; TrendForce HBM; GridStatus Pro nodal; Bloomberg DC-CMBS OAS | License |

Epoch CSV is the free five-name capex/OCF composite. Refresh from EDGAR; do not wait for their 16 Jun vintage.

### 4.2 Transforms
- Level (raw units). Do not log unless span > 10×.
- 4-week and **13-week % change** (4q / 1q if quarterly). 13-week is the default noise filter.
- **YoY** required for electricity and web traffic. Optional for tokens (short, nonstationary).
- **Z-score** of 13-week change vs trailing 2y (or full sample). Flag |z| > 1.5.
- **Breadth:** share of pane-1 series with 13-week change below their own 26-week median.
- Electricity only: residual after HDD/CDD (+ industrial / crypto where you have it). Unresidualized load is not a series you trade.
- Scarcity spread: Vast on-demand − interruptible; AA $ / Index-task for a fixed bucket.
- No HP-filter or Kalman on the live dashboard.

### 4.3 Dashboard: separate panes, one alarm
Do **not** publish a blended “AI boom index” as the object of record. Units and lags differ. Capex will keep a blended index green for two quarters into an unwind.

1. **Demand / util (HF):** OpenRouter paid model tokens + spend (levels) **and** app/agent tokens (`token-history`); Vercel Gateway daily shares (mix / volume-vs-spend); Portkey gateway levels (host-tagged, scrape); AA $ / quality / speed + `openrouterlist` routed $; gpurentalprices + Hubbard/Vast + Lambda GPU $.
2. **Power (HF + Q):** EIA-930 residuals; ISO congestion; energized/dated MW (Dominion 25 GW dated, Georgia 12.4 GW committed). Queue GW in a footnote.
3. **Hardware (M/Q):** NVDA DC $ / mix / guide / inv; TSMC/HBM language flags; SMCI inventory; Dell orders−shipments.
4. **Finance (Q + credit weekly):** TTM `CFO / cash capex` by name; FY guidance *with definition flags*; Vertiv/Eaton orders; DC-CMBS color.

| State | Rule |
|---|---|
| **GREEN** | ≥70% of pane-1 series have 13w change > 0 **and** scarcity spreads not collapsing **and** no genuine guidance cut |
| **YELLOW** | Pane-1 deceleration breadth >70% **or** scarcity spread −1.5σ for 4 weeks, finance still expanding |
| **RED** | YELLOW holds 8 weeks **and** (2 of 4 hyperscalers cut FY capex guide **or** TTM `CFO/capex` down 2q on the capex-weighted basket **or** GPU channel inventory + price weakness **or** NVDA guide implies sequential down *and** SMCI/Dell confirm) |

YELLOW is the Radon-relevant turn. RED is confirmation.

### 4.4 Turn rules (peak vs noise)
**A — utilization (earliest)**
A1. Token/proxy 13w change from >+15% ann. to <0, holds 4 weeks.
A2. Mix: Vercel open-weight token share up **and** frontier spend share down (or AA $ / 1M down on a fixed tier). Volume up + ASP down is a *revenue* bust, not a token bust. Track both. OpenRouter paid *levels* must confirm; Vercel shares alone cannot. Portkey levels are a third-host check, not a substitute (enterprise/Vertex-skewed).
A3. GPU spot/book −30% from 90d high and “sold out” commentary flips to available.
*False +:* launch week, tokenizer change, cache-accounting change, reasoning-token flood, OpenRouter losing one tenant.

**B — scarcity (confirms A)**
B1. Frontier API $ (fixed quality) down 2 months **and** output $ falling faster than input (decode overcapacity).
B2. TSMC/SK “sold out through 202X” → “meeting demand.”
B3. Vertiv/Eaton **orders** QoQ down 2q (revenue will still grow).
Need B1 **and** (B2 or B3), or A+B1.

**C — finance (lagging, high confidence)**
C1. 2 of 4 hyperscalers cut FY capex guide **or** print cash capex < prior-q run-rate without a one-time excuse. A “held” number after a useful-life/lease reclass is **not** a cut (MSFT 29 Jul 2026).
C2. TTM `CFO / cash capex` down 2q for the capex-weighted basket. Level <1 is already true at AMZN/GOOG/ORCL — that is the *build*, not the peak. The signal is the **inflection vs their own 8q path** plus a guide cut.
C3. NVDA DC YoY decelerates >20pp **and** guide sequential flat/down **and** supply-commitment language is not raised. Wait for SMCI/Dell.
C4. SMCI or Dell: inventory days up + orders < shipments two quarters.

**Falsify a YELLOW:** pane-1 13w re-accelerates above the prior 26w median within 6 weeks; GPU book re-bids; energized MW still stepping up 2q; next print *raises* capex guide. Then it was a mid-cycle pause.

**Do not use `FCF / NI`.** Amazon Q2 NI $62.6B includes $53.4B pre-tax other income, “primarily Anthropic.” MSFT Q4 includes $3.2B Anthropic + OpenAI marks. NVDA other income includes equity gains. `Capex / D&A` is an accounting diagnostic (MSFT 15→25y), not demand.

---

## 5. Failure modes

### Tokens
- OpenRouter: “A token in one row is not directly comparable to a token in another row from a different provider.” `other` is coarse magnitude only.
- `token-history` is the **same host**, app cut. Hermes Agent 1.65T/day is not incremental to `rankings-daily`. Frontend `/api/frontend/v1/rankings/apps` can break; ranks on the 28 Aug file start at #2 (missing #1). **Gap 2026-06-19 → 08-26** (11 files only). Do not interpolate.
- Cache hits flatten spend while tokens rise (AA now prices cache hit / write / input / output / reasoning / answer separately).
- Reasoning / hidden tokens sit inside completions. Agent loops: Vercel, 22% of requests but **59% of tokens**.
- Flash / OSS mix: Vercel 27 Aug board, open-weight **60.1%** of tokens while Claude Opus 5 is **21.2%** of spend. Tokens up, boom dying.
- Vercel export is **share only**. Do not splice Gateway shares onto OpenRouter token levels.
- Portkey is a **third host** with levels, scrape-only, enterprise-skewed. A −13% d/d token print (27 Aug) is not a 4-week rule. Do not add Portkey tokens to OpenRouter or invert Vercel shares into a Portkey total.
- Tidelines.ai / whatstrending.ai are OpenRouter (and Vercel) archives. Do not ingest as a fourth tape.
- `jampongsathorn/openrouter-rankings` still commits daily but `rankings.json` has been empty since ~20 May 2026. Zombie. Do not ingest.
- AnyRouter `GET https://anyrouter.dev/api/v1/analytics/network` is a real no-key JSON (28 Aug 03:00Z: 6.85B tok / 84.8k req / 30d; hourly+daily trend). **Too small and 87% cached tokens** (5.94B/6.85B). Watch, not a cycle tape.
- Model Price Watch `https://modelpricewatch.com/api/v1/price-history.json` is a free list-price book (236 models, 116 dates from 2024-02-05, updated 27 Aug 2026). Optional join next to `openrouterlist`. Not volume.
- Google 3.2Q/month is **all surfaces**. Fireworks 40T/day is **one host**. Do not splice.
- Private / ZDR excluded on OpenRouter. `:free` ranked separately.
- tokensperday.com intra-day counter is grown/modeled. Floor vintage was 16 Jul 2026.

### Electricity
- Weather dominates hourly and monthly BA load. 10–20 GW ERCOT/PJM heat swing swamps weekly AI increment.
- Mix: colo, cloud GP, enterprise, CDN, **crypto**.
- Queue ≠ energized ≠ utilized. ERCOT 438 GW requested vs 85.5 GW peak is not a demand forecast. Georgia 76.2 GW pipeline vs 12.4 GW committed.
- LBNL Queued Up is **not** a DC load queue.
- Behind-the-meter gas hides load from EIA-930.
- IEA/LBNL/EPRI/Ember TWh **do not match**. Do not splice.
- Chips can roll over quarters before BA load does. Power is realization, not a lead.

### GPUs
- NVDA Q3 FY27 $108B **assumes no China DC compute** — a license prints a beat with no RoW demand.
- $ is price × mix. Units are not disclosed.
- No public util %. gpurentalprices history starts **2026-07-05** (54 days as of 27 Aug) — too short for a 13-week z-score. Build your own archive of `latest.json`.

### Finance / guidance games (verified in current prints)
1. **MSFT useful life 15→25 and finance→operating leases** (29 Jul 2026 call). Same build, lower printed capex.
2. **Three capex definitions:** cash PP&E ≠ cash+finance leases ≠ net of incentives ≠ ORCL “net cash outlay”.
3. **Calendar vs fiscal mixing** (MSFT June year, NVDA Jan year, ORCL May, Dell Feb).
4. **ORCL RPO $638B** includes **$75B prepaid / customer-supplied GPUs**. ~12% converts in 12 months.
5. **MSFT commercial RPO $678B +84%; +25% ex-OpenAI.**
6. **Mark-to-market NI** (AMZN Anthropic, MSFT Anthropic/OpenAI, NVDA equity gains).
7. **AVGO $100B FY27** reiterated, not raised (3 Jun 2026).
8. **CRWV ~$104B backlog** plus a “>$25B early Q3” footnote.
9. Azure is a **rate**, not a dollar. NVDA DC includes networking/software. AMZN cash capex mixes AWS + Stores + devices.
10. NVDA supply agreements are “cancellable, able to be rescheduled, or adjustable” (Q1 10-Q).

SemiAnalysis / TrendForce / bank “supercycle” decks sell the boom. Require a primary (ISO, 10-Q, EIA, TSMC) before a YELLOW.

---

## 6. What a 2026 tape already shows (not a call)

| Layer | Status 27 Aug 2026 | Peak? |
|---|---|---|
| Tokens | OpenRouter **models** = levels; OpenRouter **apps** = `token-history` (same host, do not add); Vercel = daily mix/spend; Portkey = third-host levels (scrape). Do not splice | Need OR paid model tokens **and** Vercel frontier spend share down. App tape is mix (agent loops). Portkey is a check, not a vote. |
| GPU $ | gpurentalprices 27 Aug: firm H100 $1.99 (Voltage Park); Lambda list still $3.99 / $6.69; Vast is a book | Watch 90d change once the ledger is long enough. Don’t freeze a level |
| NVDA | DC +18% q/q, +117% y/y; guide $108B; inv $31.6B | **Not yet.** Inv/AR is the yellow flag inside a beat |
| Hyperscaler guides | Still up or “unchanged” (GOOG $195–205B; META $130–145B; AMZN call $220B; MSFT ~$175B after reclass) | **Not a demand peak** |
| Cash conversion | AMZN 0.96; GOOG Q2 0.87; ORCL 0.57; META 1.03; MSFT still >1 but FCF −23% y/y | **Financed build.** Necessary, not sufficient |
| Channel | Dell Q1 still orders > shipments; SMCI inventory building | Watch 1 Sep Dell print |
| Power kit | Eaton Electrical B2B 1.2 in Q2 | Orders have not rolled |
| Queues | ERCOT 410–438 GW; Oncor ~282 GW DC; Dominion 25+45 GW | Vapor. Ignore for timing |

**Confidence:** **High** that cash conversion has already turned. **High** that this is not, by itself, a utilization peak. **Medium** on SMCI Q4 inventory until the IR HTML is re-pulled. **Low** on any “global tokens/day” headline.

---

## 7. First 30 days (if you actually stand this up)

1. Key the OpenRouter `rankings-daily` history from 2025-01-01. Build paid vs free and a spend join against `jvrck/openrouterlist` `prices.json` (from 2024-09-21). Also key `Socialpranker/token-history` app snapshots — 11 files only (2026-06-10–18, 2026-08-27/28). Do not sum apps onto models. Do not fill the hole.
2. Key Vercel `leaderboard-export` from 2025-10-01 (models + labs, text, tokens/requests/spend). Store shares. Do not invent a Gateway token total.
3. Stand up a Portkey `/rankings/daily` scrape (hourly or EOD). Store host-tagged levels. If the RSC shape moves, drop the series rather than guess.
4. Key EIA-930 for ERCOT and PJM (DOM/AEP). Start the weather residual. Do not trade it until you have one summer and one shoulder in-sample.
5. Daily snapshot `gpurentalprices.com/api/latest.json` (keep your own history) + Vast median H100/B200 + Lambda list.
6. EDGAR companyfacts panel + a **definition-flag** column (MSFT 29 Jul 2026 life/lease; ORCL net-cash-outlay). Compute TTM `CFO / cash capex`.
7. One alarm, three colors, no adjectives.
8. Optional paid, in order: AA Pro ($417) if you want price history; GridStatus Pro if you want nodal into NOVA / West Texas; SemiAnalysis ChipBook if you want CoWoS/HBM units; TrendForce HBM if you don’t buy SemiAnalysis.

Do not subscribe to tokensperday, Arena, or HF downloads as cycle inputs.

---

## Sources (primary, fetched 27 Aug 2026)

OpenRouter rankings + Data API; Socialpranker/token-history app snapshots (verified 28 Aug 2026, Hermes Agent 1.65T); jvrck/openrouterlist price ledger (as_of 2026-08-28, 984 models, from 2024-09-21); meni432/ModelGraveyard events (415/129); alex-hubbard/gpu_price_tracker (HF parquet); vxguo1/powertracker (no license); Artificial Analysis pricing/data-api; Vercel AI Gateway leaderboards + `leaderboard-export` (verified 27 Aug 2026, history from 2025-10-01) and Production Index (Jul 2026); Portkey Rankings daily (verified 27 Aug 2026; no public API); gpurentalprices `latest.json` + `adriannutiu/gpu-rental-prices` (54 daily snapshots from 2026-07-05, verified 27 Aug 2026); Epoch data hub + usage-reports CSV + hyperscaler OCF/capex CSV; Google I/O 19 May 2026; OpenAI Signals 12 Aug 2026; Anthropic Economic Index 26 Jun 2026; Fireworks Series D 15 Jul 2026; EIA-930 / Open Data / EPM / STEO Aug 2026; GridStatus pricing + datasets; ERCOT Large Load hub + Apr/Jun 2026 decks; PJM 2026 LTLF; SPP HILL; IEA Key Questions 16 Apr 2026; LBNL Queued Up 2026 and US DC Energy 2025 Update; EPRI Powering Intelligence 2026; NVIDIA Q2 FY27 PR/CFO commentary 26 Aug 2026; TSMC 2Q26 transcript; SK hynix / Samsung Q2 2026; TrendForce DRAMeXchange cart; Lambda pricing; Vast.ai pricing; MSFT FY26 Q4 IR + 8-K 29 Jul 2026; Alphabet Q2 2026 IR/10-Q; Amazon Q2 2026 EX-99.1; Meta Q2 2026 IR; Oracle FY26 PR; CoreWeave Q2 2026 IR; Vertiv / Eaton Q2 2026 exhibits; Dell FY27 Q1; Equinix / Digital Realty Q2 2026.

Series-level source notes were verified on publisher pages 27 Aug 2026 (PT). Re-open each URL before pasting a print into a model.
