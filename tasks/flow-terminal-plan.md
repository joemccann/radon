# Flow Terminal (SPX 0DTE desk): Build Plan

> **STATUS (2026-10-13): PROPOSED.** Nothing here is built. Source material:
> the @BuildwithPublic "TZU" thread brief (`docs/design/flow-terminal/source-brief.md`,
> gathered by GrokBot from X on 2026-10-13), the reference screenshot
> (`docs/design/flow-terminal/before-tzu.jpg`), the no-slop review and direction
> plate (`docs/reviews/2026-10-13-no-slop-flow-terminal.md`,
> `docs/design/flow-terminal/index.html`), and a code survey of this repo.
> Every "exists today" claim below names the file it was read from.

---

## 1. What TZU is, and what Radon keeps

TZU polls the full SPXW 0DTE chain every second from Public's API, infers prints
from per-poll volume deltas, signs them against the quote, rebuilds a naive
dealer book (long call OI, short put OI, less what customers bought today),
prices Black Scholes greeks to the 4:00 settle, derives net gamma / charm /
vanna by strike, the 0γ flip, the call wall, the put wall and an "accelerant"
strike, scores each print 0 to 6 on the path from spot to its strike, and fires
cheap OTM 0DTE limit orders at ask plus one tick with a resting 2× sell and no
stop. The thread's strongest critique (foundryfutures, doc_mcgraw) is correct:
inferred prints cannot be signed reliably or separated from multi leg spreads,
so the dealer map is an estimate at best. The author conceded it and promised a
scorecard that never appeared.

Radon keeps the shape (chain in, dealer map, signed tape, edge score, engine)
and changes four things:

| TZU | Radon | Why |
|---|---|---|
| Prints inferred from volume deltas | UW `option_trades:SPX` websocket (per trade, side tagged at NBBO, premium, multi leg flags); volume delta inference only as a labelled fallback | The critique is right; provenance is shown per print |
| Take profit 2× (gain 1× loss) | Take profit 3× minimum for live orders; 2× stays available in paper for scorecard breadth | Gate 1: gain ≥ 2× loss |
| Fixed $600 / 6 trade caps | `kelly_ticket()` with p from the scorecard's hit rate for that edge bucket, half Kelly, 2.5% bankroll hard cap, plus the daily caps | Gate 3 |
| Edge = options tape only | Edge criterion 6 = dark pool SPY pace agrees with the direction (`docs/evaluation.md` intraday interpolation); required for live arming | Gate 2: flow signal or nothing |

Scorecard first: every signal is graded on whether the contract reached 2× and
3× within 30 minutes, by edge bucket and by print source. The engine does not
arm live until the scorecard has at least five sessions.

**Canonical name everywhere:** `flow-terminal` / `flow_terminal`.
Package `scripts/flow_terminal/`, unit `radon-flow-terminal.service`, tables
`flow_prints`, `flow_dealer_snapshots`, `flow_signals`, `flow_engine_state`,
FastAPI `/flow-terminal/...`, Next `/api/flow-terminal/...`, page
`/flow-terminal` (section `flow-terminal`), components
`web/components/flow-terminal/`.

---

## 2. Gaps the survey found (prerequisites)

1. **SPXW cannot be resolved.** `scripts/clients/contract_resolver.py:102-107`
   pins `SPX` to `tradingClass "SPX"` on CBOE, so neither `ib_chain.py` nor
   `ib_place_order.py` can reach a 0DTE SPXW contract. The relay's
   `optionContract` (`scripts/ib_realtime_server.js:275`) sends SMART with no
   trading class. Fix: add `SPXW` as a root in `INDEX_OPTION_ROOTS`
   (`tradingClass "SPXW"`, exchange CBOE) and let `resolve_option_contract`
   accept an explicit `trading_class` override; `ib_place_order.py` passes it
   through from the order payload. Tests: `test_contract_resolver_spxw.py`.
2. **No prints table.** Highest migration is `0094_record_high_breadth.sql`; the
   new schema is `0095_flow_terminal.sql`.
3. **No 0DTE greeks.** `theta_harvester_scanner.py:249` floors T at 1/365 and
   `gex_scan.py:137` documents UW's 0DTE IV as unstable as T→0. A new pure
   module computes greeks with T in minutes to 16:00 ET and takes the expected
   move from the ATM straddle mid (model free), not from IV.
