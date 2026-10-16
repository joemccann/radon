# Options "Flow Terminal" Design + Build Brief
Source thread: https://x.com/BuildwithPublic/status/2109650320746074416 (last post of the author's chain; conversation root is https://x.com/BuildwithPublic/status/2109650307680534890)
Author: Matthew@Public (@BuildwithPublic), bio "Director of API trading @Public. Helping traders build better execution infrastructures on our API. Not financial advice." ~2.5k followers.
Posted: 2026-10-12, 7:20 AM PT (14:20 UTC). Root post stats at read time (2026-10-13 ~5:35 AM PT): ~12.9k impressions, 135 likes, 220 bookmarks, 15 direct replies, 4 reposts, 2 quotes (only 1 quote retrievable).
Product name visible in the screenshot: **TZU - "SYSTEMATIC OPTIONS"** (an SPX 0DTE flow + dealer-positioning + auto-execution desk, running in PAPER mode).
Read method: X API (post lookup, full conversation search, quote lookup) plus full-resolution download and zoomed crops of every image. No videos exist in the author's chain. Total thread: 7 author posts, 1 image; 40+ replies; 1 quote post.
No PDF attached: this reply channel is text-only, so the full brief is below as markdown.

---

## 1. Thread transcript (author's chain, verbatim, in order)

**1/7** - https://x.com/BuildwithPublic/status/2109650307680534890 (14:20:03 UTC) [has 1 image, described in section 2]
> Finished some iterations of 0DTE SPX desk entirely on the @public API-
>
> Every few seconds it pulls the full SPXW chain (quotes, greeks, open interest), spots the unusual block prints, and maps where dealers have to hedge. Then it fires a real order in under a second.
>
> Greeks in, trades out- Here's how it works:

**2/7** - https://x.com/BuildwithPublic/status/2109650310922899727 (14:20:04 UTC)
> The data: @Public gives you SPX index quotes, the full 0DTE SPXW chain with delta, gamma, IV and open interest on every strike, plus 1-minute bars.
>
> That's enough to rebuild a dealer positioning model yourself, from scratch.

**3/7** - https://x.com/BuildwithPublic/status/2109650312415908055 (14:20:04 UTC)
> The print detector reads the tape every second.
> When one contract trades 10x+ its normal minute, or a single block hits at the ask, it gets flagged and plotted as a bubble on the SPX chart, sized by premium, right where it hit

**4/7** - https://x.com/BuildwithPublic/status/2109650313837707625 (14:20:04 UTC)
> The dealer map: every print is signed by whether it lifted the ask or hit the bid. Combined with open interest and Black-Scholes greeks, that gives you dealer gamma, charm and vanna by strike.
>
> Out of that come the gamma flip, the call wall, the put wall and the accelerant strike.

**5/7** - https://x.com/BuildwithPublic/status/2109650315939061925 (14:20:05 UTC)
> Why it matters: when dealers are short gamma, they chase price and moves accelerate. When they're long gamma, price pins.
>
> So every print gets an edge score from 0 to 6: does the path from SPX to that strike run through short gamma, with no wall in the way, inside the expected move?

**6/7** - https://x.com/BuildwithPublic/status/2109650318418182569 (14:20:05 UTC)
> Execution: cheap, slightly OTM 0DTE contracts at $1.00-1.50. A limit order goes in at the ask plus one tick, then a resting 2x take-profit.
>
> I pick bull or bear. The engine only takes the prints on my side, with hard caps on trades and premium.

**7/7** - https://x.com/BuildwithPublic/status/2109650320746074416 (14:20:06 UTC)
> This is the point of an API brokerage. The greeks, the chain and the order ticket are all endpoints.
> Anything a flow terminal shows you, you can build yourself, then wire straight to execution.
>
> Start building and DM me if you need help 🦾: https://public.com/api
(Link card: "Public Trading API - Automate Stocks & Options Orders": "Access the Public Trading API to automate stock and ETF options trades with real-time execution, no commissions, and rebates. Get full read/write access to portfolio data, pricing, and order controls.")

Author's substantive follow-ups in replies (author's voice, not the chain):
- https://x.com/BuildwithPublic/status/2109795640771514462 : "agree inference can't rebuild true dealer book, it doesn't try to. but print detector with a scorecard is workable- every signal is graded on whether the contract hit 2×/3× within 30 min.if reads were backwards scorecard shows. numbers to come"
- https://x.com/BuildwithPublic/status/2109749311076504015 : "go to https://public.com open an account -> pull an API key-> open access to all builders https://public.com/api/docs 🦾"
- https://x.com/BuildwithPublic/status/2109699853425230332 : "thanks, paper trading is on the way- hang tight. we have hist data open sourced on our API right now"
- https://x.com/BuildwithPublic/status/2109790563608531213 : (re websocket prices + futures) "Both coming - stay tuned 🦾"
- https://x.com/BuildwithPublic/status/2109666904491106710 : "no overnight SPX data yet- been hearing this request a lot"
- https://x.com/BuildwithPublic/status/2109665779251912786 and https://x.com/BuildwithPublic/status/2109667087975080179 : (re cost) "Open sourced on @public" / "all open sourced on API"
- https://x.com/BuildwithPublic/status/2109666998128894061 : (re "So build gex on top?") "sure you can do this"
- https://x.com/BuildwithPublic/status/2109668894030082309 : "have OCO been working for you? Whats biggest needlemover that you'd want to see next?"
- https://x.com/BuildwithPublic/status/2109680154163532286 : "What's highest value on mkt internals list you need?"

