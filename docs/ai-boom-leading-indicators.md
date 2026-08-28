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
| Usage-reports CSV, models, DC, chips | Individual rows continue in part 2 if truncated. Keep every row you received. |