4. **No sub minute scheduler.** The fastest existing pattern is a 1 minute
   timer; the monitor daemon loops at 30s. The terminal needs a long running
   `Type=notify` service modelled on `cloud/services/radon-monitor.service`.
5. **UW REST cannot be polled at 1s.** `scripts/utils/uw_budget.py` caps 40,000
   calls/day; 1s polling of one endpoint is ~23,400. Chain state comes from IB
   streaming; UW is websocket for prints and 60s REST for cross checks.
6. **IB OI tick never requested.** The relay subscribes `"233,165"`
   (`ib_realtime_server.js:1225`). The terminal's own IB session requests
   `"100,101,106"` (option volume, open interest, implied vol) so OI and the
   model greeks arrive on the same lines.

---

## 3. Data architecture (source priority per root `CLAUDE.md`)

| Series | Primary | Fallback | Cadence | Notes |
|---|---|---|---|---|
| SPX spot | IB `reqMktData` on the SPX index | UW `price:SPX` ws | stream | Same session as the chain |
| SPXW 0DTE chain: bid/ask/last/volume/OI/IV/delta/gamma | IB streaming lines, strikes within spot ± 3 expected moves, re-centred every 5 min | UW `GET /stock/SPX/greeks?expiry=` + `option-contracts` at 60s | stream, snapshot every 1s | ~50 to 90 lines; the all strikes chain tab already streams up to 202 contracts through the relay (`scripts/CLAUDE.md` § Relay subscription admission), so the line budget is known to hold. Measured in M0 |
| Prints | UW ws `option_trades:SPX` (needs Advanced tier; probed in M0) | Volume delta inference from the IB chain, `source = "inferred"` | per trade | Never mix sources in one scorecard bucket |
| Dark pool SPY | UW `GET /darkpool/SPY` via `uw_client.get_darkpool_flow` | none | 60s | Pace vs prior 5 sessions per `docs/evaluation.md` § Intraday Dark Pool Interpolation |
| Dealer exposure cross check | UW `GET /stock/SPX/spot-exposures/{expiry}/strike` (per strike `call/put_{gamma,charm,vanna}_{oi,vol,ask_vol,bid_vol}`, `docs/unusual_whales_api_spec.yaml:14600-14640`, wrapper to add beside `uw_client.get_spot_exposures`) | none | 60s | Radon publishes its own map AND the drift from UW's; the drift is a scorecard column |
| Risk free rate | FRED DFF (same source as `web/lib/useRiskFreeRate.ts`) | 0.0 | daily | |
| Normal per minute volume | Session to date per contract (min 10 bars), else chain median | | 1m | Seeds the burst multiple |

Robinhood serves NBBO/last only (root `CLAUDE.md` § Data Source Priority) and
has no greeks, OI or tape, so it is not on this path. Yahoo is not on this path.

Confidence: IB line budget high (the relay streams a 202 contract chain today);
UW websocket entitlement unknown until M0; UW spot exposure field names high
(read from the spec).

---

## 4. Services

### 4.1 `scripts/flow_terminal/` (Python)

```
scripts/flow_terminal/
  __init__.py
  greeks.py        bs_greeks(S, K, T_minutes, r, sigma, right) -> delta, gamma, vanna, charm   [pure]
  dealer.py        inventory(oi, signed_flow, mode), exposures_by_strike(), levels()           [pure]
  detector.py      PrintDetector: block / burst / skip rules, NORM baseline                    [pure]
  edge.py          edge_score(print, state) -> 0..6 with per criterion booleans                [pure]
  sizing.py        size_entry(edge_bucket_stats, premium, bankroll) via scripts/kelly.py       [pure]
  engine.py        FLAT -> ENTERING -> IN_POSITION state machine, caps, windows, cooldown      [pure]
  ingest_ib.py     IB session: spot + chain lines, 1s snapshots, client_id="auto"
  ingest_uw.py     UW websocket consumer (option_trades:SPX), reconnect, inferred fallback
  darkpool.py      60s SPY dark pool pace
  store.py         batched Turso writes (chunked multi row INSERT, 5s cadence)
  publish.py       atomic_save() of data/flow_terminal/latest.json + prints-YYYY-MM-DD.jsonl
  daemon.py        loop, market hours gate, service_health heartbeat, sd_notify watchdog
  probe.py         M0 entitlement probe (UW ws message count, IB line errors)
```