---

## 2. Media (exhaustive)

### 2.1 Root image: TZU SPX 0DTE desk (1919x989 px, dark theme)
URL: https://pbs.twimg.com/media/HUb60FBXwAAnkMk.jpg (post: https://x.com/BuildwithPublic/status/2109650307680534890/photo/1)

**Global style:** near-black background, panels as slightly lighter dark-grey rounded cards with thin borders, monospace numerics, small-caps grey labels. Color semantics: green = calls / positive / bullish; red/pink = puts / negative; orange = put bubbles on chart; blue = long dealer gamma; magenta/pink = short dealer gamma; yellow/amber = highlights (0γ value, burst multiples, block size). Snapshot moment: 10:15:30 ET, 2026-10-12, SPX 7,806.89.

**Layout grid (top to bottom):**
- Row A: top app bar
- Row B: instrument/context strip
- Row C: 8 KPI cards in a single row
- Row D (main, ~55% height): 4 columns: [SPX price chart + flow subpanel] (~50% width) | [DEALERS ladder] (~15%) | [PRINT TAPE over EXECUTION] (~17%) | [PRINT RULES, DEALER MODEL, RISK] (~18%)
- Row E (bottom): "SPX 0DTE PRINTS" table strip with paper position/P&L cards at right

**Row A - top bar**
- Left: logo (stacked white bars glyph) + "TZU" wordmark, subtitle "SYSTEMATIC OPTIONS".
- Underlying toggle pill: **SPX** (selected, white pill) | SPY.
- Center ticker (single-line latest-alert marquee): "**BLOCK** 7835C **665** @ 0.68 3× normal · 10:14:59 ET · 1m ago" ("BLOCK" in green; size bold).
- Right: "Sound on" button (audio alerts), badge "SPX · PAPER" (indigo/purple pill = mode indicator), "ET 10:15:30" live clock, red pill button "Stop SPX" (kill switch for the engine).

**Row B - context strip** (label above, value below)
- "SPX" + green live dot: **7,806.89**
- "EXPIRY": SPXW 2026-10-12 0DTE
- "ENTRY WINDOWS ET": 10:00-12:59 · 3:45-3:55 · open (green = window currently open)
- "CHEAP CALLS $1.00-1.50": 7830 1.20 (green) = strikes whose ask is in the buy band
- "CHEAP PUTS $1.00-1.50": 7770 1.10 · 7775 1.35 (red)
- Right "ENGINE": "hunting · history 84 strikes · 0 bar prints" (engine state; how many strikes have per-minute history; prints flagged in the current bar)

**Row C - 8 KPI cards** (label / big value / caption)
1. DAY NET · PAPER / **+$0.00** / "0/6 trades · (dash) win · cap 0%" (P&L, trades used vs max, win rate, % of premium cap used)
2. GAMMA REGIME / **LONG γ** (green) / "dealers fade · pin"
3. NET DEALER Γ / **+$1.53B** / "$ hedged per 1% move"
4. 0Γ FLIP / **7688** / "SPX above by 119 pts" (7806.89 - 7688 = 118.9, consistent)
5. WALLS / **7770 / 7860** / "accel 7800" (put wall / call wall / accelerant strike)
6. CHARM FLOW / **+$372.7M/h** (green) / "dealers buying into close"
7. NET DRIFT / **-$2.6M** (red) / "bought - sold · 5m -$744k" (session net customer premium bought minus sold; last 5 min)
8. EXPECTED MOVE / **±16** / "ATM IV 179.0% · -118.4 today" (points to 4:00 settle from ATM IV; IV change today, units unclear)

**Row D col 1 - price chart panel**
- Title: "SPX · TICK  15 reads per bar · green = calls, orange = puts · 2026-10-12". Timeframe pills: **Tick** (selected) | 1m | 5m | 15m | 1h. ("15 reads per bar" = each tick bar aggregates 15 one-second polls.)
- Plot: dark navy background, thin white OHLC-style bars, y-axis right side 7,800 to 7,825 in 5-pt gridlines. Session shown ~9:30 to 10:14 ET. Price drifts from ~7,817 high area down to ~7,804, last 7,806.89 marked by crosshair ring and grey price tag "7,806.89".
- Right-edge green tag "▲1.7k 7,825.00" at the top (appears to mark the 7825C 1.7k block/strike level, or the chart top bound with a flow annotation; ambiguous).
- **Flow bubbles** overlaid at the time+price where each flagged print hit: orange = put prints, green = call prints; bubble radius scales with premium; white ring on the most recent / selected bubbles. Label format: "<strike><C/P> <size> · <edge>/6", seen: "7790P 946 · 1/6", "7785P 900 · 1/6", "7790P 900 · 1/6", "7825C 1.7k · 2/6", "7785P 906 · 1/6". Clusters around 09:46-09:48 and 10:08-10:14.
- Dashed magenta horizontal line labeled "ACCEL 7800" (accelerant strike level) near bottom of plot.
- **Sub-panel** under chart (shared time axis 9:30, 09:34 ... 10:13):
  - Legend lines: "VOL SPX · max 10,504" (grey histogram, per-bar volume), "0DTE FLOW · max bar 3,091" (teal label; colored spikes), "0DTE NET BOUGHT -$2.6M calls - puts" (pink/red step line, cumulative net premium, ending tag "-2.6M" at right).
  - Histogram bars grey with highlighted spikes: green (call flow) and pink/red (put flow), spike labels 2.6k (~09:47), 2.2k (~09:48), 1.0k (~10:02), 1.4k (~10:05), 2.1k (~10:07), 3.1k (~10:09, green, tallest).

**Row D col 2 - DEALERS ladder**
- Header "DEALERS" with tab pills: **γ** (selected) | Ch (charm) | Va (vanna) | Buy (customer buy flow, assumed).
- Column header: "SHORT · $ / 1%" (left) | STRIKE (center) | LONG (right). Horizontal diverging bar chart per strike, 5-pt strikes from 7900 (top) to 7710 (bottom).
- Right blue glowing bars = dealer long gamma at that strike; left pink/magenta glowing bars = dealer short gamma.
- Labeled strikes: **7860 CALL WALL** (strike text blue, blue tag), **7800 ACCEL** (strike text red, red tag), **7770 PUT WALL** (blue text + tag). Spot marker tag "SPX 7806.9" pinned at left near 7810/7805.
- Pattern: 7900-7815 nearly all long (blue), largest around 7850-7840 and 7830; 7835 is the lone short bar above spot. 7810 down to 7710 almost all short (pink), large at 7810, 7800, 7780, 7750; 7770 and 7765 small long (put wall).
- Footer: "Net γ +$1.53B" (blue), "Charm +$372.7M/h", "0γ 7688" (yellow), "EM ±16".

**Row D col 3 top - PRINT TAPE**
- Title "PRINT TAPE  1.0s reads". Columns: ET | STRIKE (green calls, red puts) | badge (BLK for block; green badge on calls, pink badge on puts) | SIZE | PX | × (multiple of normal minute volume) | EDGE (0-6). Newest row highlighted (green tint for call row, red tint for put row). Scrollbar.
- Rows visible (verbatim):
  - 10:14:59 7835C BLK 665 0.68 3 0
  - 10:14:50 7760P 678 0.88 5 1
  - 10:12:14 7755P 685 0.78 8 1
  - 10:12:08 7750P 390 0.68 5 1
  - 10:11:50 7760P 792 0.98 8 1
  - 10:10:26 7785P 906 2.83 6 1
  - 10:10:06 7835C BLK 1,367 0.68 5 (edge cut off)
  - 10:10:06 7825C BLK 1,724 1.53 7 (edge cut off, looks like 2, matching chart label "7825C 1.7k · 2/6")
  - 10:09:14 7755P 445 0.63 6 1
  - 10:08:26 7765P BLK 926 0.90 8 1
  - 10:08:20 7775P 800 1.42 5 1
  - 10:08:20 7780P 873 1.83 6 1
  - 10:08:18 7760P 413 0.78 5 1
  - 10:07:54 7750P 351 0.53 ... (partially visible)

**Row D col 3 bottom - EXECUTION (paper)**
- Title "EXECUTION paper". Segmented toggle: **Bull · calls** (selected) | Bear · puts.
- Caption: "Trades call prints only. Put prints are logged, never bought."
- "Min edge" segmented: Any | 2+ | **3+** (selected) | 4+.
- "Contracts per print" stepper: [-] 1 [+].
- Wide button "Arm live orders" (switch paper to live).
- Status line: "flat · hunting for a print".

**Row D col 4 - PRINT RULES** (label left, rule right; strategy config, displayed read-only)
- Watch: OTM mid $0.50-3.00
- Block: ≥ 500 in one read
- Burst: ≥ 300 in 60s at ≥ 5×
- Skip if: already up > 100% in 2m
- Buy: OTM ≤ 0.8% · ask $1.00-1.50
- Fill: ask + 1 tick · 6s or cancel
- Exit: sell 2× resting · no stop · hold
- Edge: take only 3+/6
- 4:00 PM: cash-settles at intrinsic

**Row D col 4 - DEALER MODEL (estimate)**
- Toggle: **OI + tape** (selected) | Tape only.
- Text verbatim: "Dealer = long call OI, short put OI, minus what customers bought today (each print signed at the bid or ask). Greeks Black-Scholes to the 4:00 settle."
- "Magenta: dealers short γ, moves accelerate. Blue: long γ, price pins. Edge 0-6 scores a print's path to its strike." (Magenta and Blue words colored.)

**Row D col 4 - RISK**
- Trades: 0 of 6
- Premium loss cap: $0.00 of $600
- Cooldown: 3m between entries
- Fees: $1.40 / contract
- Side: Bull · calls only
- Orders: paper (arm to go live)