Rules inherited: `asyncio.wait_for(..., 15)` on every IB await and a `/health`
`auth_state` precheck (`scripts/CLAUDE.md` § IB Request Bounding); client id
`"auto"` (20 to 49); heartbeat `ok` only on a genuine cycle, `warn`/`error`
otherwise (`scripts/monitor_daemon/CLAUDE.md`); writes chunked, never
`executemany` (`scripts/CLAUDE.md` § Turso Hrana I/O Bounding); a fresh
connection per batch.

### 4.2 Model (`greeks.py`, `dealer.py`)

- Greeks: Black Scholes with `T = minutes_to_1600_ET / (365 × 24 × 60)`; charm
  and vanna analytic. Parity test to 4 decimals against
  `scripts/scenario_analysis.py:192-218` and `web/lib/blackScholes.ts` for
  T ≥ 1 day; a 0DTE fixture pins the T→0 behaviour (gamma concentrates at the
  money, charm sign flips across spot).
- Inventory per strike: `oi_tape` mode = `+call_OI − put_OI − customer_net_bought_today`;
  `tape_only` mode = `−customer_net_bought_today`. Prints signed at the ask add
  to customer bought, at the bid subtract, at the mid are excluded and counted.
  Same sign convention as `gex_scan.py` (`net = call_gex + put_gex`, UW positive
  calls / negative puts) so the two GEX surfaces agree.
- `$ per 1%` = inventory × gamma × S² × 0.01 × 100; charm in $/hour; vanna in
  $/vol point.
- Levels: net at spot (regime), 0γ flip (sign change re-pricing gamma across a
  spot grid, as `gex_scan.compute_gex_flip` does on buckets), call wall (largest
  long γ strike above spot), put wall (largest long γ strike below spot),
  accelerant (largest short γ strike within ± expected move), expected move =
  ATM straddle mid (model free; IV shown as derived).

### 4.3 Detector and edge (`detector.py`, `edge.py`)

Rules as published (brief § 3), made explicit:

- Watch: OTM, mid $0.50 to $3.00.
- Block: ≥ 500 contracts in one trade (tape) or one read (inferred).
- Burst: ≥ 300 contracts in 60s at ≥ 5× NORM.
- Skip: contract already up > 100% in the last 2 minutes.
- Edge, six binary criteria, one point each:
  1. The path from spot to the strike crosses net short γ (≥ half the strikes in the path short).
  2. No wall between spot and the strike.
  3. Strike inside the expected move.
  4. Print signed at the ask (buyer initiated).
  5. Burst and block both true, or premium ≥ $100k.
  6. Dark pool SPY pace ≥ 1.2× prior and on the same side (Gate 2 marker).
- Live arming requires criterion 6 true on the triggering print; paper does not.

### 4.4 Engine (`engine.py`, `sizing.py`)

- Entry: side filter (calls or puts), min edge (default 3), entry windows
  (default 10:00 to 12:59 and 15:45 to 15:55 ET, configurable), cooldown 3m,
  OTM ≤ 0.8%, ask $1.00 to $1.50.
- Order: limit at ask + 1 tick, cancel after 6s; on fill a resting limit sell at
  the take profit multiple; no stop; hold to the 16:00 cash settle.
- Sizing: `kelly_ticket(prob_win = scorecard hit rate for the edge bucket and
  TP multiple, max_gain = (TP − 1) × premium, max_loss = premium, bankroll,
  fraction = 0.5)`, then `min(contracts, 2.5% bankroll / premium, config cap)`.
  Below five sessions of scorecard the engine sizes one contract in paper and
  refuses live.
- Gate 1 check at config write: live TP multiple < 3× is refused with the gate
  named.
- Paper mode: `scripts/paper/matcher.py` + `paper_fills` (migration 0021) with
  the chain's 1s bid/ask as the tick stream. Live mode: FastAPI
  `POST /orders/place` → `scripts/ib_place_order.py` with
  `RADON_KELLY_ENFORCE_ORDERS=1`, `RADON_KELLY_ENFORCE_MODE=block` for this
  caller, `tradingClass "SPXW"`, `idempotencyKey = signal_id`. The take profit
  sell is placed by the engine on the fill event (the fill monitor pattern,
  `scripts/monitor_daemon/handlers/fill_monitor.py`), never pre-placed.