**Row E - SPX 0DTE PRINTS strip**
- Header line: "SPX 0DTE PRINTS  paper · hunting · entries 10:00-12:59 · 3:45-3:55 ET · buy $1.00-1.50 OTM · no stop · sell 2× · poll 1s · history 84 strikes · 0 bar prints". Right controls: take-profit multiple segmented **1.5× | 2× (selected) | 3×**, and engine **On (selected) | Off**.
- Left table "CHEAP OTM" (candidate contracts) columns: CHEAP OTM | BID×ASK | 60S | NORM | BURST | BLOCK
  - 7835C 0.75×0.80 646 253 2.5× **575** (575 highlighted amber = block threshold crossed)
  - 7780P 1.65×1.70 432 173 2.5× 199
  - 7755P 0.60×0.65 238 99 2.4× 201
  (60S = contracts traded in last 60s; NORM = normal per-minute volume baseline; BURST = 60S/NORM; BLOCK = largest single-read volume. Interpretation inferred from values: 646/253 = 2.55.)
- Middle table (signal log) columns: TIME | PRINT | SIZE | BURST | MID | HIT | RESULT
  - 10:14:59 7835C block 665 3× 0.68 bid "edge 0/6 below 3"
  - 10:14:50 7760P burst 678 5× 0.88 mid "your side is Bull"
  - 10:12:14 7755P burst 685 8× 0.78 bid "your side is Bull"
  (HIT = where the print executed vs quote: bid/mid/ask; RESULT = why the engine did or did not trade it.)
- Right cards: "PAPER POSITION: flat · SPX 7,806.89"; "PAPER TODAY: +0.00 (green), 0/6 trades · 0W 0L".

**Notable details/quirks in the screenshot**
- The 7835C 665 "BLOCK" alert shows HIT = bid (customer likely sold) but is still colored green as a call; the engine rejected it on edge (0/6), not on side. Colors encode call/put, not buy/sell.
- Bubble labels and tape agree on edge (e.g., 7825C 1.7k 2/6).
- Cheap-calls strip shows 7830 at 1.20 while tape shows 7835C at 0.68-0.80 (below the $1.00 floor), so it is "watched" (mid $0.50-3.00) but not "buyable".
- Take-profit toggle offers 1.5×/2×/3× though the thread says 2× (2× selected).
- Paper mode, 0 trades taken at snapshot time; no live results shown.

### 2.2 Reply images (not the author's, but directly relevant reference UIs)
Posted by @BlippiToad (Zoey) in https://x.com/BlippiToad/status/2109784176518144402 ("love this!! @public is letting rats out of the lab. This is why developer-first platforms matter!!"). Account names blacked out in the screenshots.

**Image A** https://pbs.twimg.com/media/HUdw1GwXUAAl2la.jpg (1865x930) - a TSLA GEX dashboard
- Top nav: Dashboard | Screener | Watchlists | Earnings | Screens; right "Auto-refresh" toggle (Off), "Sign out".
- Ticker input "TSLA" + Load; quick chips SPX, SPY, QQQ, IWM, NVDA, TSLA (selected), AAPL; "Recent" chips with x: GS, AAOI, C, ASTS, UNH, RKLB, AMZN, CRWV; "Clear all".
- Status: "TSLA 377.64 · updated 17:48:42 · 20-day realized vol: 30% · auto-refresh off" + Refresh button.
- Green-bordered banner "Market thesis - Oct 12: TSLA: long gamma, so dealer hedging dampens moves. Price is above the 377.50 call wall, which can act as support on a pullback, with magnet pull toward the 377.50 king node. Charm: as time passes, hedging implies dealers buy about $1,953.5M of delta per day into this expiry." (auto-generated narrative).
- Left KPI grid (2 cols): Spot 377.64; Regime Long gamma (+$24.5M net); Gamma flip 381.24 (1% from spot); Call wall 377.50; Put wall 377.50; King node 377.50 (+$22M); Implied move ±0.15% (ATM straddle 0.57); Pin score 53 (moderate); Net vanna +$0.4M (dealer delta per vol pt); Net charm -$1,953.5M (dealer delta per day).
- Expiry tabs: Oct 12 · 0DTE (selected), Oct 14 · 2d, Oct 16 · 4d, Oct 19 · 7d, Oct 23 · 11d, ... (horizontal scroll).
- Range pills ±3% | ±10% (selected) | ±20%; Show pills Gamma (selected) | Vanna | Charm.
- Chart "Net gamma by strike · Oct 12 ($ per 1% move)": horizontal bars, blue positive / red negative, strikes 415 to 372.5, dashed lines labeled Flip, Call, Spot.
- Center: "Net gamma heatmap - strike × expiration". Legend: Positive gamma (blue), Negative gamma (red), ★ King node (amber cells), "dark strike badge = nearest spot · click a date to chart it". Rows = strikes 420 down to 340 with % distance from spot; columns = expiries Oct 12 (0DTE), Oct 14, Oct 16, Oct 19, Oct 23, Oct 30, Nov 6, Nov 13, Nov 20, Dec 18, Jan 15 '27, Feb 19 '27, Mar 19 '27, and further (days-to-expiry under each). Cell = $ gamma (e.g., $2.9M, -$1.6M), shaded by sign and magnitude, king-node cells amber with ★.
- Right "Live event feed": subtitle "Unusual Vol/OI, king node and wall moves, and A-grade wheel setups. Volume is the latest session's." Dropdown "All events", "Clear tape". Cards with colored pill tags "FULL SIZE CALL" (blue) / "HEAVY PUT FLOW" (red), timestamp 17:48:42, title e.g. "TSLA $380 Call · Oct 12 (+0.6% OTM)", body e.g. "Heavy near-the-money call volume. Bullish directional flow. 103.9x Vol/OI (221,876 contracts traded vs 2,135 OI)." Others: $377.50 Put 85.7x (155,238 vs 1,811 OI); $380 Put (+0.6% ITM) 26.6x (126,702 vs 4,769); $382.50 Call 39.1x (117,287 vs 3,002); $385 Call 12.1x (59,912 vs 4,969); $375 Put 18.2x (59,151 vs 3,256).

**Image B** https://pbs.twimg.com/media/HUdycVjXUAAzkK9.jpg (1877x877) - Watchlists page
- Title "Watchlists": "Price, volume and trend for your tickers. Cells shaded amber are within 5% of that moving average, the stocks sitting on a line you trade around."
- Controls: New watchlist name + Add list; "Dynamic list..." dropdown; Update lists; "Lists updated 07:43:50"; Near MA 1% | 2% | 3% | 5% (selected); Columns 1 | 2 (selected) | 3; "Near 50/200 only (91)"; "Closest first"; Import CSV; Export CSV.
- Status: "market closed, showing last close · fetched 17:52:50 · auto-refresh off" + Refresh.
- Two list cards: "Earnings this week (30), Finviz screen, updated 17:51:41" and "Earnings next week (65), Finviz screen, updated 17:52:13". Columns: Symbol | Price | Change ($ and %) | Rel vol (e.g., 0.6×, with avg volume) | RSI | EMA 8 | EMA 20 | EMA 50 | EMA 200 (each value with % distance, amber shaded if within threshold) | Near 50/200 (%). Tickers include JPM, JNJ, BAC, UNH, MS, GS, WFC, C, BLK, SCHW, PLD, PGR, BNY, USB, PNC, MRSH, TRV, FAST, STT, IBKR, MTB, TFC, CFG, RF (this week); TSLA, LRCX, PG, PM, NFLX, RTX, IBM, AXP, ABT, T, ISRG, WDC, BX, NEM, COF, LMT, NOC, NDAQ, CME, MCD, GM, WAB, MSCI, CBRE (next week).
- Right detail drawer for "C" 131.92 +2.28 (+1.8%): tabs "GEX heatmap" | "Price chart" (selected), "Open in GEX dashboard ->"; TradingView daily candle chart with EMAs; 52w low 95.00 / high 147.95; table Average/Value/Price vs: EMA 8 129.67 (+1.7%), EMA 20 130.87, EMA 50 132.51, EMA 200 124.48, SMA 50 133.62, SMA 200 126.30; Volume 9.2M (0.9× avg), RSI(14) 51.2, Day range 129.63-132.00, Prev close 129.64, From 52w high -10.8%. Footer "Chart by TradingView. Averages are computed from Public.com daily bars."

### 2.3 Quote post (text only, no media)
@doc_mcgraw (Doc aka Trader McGraw) https://x.com/doc_mcgraw/status/2109964739589669263 - text in section 5.

---

## 3. Feature inventory (TZU, as shown + described)

| # | Feature / widget | Data shown | Filters / controls | Sort | Alerts / states |
|---|---|---|---|---|---|
| F1 | Top bar | Logo, underlying, latest alert marquee, mode badge, ET clock | SPX/SPY toggle; Sound on/off; Stop (kill switch) | n/a | Audio alert on new flagged print; marquee shows last alert + age ("1m ago") |
| F2 | Context strip | Spot, expiry, entry windows + open/closed, cheapest calls/puts in buy band, engine state | none shown | Cheap strikes listed nearest-first (assumed) | Window open state colored |
| F3 | KPI cards (8) | Day P&L, trades used/max, win %, cap used %; gamma regime; net dealer gamma $/1%; 0γ flip + distance; put/call wall + accel; charm flow $/h; net drift (session + 5m); expected move + ATM IV + IV change | none | n/a | Color by sign; regime text (pin vs accelerate) |
| F4 | Price chart w/ flow bubbles | SPX tick bars (15 reads/bar) and 1m/5m/15m/1h; bubbles at time+price of flagged prints, sized by premium, colored call/put, labeled strike/size/edge; accel line | Timeframe pills | Time | New bubble per flagged print; ring = latest/selected |
| F5 | Flow subpanel | Per-bar volume; 0DTE flow spikes (calls green, puts red) with size labels; cumulative 0DTE net bought (calls - puts, $) line | none shown | Time | Spike labels on big bars |
| F6 | DEALERS ladder | Per-strike dealer exposure $/1%, short left (magenta), long right (blue); spot marker; call wall / put wall / accel tags; footer net γ, charm, 0γ, EM | Tabs γ / Charm / Vanna / Buy | Strike descending | Wall/accel tags move as model updates |
| F7 | PRINT TAPE | Flagged prints: time, strike, BLK badge, size, price, × normal, edge | none shown | Newest first | Row tint on newest; BLK badge |
| F8 | EXECUTION | Side, min edge, contracts/print, arm live, status | Bull calls / Bear puts; Min edge Any/2+/3+/4+; qty stepper; Arm live | n/a | Status text (flat/hunting/in position) |
| F9 | PRINT RULES | Strategy config text (watch band, block, burst, skip, buy, fill, exit, edge, settle) | Read-only here (likely config) | n/a | n/a |
| F10 | DEALER MODEL | Model description + mode | OI + tape / Tape only | n/a | n/a |
| F11 | RISK | Trades x of max, premium loss used of cap, cooldown, fees, side, order mode | none shown (config) | n/a | Blocks entries when caps hit (implied) |
| F12 | 0DTE PRINTS: CHEAP OTM table | Candidate contracts: bid×ask, 60s vol, normal/min, burst ×, max single-read block | none | Probably by burst or block | Amber highlight when threshold crossed |
| F13 | 0DTE PRINTS: signal log | Each signal: time, print type (block/burst), size, burst ×, mid, hit (bid/mid/ask), RESULT reason | Take-profit 1.5×/2×/3×; engine On/Off | Newest first | Reason codes e.g. "edge 0/6 below 3", "your side is Bull" (rejected as other side) |
| F14 | Paper position / Paper today | Position, spot; P&L, trades, W/L | none | n/a | n/a |
| F15 | Execution engine (headless) | Orders: limit at ask + 1 tick, 6s or cancel; resting sell at 2× fill; no stop; hold to settle | Caps: 6 trades, $600 premium loss, 3m cooldown | n/a | Sub-second order fire claimed |
| F16 | Scorecard (announced, not shown) | Every signal graded on whether contract hit 2×/3× within 30 min | n/a | n/a | "numbers to come" |