- Arming: `flow_engine_state.armed` with `armed_by`, `armed_at`, `expires_at`
  (15:55 ET same day). Daemon restart, trading halt (`scripts/trading_halt.py`),
  relay/market data degraded, or the watchdog disarms; nothing re-arms
  automatically. Kill = `/trading/kill` (existing) plus engine Off.
- Pushover on every live order placed and every live fill.

### 4.5 Storage (`0095_flow_terminal.sql`)

```
flow_prints          (id, session_date, ts, symbol, expiry, strike, right, size, price, premium,
                      side bid|mid|ask, source tape|inferred, is_block, burst_x, edge, edge_bits,
                      spot, raw json)                       -- every flagged print
flow_dealer_snapshots(session_date, ts, symbol, expiry, mode, spot, net_gex, charm, vanna,
                      flip, call_wall, put_wall, accel, em, by_strike json, uw_drift)
                                                            -- every 5s
flow_signals         (id, print_id, side, edge, decision taken|rejected, reason, entry_px,
                      hit_2x_at, hit_3x_at, max_px_30m, paper_fill_id, order_ref)
flow_engine_state    (symbol, config json, armed, armed_by, armed_at, expires_at, updated_at)
```

Per session volume: ~4,700 dealer rows, hundreds of prints, tens of signals.
Written in 5s batches. Reads are id cursor paginated. Verified landing in Turso
in production before M2 closes (root `CLAUDE.md` § Data Persistence).

### 4.6 Publish path

- Daemon writes `data/flow_terminal/latest.json` (full state, ~10 KB) every
  second with `atomic_save()` and appends print events to
  `data/flow_terminal/prints-YYYY-MM-DD.jsonl`.
- FastAPI `GET /flow-terminal/stream` (SSE): stats the file every 250 ms, emits
  `state` on mtime change and `print` events from the jsonl offset the client
  last saw (`Last-Event-ID`). No Turso call in the handler. SSE through Next
  already exists for chat (`web/app/api/assistant/route.ts`); the Next route
  `/api/flow-terminal/stream` pipes the body through with `force-dynamic`.
- `GET /flow-terminal/snapshot` is the same payload for polling fallback (the
  `useSyncHook` pattern at 2s) and for the assistant catalog.
- `GET /flow-terminal/scorecard?sessions=5`, `GET/POST /flow-terminal/config`,
  `POST /flow-terminal/arm`, `POST /flow-terminal/disarm`. Config and arm are
  operator only and audited to `order_events` (`scripts/api/order_audit.py`).
- Alternative if SSE fan out proves inadequate: a dedicated hub like
  `scripts/mktnews/index.js --serve` on 8766 behind Caddy `/ws-headlines`
  (`cloud/caddy/Caddyfile:33-34`) with a `web/lib/flowSocket.ts` modelled on
  `headlinesSocket.ts`. Not the first choice: it adds a port, a Caddy route and
  a second auth path.

### 4.7 Deployment

- `cloud/services/radon-flow-terminal.service`: `Type=notify`,
  `WatchdogSec=120`, `Restart=always`, `RADON_DB_NO_REPLICA=1`, the
  `runtime-container.conf` drop in, `EnvironmentFile` for `UW_TOKEN` and the
  IB mode overlay. Market hours gate inside the daemon; the unit stays up.
- Register in `cloud/scripts/setup-vps.sh` (unit list), pin in
  `cloud/config/installed-units.sha256`, add to the `deploy.sh:103` restart
  set, `cloud/tests/test_systemd_services.py`, health windows in
  `scripts/watchdog/services.py:128` and `web/lib/serviceHealthWindows.ts`.
  A long running `.service` needs a manual `enable --now` once
  (`cloud/CLAUDE.md` § 563-620).
- Host: Hetzner in cloud mode (co located with the gateway and FastAPI, so the
  file based publish path is local). Local dev: add to `scripts/dev` names.

---

## 5. UI