Signal logic as stated (to implement):
- Poll every 1s ("reads"); full SPXW 0DTE chain every few seconds; index quote; 1-min bars.
- Watch universe: OTM contracts with mid $0.50-3.00.
- Block: ≥500 contracts traded in one read (thread also says "a single block hits at the ask").
- Burst: ≥300 contracts in trailing 60s at ≥5× normal minute (thread says "10x+ its normal minute"; screenshot rule says ≥5×; tape × column shows 3 to 8).
- Skip if contract already up >100% in last 2 min.
- Sign each print: ask-lift = customer buy, bid-hit = customer sell, mid = ambiguous.
- Edge score 0-6 from the path SPX -> strike: runs through short gamma, no wall in the way, inside expected move (3 criteria described; the rest unknown).
- Side filter: only prints on the chosen side (Bull = calls, Bear = puts).
- Entry: OTM ≤0.8% from spot, ask $1.00-1.50, edge ≥ min (3+), inside entry windows 10:00-12:59 and 15:45-15:55 ET, under caps and cooldown.
- Exit: resting limit sell at 2× (configurable 1.5/2/3×), no stop, else hold to 4:00 PM cash settlement at intrinsic.

Dealer model as stated:
- Baseline: dealers long call OI and short put OI (classic naive GEX assumption).
- Adjust intraday: subtract customer net buying per strike (from signed prints) to update dealer inventory ("OI + tape" mode); "Tape only" mode uses only today's signed flow.
- Greeks: Black-Scholes to the 4:00 PM settle (own computation for gamma, charm, vanna; API supplies delta, gamma, IV).
- Derived levels: net dealer gamma ($ hedged per 1% move), 0γ flip (spot level where net gamma changes sign), call wall (largest long-gamma strike above spot, 7860 here), put wall (7770), accelerant strike (largest short-gamma strike near spot, 7800), charm flow ($/hour dealer delta change), expected move (from ATM IV to settle).

---

## 4. Data requirements

### 4.1 Feeds
| Feed | Fields | Cadence | Used by | Public API per thread? |
|---|---|---|---|---|
| Index quote (SPX; SPY optional) | last, timestamp, day O/H/L | 1s | F2, F3, F4, edge, EM | Yes ("SPX index quotes") |
| Option chain snapshot, 0DTE SPXW (all strikes) | OCC symbol, strike, C/P, expiry, bid, ask, bid/ask size, last, mid, cumulative day volume, open interest, IV, delta, gamma (theta/vega optional) | 1s polls ("reads"), full chain "every few seconds" | F4-F7, F12, dealer model | Yes ("full 0DTE SPXW chain with delta, gamma, IV and open interest on every strike") |
| 1-minute bars | OHLCV for SPX and per contract (for "normal minute" baseline) | 1m | F4 bars, burst baseline (NORM) | Yes ("plus 1-minute bars") |
| Historical bars | daily/minute history to build per-strike normal-volume baseline ("history 84 strikes") | startup + daily | NORM column | Author says historical data is available on the API |
| Trade prints (time & sales) | per-trade price, size, exchange, condition codes (sweep, multi-leg, ISO), NBBO at trade | tick | True block/sweep detection | **Not stated.** TZU appears to infer prints from per-poll volume deltas plus last-vs-quote comparison. A real feed (OPRA / CBOE) is needed for sweeps, multi-leg filtering and accurate signing |
| Dark pool / equity blocks | n/a | n/a | Not part of TZU | Not mentioned; out of scope unless added |
| Account / positions / orders | buying power, positions, open orders, fills, P&L | event / 1s | F8, F11, F14 | Yes (order ticket endpoints; OCO mentioned in replies) |
| Order entry | limit buy, cancel-after-6s, resting limit sell (OCO available) | on signal | F15 | Yes |
| Rates / dividend yield | r, q for Black-Scholes | daily | Greeks to settle | Not mentioned (use T-bill rate, SPX div yield) |
| Market internals (requested, not in product) | TICK, ADD, VOLD, ADV/DECL, UVOL/DVOL for NYSE + NASDAQ | 1s-1m | Requested by @tradeseekers | Not available yet |
| Websocket streaming | quotes/trades push | stream | Would replace polling | "coming" |

### 4.2 Derived fields (compute locally)
- Per contract per read: dVol = cumVol(t) - cumVol(t-1); print price ≈ last; hit side = compare last to bid/ask/mid at read; premium = dVol × price × 100.
- NORM: baseline contracts/minute per strike (rolling history); 60S volume; BURST = 60S / NORM; BLOCK = max single-read dVol.
- Signed customer flow per strike: + at ask, - at bid, 0/ignored at mid; cumulative net bought (calls - puts) in $; NET DRIFT session and 5-min.
- Dealer inventory per strike: (+call OI, -put OI) baseline minus signed customer net contracts (mode dependent).
- Greeks (Black-Scholes, T = time to 4:00 PM ET): gamma, charm (dDelta/dt), vanna (dDelta/dIV); dealer GEX $/1% = Σ inventory × gamma × S² × 0.01 × 100; charm flow $/h; vanna $/vol pt.
- Levels: 0γ flip (re-price gamma across a spot grid, find sign change), call wall, put wall, accel strike, gamma regime (sign of net GEX at spot).
- Expected move: S × ATM IV × sqrt(T to settle) (±16 pts at 179% annualized IV with ~5.75h left is consistent).
- Edge 0-6 per print: path checks between spot and strike (short-gamma share of strikes in path, wall blocking, strike within EM, plus 3 undocumented criteria).
- Strategy state: trades used, premium loss used, cooldown timer, position, take-profit order id, P&L, W/L.
- Scorecard: for each signal, max price within 30 min vs signal price, hit 2× / 3× flags, hit rates by edge bucket and side.

### 4.3 Not needed by TZU as shown (but expected in a generic "flow terminal")
Sweeps (multi-exchange ISO detection), multi-leg/spread detection, dark pool prints, next-day OI change confirmation, multi-ticker scanning, multi-expiry heatmaps (Image A has these), news. Add only if scope is broader than the SPX 0DTE desk.

---

## 5. Notable replies (substantive)

**Critiques**
- @foundryfutures https://x.com/foundryfutures/status/2109791781399519729 (reply to 4/7): "Unfortunately, this isn't actual CBOE trade data with proper signing and tagging. Inference cannot rebuild an accurate model, period. It cannot reconcile multi-leg spreads and can often completely reverse the positioning or get it wrong." Author conceded (scorecard reply above). Follow-up https://x.com/foundryfutures/status/2109801016342491254: "'inference can't rebuild true dealer book, it doesn't try to.' - Then your post shouldn't infer that it does. Need to double check your AI copy before blindly posting."
- @btcpathobear https://x.com/btcpathobear/status/2109863603687903429: "How are you accurately doing this without dealer signed positioning?" (no answer).
- Quote by @doc_mcgraw https://x.com/doc_mcgraw/status/2109964739589669263: "Naive gets a lot of intraday trading back-and-forth volume so this stuff kind of helps. I used to use it before we had better data but these days how much accuracy you have going in is going to be reflective of your results. Good luck to you and thanks for making it public. I prefer to use something that has real CBOE tagged data. @OptionsDepth is my go-to"

**Feature requests / questions**
- @tradeseekers https://x.com/tradeseekers/status/2109678826561130700: "So sexy, if I could get intraday market internals on this I could completely kill off a handful of subscriptions." Then https://x.com/tradeseekers/status/2109697003886379089: "I used tick, add, vold daily, specifically I would rather have adv, decl, uvol, dvol. NYSE and if possible NASDAQ."
- @phantomphreak88 https://x.com/phantomphreak88/status/2109698481547854067: "OCO has been solid... Paper accounts and historical stock data would move the needle by a lot. I currently have to pay for Alpaca $100/mo for this. Id gladly pay more in my Public subscription for this." And https://x.com/phantomphreak88/status/2109777667587530967: "any roadmap plans for websocket prices? And overall, for futures trading?" (author: both coming)
- @jcomfin https://x.com/jcomfin/status/2109664117703442735: "is the GTH SPX options data available?" (author: no overnight SPX data yet). Also https://x.com/jcomfin/status/2109663826367201760: "Is there a charge to access data via API?"
- @AndomMerih https://x.com/AndomMerih/status/2109666338381742081: "So build gex on top?" and https://x.com/AndomMerih/status/2109666507257159974 "Is there additional payment for this feature?"
- Open-source / layout requests: @SPXChintan https://x.com/SPXChintan/status/2109680990725833097 "is your version of dashboard open source by any chance?"; @JaimeVI24 https://x.com/JaimeVI24/status/2109728349908308474 "Trying to rebuild this exact layout"; @N_I_K_Capital https://x.com/N_I_K_Capital/status/2109940575151792335 "are you able to share a Git of this layout?"; @OnlyStockCharts https://x.com/OnlyStockCharts/status/2109746476448387580 "And we can get this how?"; @2020_kidd https://x.com/2020_kidd/status/2109669751899185611 asked where to load the script. No repo was shared.
- @BlippiToad https://x.com/BlippiToad/status/2109784176518144402: posted their own GEX dashboard + watchlist screenshots (section 2.2), useful reference for multi-expiry heatmap, king node, pin score, auto-generated thesis, and Vol/OI event feed.
- Non-substantive praise: @inversewarrenTP, @AviHarkishun, @BelangerTrading, @BradleyDHobbs, @_TraderLarry, @malling ("Banger"), @phantomphreak88 ("Very cool").