Direction 3 from the review ("decision strip"): one sourced finding sentence,
the price chart and the dealer ladder fused on one price axis, the tape and the
engine in the existing 384px instrument rail, model and scorecard behind a
disclosure. Plate: `docs/design/flow-terminal/after-{light,dark}.png`.

### 5.1 Registration (survey § 7)

`web/app/flow-terminal/page.tsx` → `<WorkspaceShell section="flow-terminal" />`;
add to `WorkspaceSection` (`web/lib/types.ts:141`), `navItems` and
`clearPrimaryNavigation` (`web/lib/data.ts:40-48`, keep `quickPromptsBySection`
exhaustive), `resolveSectionFromPath` (`web/lib/chat.ts:533`), the switch in
`WorkspaceSections.tsx:4372`, `pageTitle.ts:8`. Mobile drawer reads `navItems`.

### 5.2 Components (`web/components/flow-terminal/`)

| Component | Owns | Notes |
|---|---|---|
| `FlowTerminal.tsx` | `useFlowTerminalStream()` (SSE with polling fallback), reducer, layout | Publishes nothing to the price socket; the terminal has its own stream |
| `FlowFinding.tsx` | The sentence and the six item ledger | Sentence built from state by `lib/flowTerminal/finding.ts`; missing inputs render the brand error pattern ("Dealer map unavailable. Chain feed incomplete. Last valid sample 10:14:59 ET."), never an empty sentence |
| `FlowInstrument.tsx` | SVG: price path, print bubbles, ladder bars on the shared price axis, walls / accel / flip rules, EM band, spot tag | Hand built SVG like `instruments/GexLaplaceContour.tsx` and `RegimeRelationshipView.tsx`; d3 scales only. Bubble fill = call solid / put hollow; tone = edge state (`--text-muted` → `--signal-deep` → `--signal-core`); ladder `--signal-core` long γ, `--dislocation` short γ |
| `FlowPremiumStrip.tsx` | Net premium bought line + per bar flow histogram on the same time scale | `--fault` for the signed line only |
| `PrintTape.tsx` | Dense table, newest first, `SortTh` + `useSort` | Columns ET, contract, size, px, side, ×norm, edge, source badge when inferred |
| `EnginePanel.tsx` | Side, min edge, TP multiple, sizing summary, ack, Arm, caps line; **owns the arm fetch** | Renders `<OrderRiskGate>` for the representative one contract long call (bounded); typed confirmation on arm; disabled until ack |
| `ScorecardTable.tsx`, `ModelDisclosure.tsx` | Scorecard by edge bucket and source, UW drift column; model text | Below the fold |
| `mobile/MobileFlowTerminal.tsx` | One column: finding, instrument with the ladder collapsed to the three levels, tape, engine as `BottomSheet` | 393×852 capture before M3 closes |

Rules: tokens only, `color-mix` washes, 6 to 10px radii per Clear; no raw hex;
no em dashes; cadence copy derived from `sample_interval_ms` in the stream
metadata, never hardcoded; `radonCapability` on every route (`read` for
stream / snapshot / scorecard, `mutate.trading` for config / arm / disarm);
`dynamic = "force-dynamic"` + `cache: "no-store"`.

---

## 6. Tests (red first)

Python (`scripts/tests/test_flow_terminal/`):
- `test_greeks.py`: parity with `scenario_analysis.py` at T ≥ 1d; 0DTE fixture
  (T = 345 min): gamma peaks at the ATM strike, charm changes sign across spot,
  vanna finite.
- `test_dealer.py`: a synthetic chain fixture yields net long γ at spot, call
  wall above, put wall below, accel inside the EM, flip below spot; `tape_only`
  mode ignores OI; mid prints are excluded and counted.
- `test_detector.py`: block, burst, watch band, skip rule, NORM seeding.
- `test_edge.py`: each criterion flips exactly one point; criterion 6 reads the
  dark pool pace helper.
- `test_sizing.py`: half Kelly, 2.5% cap wins, refusal below five sessions.
- `test_engine.py`: entry at ask + 1 tick, cancel at 6s, TP resting after fill,
  caps, cooldown, windows, Gate 1 refuses live TP < 3×, disarm on halt /
  degraded / restart, idempotency key = signal id.