---

## 6. Open questions / ambiguities

1. **Print source:** Does Public expose trade-level time & sales for options, or is TZU inferring prints from 1s cumulative-volume deltas? Screenshot language ("1.0s reads", "≥500 in one read", "15 reads per bar") strongly suggests inference. That limits signing accuracy, cannot separate sweeps vs blocks vs multi-leg spreads, and can misattribute aggregated trades.
2. **Edge score:** 0-6, but only 3 criteria are described (path through short gamma, no wall in the way, inside expected move). The other points and their weights are unknown. Six binary checks or weighted?
3. **Burst threshold conflict:** thread says 10x+ normal minute; rules panel says ≥300 in 60s at ≥5×.
4. **Block definition conflict:** thread says "a single block hits at the ask"; rules say ≥500 in one read, and the 7835C BLOCK in the screenshot hit at the bid. Is side required for a block flag or only for trading?
5. **What gets bought:** the flagged contract itself (if inside the $1.00-1.50 band and ≤0.8% OTM) or the cheapest in-band contract on the same side ("CHEAP CALLS 7830 1.20")? Unclear.
6. **"Buy" tab** in DEALERS ladder: customer net buying per strike? Not explained.
7. **Dealer model sign convention:** "Dealer = long call OI, short put OI, minus what customers bought today" is the naive GEX assumption; critics note it can invert. How OI (a prior-day number) is combined intraday and how mid prints are handled is unspecified.
8. **Accelerant strike** definition (largest short-gamma strike near spot? nearest short-gamma cluster in direction of travel?) not given; here 7800.
9. **Charm flow +$372.7M/h** alongside dealer long gamma: units and sign convention (dealer delta change per hour in $ notional) to confirm.
10. **"-118.4 today"** under expected move: IV change in vol points? Unclear.
11. **"▲1.7k 7,825.00" tag** on the chart's right axis: flow annotation or chart top bound?
12. **Entry windows** 10:00-12:59 and 15:45-15:55 ET: rationale not given; configurable?
13. **No stop loss**, hold to settlement: max loss per trade = premium; caps $600 and 6 trades. Per day or per session?
14. **Fees $1.40/contract:** Public advertises no commissions and rebates; confirm actual SPX exchange/regulatory fees and rebate treatment.
15. **Latency claim** "fires a real order in under a second": measured from detection to order ack? With 1s polling the real lag from trade to order is 1-2s+.
16. **Scorecard numbers** promised ("numbers to come"): none published; no backtest or live results shown. Screenshot is paper, 0 trades.
17. **Pricing/access:** author says data is "open sourced on API" and "open access to all builders"; whether real-time options data is free or entitlement-gated is unstated.
18. **No code shared** despite several requests; the layout must be rebuilt from the screenshot.
19. **SPY toggle:** SPY options are American-style and physically settled; whether the same model/rules apply is unknown.
20. **Second quote post** counted in metrics (quote_count 2) was not retrievable (likely deleted/protected, or an edit-history artifact); only @doc_mcgraw's quote was found.

---

## Suggested build outline (derived, for implementation)
- Ingest service: 1s poller for SPX quote + SPXW 0DTE chain (later websocket), stores per-read snapshots; derives dVol prints, hit side, premium; maintains per-strike NORM from history.
- Model service: dealer inventory (OI + tape / tape only), BS greeks to 16:00 ET, GEX/charm/vanna by strike, flip/walls/accel, EM, regime; recompute each read.
- Signal service: block/burst detection with watch band and skip rule; edge score; side filter; emits signal records with RESULT reason codes; scorecard tracker (2×/3× within 30 min).
- Execution service: risk gate (windows, caps, cooldown, side, min edge), limit buy ask+1 tick cancel after 6s, on fill place resting sell at TP multiple; paper and live modes; kill switch.
- UI (single dark screen as in 2.1): top bar, context strip, 8 KPI cards, tick chart with premium-sized bubbles + flow subpanel, dealers ladder with γ/Ch/Va/Buy tabs, print tape, execution controls, rules, model, risk, bottom candidates + signal log + paper P&L; audio alerts.
- Optional v2 (from replies/reference UI): multi-ticker, multi-expiry gamma heatmap with king node, pin score, auto-generated market thesis, Vol/OI event feed, market internals, real CBOE/OPRA tagged trade feed for signing and spread filtering.