- `test_store.py`: chunked multi row INSERT, 5s batching, cursor reads.
- `test_contract_resolver_spxw.py`.
- FastAPI route tests: stream emits `state` then `print`, `Last-Event-ID`
  resume, snapshot, scorecard, config/arm auth and audit.

Vitest (`web/tests/flow-terminal-*.test.tsx`):
- `flow-terminal-stream.test.tsx`: reducer from SSE events, polling fallback.
- `flow-terminal-finding.test.tsx`: sentence cases and the error pattern.
- `flow-terminal-instrument.test.tsx`: a bubble at strike K lands on the same y
  as the ladder bar for K; out of range walls pin to the edge with distance.
- `flow-terminal-arm-wire.test.tsx`: render `EnginePanel`, stub `fetch`, click
  Arm with ack ticked and typed confirmation → assert
  `POST "/api/flow-terminal/arm"` with the payload shape; nothing fires while
  the ack is unticked (`web/CLAUDE.md` § gated action tested at the wire).
- Sortable contract, assistant catalog pin, cache contract (existing suites).

E2E (Playwright, `web/e2e/flow-terminal.spec.ts`): stubbed stream; desktop
1440×800 and mobile 393×852 screenshots; chrome-cdp live capture during RTH
before M3 closes.

Coverage target 95% on `scripts/flow_terminal/` and `web/components/flow-terminal/`.

---

## 7. Milestones (each closes on evidence, not on code)

| M | Scope | Done when |
|---|---|---|
| M0 Probe | `probe.py`: UW ws `option_trades:SPX` for 60s during RTH; IB session with 90 SPXW lines and ticks 100/101/106; SPXW resolver fix | Evidence file with message counts, line errors, OI tick presence; decision recorded: tape vs inferred |
| M1 Model | `greeks`, `dealer`, `detector`, `edge`, `sizing`, `engine` pure modules | All Python tests green; fixture reproduces the TZU snapshot's level ordering |
| M2 Daemon, paper only | ingest, publish, store, unit, health | Runs three full sessions on Hetzner; rows in Turso `flow_dealer_snapshots` / `flow_prints` / `flow_signals`; `latest.json` under 1.5s old through RTH; UW drift column populated |
| M3 UI read only | page, finding, instrument, tape, scorecard, mobile | Vitest + E2E green; chrome-cdp screenshots light/dark/mobile attached to the PR; no-slop re-score ≤ 0.5 |
| M4 Paper engine | `scripts/paper` fills, P&L surface appears only with trades | Five sessions of paper signals graded; scorecard shows hit rates by bucket |
| M5 Live arm | arm flow, Kelly block mode, Pushover, auto disarm | First live session capped at one contract per print; every order audited in `order_events`; PR green per § Pull Request Completion |

Branch per milestone (`feat/flow-terminal-m0` ...), one PR each, CI watched to
green, Pushover `radon PR green` on each.

---

## 8. Decisions needed before M0

1. **Underlying:** SPX only for v1. SPY (American, physically settled, no 16:00
   cash settle) is a later variant with its own engine rules.
2. **Host:** the daemon runs on Hetzner next to the gateway (cloud mode). Laptop
   only for development against the local gateway.
3. **UW tier:** websocket needs the Advanced tier. If M0 shows no entitlement,
   v1 ships inferred prints labelled as such and the live arm stays disabled
   until a tape source exists.
4. **Gate 2 for live:** criterion 6 (dark pool SPY agreement) is required to
   arm. Recommended yes; without it the terminal is a lit flow follower, which
   is not Radon's edge.
5. **Entry windows:** keep TZU's defaults as config, no rationale was given in
   the thread; the scorecard will say whether they matter.

## 9. Risks

- Inferred print misattribution: shown per print, scored per source, never
  pooled.
- Naive dealer sign convention: the UW drift column makes disagreement visible
  every 60s; a drift above a threshold paints the finding sentence with the
  warn state.
- No stop at the 16:00 settle: max loss is the premium, capped by Gate 3 and
  the daily premium cap; the ack names it.
- IB line contention with the relay during a full chain tab session: M0
  measures; the daemon backs off to a ± 2 EM window on IB's market data line
  limit error and records `warn`.
- Turso write volume: batched; verified in M2 before anything else lands.
