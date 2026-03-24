# Lessons

## 2026-03-24

- Do not default to `python3.13 -m pytest -q` for every scoped fix in this repo. Use an affected-file runner first so Python verification tracks only the changed surfaces, then escalate to broader pytest only when the change genuinely crosses many Python modules.
- When a user explicitly names a skill and asks for a separate agent, use that skill in a dedicated subagent and integrate its result instead of handling the work only in the main thread.
- For order-placement failures, never dump transport-layer strings like `Radon API 502: ...` into the UI. Preserve upstream status/detail at the Next route boundary, then convert broker prose into short operator-facing summary/detail copy in a shared banner.
- When UI verification guidance prefers `chrome-cdp`, check whether that skill is actually available in the current Codex runtime before planning around it. If it is unavailable, explicitly reuse any already-running dev server and fall back cleanly instead of trying to boot a duplicate app instance.
- IB Gateway CLOSE_WAIT (port listening but upstream IB session dead) causes `TimeoutError` on every API call but was NOT caught by the auto-recovery pattern matcher which only matched `ECONNREFUSED`. Fix: add `TimeoutError` and `API connection failed` to `_IB_CONN_REFUSED_PATTERNS`, add `_has_close_wait()` via `lsof` to detect dead upstream at startup, and have the restart script kill lingering IB/IBC Java processes (`pgrep -f 'ibgateway|IBC|ibcontroller'` + `kill -9`) before restarting.
- When modifying an IB order by re-submitting via `placeOrder`, the open-order snapshot may contain stale VOL fields (`volatility`, `volatilityType`) populated by IB. Re-submitting these on a LMT order causes IB error 321 ("VOL order requires non-negative floating point value for volatility"). Fix: reset both fields to IB sentinel values (`1.7976931348623157e+308` and `2147483647`) before `placeOrder` on non-VOL orders.

## 2026-03-22

- When a route handler gates fresh data fetches behind `isMarketOpenNow()`, any data published after market close (UW end-of-day processing, overnight batch updates) will never be picked up until market reopens. Use a data-staleness check (compare latest data-point date to today) instead of a market-hours guard. This prevents stale charts across ~17.5 hrs/day of market-closed time plus weekends.

## 2026-03-21

- Never hardcode `"python3"` in subprocess calls. Use `sys.executable` so child processes inherit the same interpreter as the parent. On this machine, `python3` resolves to Homebrew Python 3.14 (missing `ib_insync`, incompatible asyncio) while the FastAPI server runs Xcode Python 3.9. The PATH order varies between shell, Node.js, and Codex environments.

## 2026-03-19

- For `/internals` skew history, treat the skew metric source and the expiry-discovery source as separate decisions. If the user explicitly chooses UW for the metric, do not keep IB in the path just because it can supply expiries; make the route semantics match the chosen provider.
- For `/internals` skew work, honor the market-data source order explicitly: check IB first for the option-chain/expiry surface before falling back to Unusual Whales for the skew metric itself. Do not assume IB is irrelevant just because the final skew value comes from UW.
- When a feature fix is green in focused tests but the repo-level gates are still red, stay on the blocker chain before switching to docs/commit work. Re-run the full suites, trace the remaining failures by boundary (backend seam, provider contract, frontend bridge), and clear those blockers first so the task can actually close.
- For IB cancel/modify bugs, do not trust the original `Trade` object or collapse provider errors at the bridge layers. Re-check refreshed open orders, treat disappearance after cancel as success, extract human-readable JSON script errors in FastAPI, and preserve upstream status/detail through the Next order routes so the browser sees the real failure.
- When a ticker-detail or quote-telemetry surface shows a synthetic multi-leg option quote, do not label a derived combo midpoint as `LAST` or build it from asynchronous leg trade prints. For spreads and risk reversals, use the live combo mark from bid/ask and label it clearly as a mark so it cannot be confused with the entry fill basis.
- When a combo or short-option surface already carries sign semantics from the portfolio model or combo quote math, do not normalize it with `Math.abs()` in a detail tab or order form; add regressions that assert both the displayed sign and the tone class survive into the input and leg table.
- When refactoring shared portfolio row math across `PositionRow` and `LegRow`, do not let a child row reference a parent-only variable by name; add a render-path regression for expanded legs so scope leaks fail in test instead of at runtime on `/portfolio`.
- When a user points to a specific bad position as the clue for a portfolio-wide P&L bug, pivot the reproduction to that exact live position first. Verify the cached portfolio snapshot, the live websocket quote payload, and the rendered row/card for that symbol before designing a generic fix, or you risk fixing the wrong layer.
- When a dashboard Day Move bug involves a same-day position, compare the rendered close-based move to `ib_daily_pnl` before trusting the math. `reqPnLSingle` uses fill basis for intraday adds, so prior-close calculations can show the opposite sign even when the provider data is already correct.
- When a user flags a second symbol as having the "same" pricing problem, check both the visible row path and any shared synthetic spread helpers. A row-level stale-last fix can leave shared combo/spread calculations still trusting raw option `last` prints outside the live market.
- When a cached panel auto-syncs on mount through a shared hook, guard the initial GET path against StrictMode-style double effects. Otherwise a second stale cache read can overwrite the fresh POST result and make the auto-refresh appear broken even though the network request succeeded.
- When a background refresh fails but cached data is still being shown, surface the upstream error instead of suppressing it. A stale table with no warning looks current and wastes debugging time; a degraded-but-explicit view makes the real provider boundary obvious.

## 2026-03-17

- IB Gateway's market data feed can go stale: the TCP connection stays alive (port 4001 listening, control plane responding to `qualifyContracts`) but the data plane stops delivering ticks. All `reqMktData` calls return nan indefinitely. The fix is restarting IB Gateway. This happens when the internal session expires overnight. The WS relay server should detect this condition (subscriptions active but zero ticks received for >30s during market hours) and auto-restart the gateway.
- Static IB client ID registries are fragile — they cause recurring collision bugs when persistent pool connections hold IDs that subprocess scripts also try to use. The durable fix is range-based allocation: partition the ID space (pool=0-9, relay=10-19, subprocess=20-49, scanners=50-69, daemons=70-89, CLI=90-99) and have on-demand scripts use `client_id="auto"` which picks a random ID from the range and retries on conflict.
- Python 3.9 does not support the `int | str` union type syntax (requires 3.10+). Use `Union[int, str]` from `typing` or omit the annotation. This caused `TypeError: unsupported operand type(s) for |` when subprocess scripts were spawned.
- IB executed orders should be grouped by position (opening/closing) rather than shown as flat fills. Group by underlying symbol + time bucket (60s). BAG fills are the combo envelope (quantity, net price); OPT fills are the legs (P&L, commission). P&L and share data belong on the closing position group, not individual fills.
- When computing P&L % for a position group share card, use the aggregated OPT leg notional (avgPrice × qty × multiplier per leg), not the BAG envelope price. BAG fills have commission=0 and realizedPNL=0 — they're metadata, not execution data.
- Combo net credit on open-order rows is quantity-aware: scale each leg by its effective ratio/size before summing quote values. Ignoring this turns 1x2 risk reversals (for example 25 short puts / 50 long calls) into incorrect net credit/debit values and can mislead execution/hedge decisions.
- For options-chain combo entry, treat leg quantities as the operator's absolute desired contract counts at the UI boundary, then normalize them to `combo quantity + per-leg ratio` before computing net quotes or building the IB payload. If the UI prices raw counts but submits hardcoded `ratio: 1`, the displayed net credit and the actual order structure diverge.
- Option expiries must be canonicalized at the shared contract layer, not ad hoc at individual call sites. If portfolio positions use `YYYYMMDD` but the chain page or websocket client keeps dashed expiries from `/api/options/expirations`, held option legs can miss quotes even though the same contract is already subscribed elsewhere in the app.
- For closed combo share cards, never source the entry basis from the first open BAG on the same symbol. Match opening fills to the closing combo's exact option contracts and quantities, then derive the signed basis from opening cash flow so net-credit risk reversals render negative entry prices and correct return percentages.

## 2026-03-18

- When the backend already preserves descriptive order metadata, do not let the open-order display model throw it away for single rows. Trace the full path first: IB open orders -> sync serialization -> cached JSON -> API -> renderer. If the API already has `secType/right/strike/expiry`, build a frontend summary from that contract instead of rendering only the bare ticker.
- For `/orders` modify flows, separate "mutate the existing order" from "change the structure of the order." Price and quantity can ride the IB modify path; combo leg edits cannot. If the operator needs to change BAG legs, the UI and API must switch to cancel-and-replace instead of pretending a price-only modify modal is sufficient.
- When a modal graduates from a single-field dialog to a structured order editor, do not keep it inside the default narrow shell or reuse summary-row flex styles for input rows. Give it a dedicated modal width and field-grid layout as soon as it contains multiple logical sections, or the UI will become technically functional but operationally unusable.
- When widening a modal for a new editor, do not stop at the shell width. Measure the usable width of each inner panel and compare it to the minimum width implied by the field grid. A modal can be “wide” and still clip badly if a nested grid keeps desktop-style column minimums that exceed the panel it lives in.
- For IB order modification, never treat an unchanged `Submitted` / `PreSubmitted` status as proof that the modify succeeded. An already-open order starts in `Submitted`, so success must be confirmed against a refreshed open-order snapshot that reflects the requested price or quantity; otherwise the API can report a false positive that the `/orders` UI later contradicts.

## 2026-03-16

- IB BAG (combo) orders use `ComboLeg.action` to define the spread structure and `Order.action` (BUY/SELL) to control direction. When `Order.action=SELL`, IB reverses all leg actions. Never flip `ComboLeg.action` based on the trade direction — that creates a double-reversal and triggers IB error 201 ("Riskless combination orders are not allowed"). Always: `LONG → BUY`, `SHORT → SELL` in `ComboLeg.action`.
- When reconstructing a portfolio equity curve from trade fills, snap weekend/holiday trade dates to the next valid calendar trading day. Trades on non-calendar dates get counted in the back-solve equation but never applied during simulation, causing the ending equity to diverge from IB net liquidation.
- The `formatExpiry()` function adds dashes (`"20260327"` → `"2026-03-27"`), but IB's `Option` constructor expects the raw `"20260327"` format. Never call `formatExpiry()` in order payloads sent to IB — it causes "Could not qualify contract" 502 errors.
- BAG orders in `orders.json` don't include combo leg contract details by default. To resolve BID/MID/ASK for spread modify modals, qualify each `ComboLeg.conId` during order sync to store full contract details (symbol, strike, right, expiry). Without this, the modify modal shows "No real-time market data available" for spreads.

## 2026-03-15

- IB Gateway's CLOSE tick on weekends returns the penultimate session's close, not the last trading day's close (e.g. Wednesday's $106.19 instead of Friday's $96.81). Never use IB's `close` field as a reliable "previous close" for stocks; only cash indexes (VIX, VVIX) report their actual value via the CLOSE tick. Use the previous-close API (UW/Yahoo) instead.
- The first FastAPI migration attempt (2026-03-12) failed because of three traps: dual-path spawn-fallback, file-writer scripts that don't fit JSON response model, and three-service startup fragility. The successful approach: no spawn fallback (serve cached on failure), subprocess execution for all IB scripts (avoids ib_insync event loop conflicts), and auto-restart IB Gateway on ECONNREFUSED.
- When `ib_insync.IB.connect()` is called from `asyncio.to_thread()`, the thread has no event loop. Fix: create and set a new event loop in the thread before connecting (`asyncio.set_event_loop(asyncio.new_event_loop())`).
- IB doesn't allow duplicate client IDs. When a connection pool holds client_ids 0/11/31, subprocess scripts must use different IDs (40/41) or they'll get ECONNREFUSED.
- When reverting an optimization campaign that renamed CSS classes across 60+ files, identify which files were NEW features built during the campaign (keep them) vs. existing files that were modified (restore from pre-optimization). Use `git ls-tree` to check file existence at the target commit.

## 2026-03-13

- Before a ship/push step, confirm whether the current checkout is already on `main`; do not talk about merge cleanup as if a worktree branch exists when the active checkout is already the main branch.
- When a scoped commit leaves behind a dirty regression file that encodes a stronger route contract, do not just explain why it was excluded; either revert it immediately or ship the stronger contract as the next scoped change so the worktree does not stay in a half-promised state.
- When a derived page like `/performance` depends on the portfolio sync lifecycle, do not give it an isolated long-poll loop and assume the cache route will save you; if the shell can advance `portfolio.last_sync` first, the dependent page must react to that fresher timestamp and revalidate immediately.

## 2026-03-12 (Cloud Migration — Reverted)

- Before refactoring a route from `spawn()` to `fetch()`, read the original wrapper code to understand output format (stdout JSON vs file write vs human text). `ib_sync.py` writes to `portfolio.json` and prints human-readable text — the FastAPI endpoint and fallback both assumed JSON stdout.
- When adding a new service dependency (FastAPI), the app must work identically without it. The "fetch failed" error surfaced in production because the fallback wasn't tested.
- Don't report refactored routes as "complete" without `curl`-testing both GET and POST. The portfolio POST was broken but reported as done.
- Three-service startup via `concurrently` (Next.js + IB WS + uvicorn) is fragile — port conflicts cause silent failures. Prefer two-service until the third is proven stable.
- When shelving incomplete work, save all code to a tmp directory with documentation before reverting, so it can be resumed later.

## 2026-03-12

- When a post-close dashboard depends on a cache-backed market scan, do not treat the first `market_open=false` payload as final unless its session date matches today; if the close-transition scan still returns the prior session, fix the scan to synthesize today's closing snapshot from quote sources instead of relying on the UI to keep retrying.
- When a dashboard route already encodes a one-minute freshness contract in its GET path, do not poll that surface every five minutes with POST rescans from the browser; use GET polling at the route cadence so scheduled close updates can land in the open page automatically.
- When a chart point encodes the current classified regime, do not hardcode its highlight color independently of the shared classifier; the marker, summary label, and state key must all derive from the same quadrant state or the UI will contradict itself.
- When adding a new shell-level alert surface for an actionable broker issue, do not broaden it into a generic connection-status banner; keep the new banner scoped to the actionable state and leave generic reconnect/disconnect notices on the existing toast channel.
- When a repo-owned dev service binds a fixed local port, do not let `EADDRINUSE` crash the whole startup workflow; detect the port conflict and either reuse the existing listener intentionally or fail with an explicit, non-catastrophic message.
- When a stacked telemetry row still wastes width after collapsing to one line, do not just center the cluster; anchor the primary label/value on the left and use the remaining width for an intentional meta rail.
- When a telemetry strip collapses to single-column rows, do not just preserve desktop stacking inside each cell; use the full row width with a compact horizontal presentation so the operator does not waste half the viewport on empty space.
- When a telemetry strip collapses to single-column mobile cells, do not keep the desktop `1fr auto` change-row layout; the delta text and arrow should stay left aligned as one inline signal, not split across the cell width.
- When an operator asks for a 3 x 2 telemetry strip, removing the empty slot is not enough; the second-row cards must still read as intentional peers, which means equal-width bottom-row cards rather than one narrow card plus one stretched card.
- When an odd-number telemetry strip collapses into a multi-column grid, do not leave the final slot empty and let the strip background read as a fake card; make the last real card span the remaining columns or collapse the strip further.
- When an operator asks for a diagnostic chart to behave like an existing analytical time-series chart, match the interaction affordance directly; static lines are not enough when the comparison depends on point-in-time values.
- When an operator asks how to make a regime tooltip actionable, do not stop at metric definition; include the concrete portfolio posture and trade response for both sides of the signal.
- When a dashboard row contains only two dense diagnostic panels, do not leave it as a permanent `1fr 1fr` split on narrow widths; stack the panels earlier and remove inline layout rules so the responsive contract lives in CSS and tests.
- When five telemetry cards cannot fit cleanly, do not hold the strip on a single row past readability; abbreviations can help, but the layout still needs an earlier balanced collapse breakpoint before cards clip or truncate.
- When a dense strip only breaks at narrower desktop widths, prefer a semantic abbreviation and tighter spacing before dropping to a wrapped multi-row grid; preserve the operator’s scan rhythm until the viewport is genuinely too small.
- When a dense telemetry strip starts collapsing on narrower desktop widths, do not keep forcing a fixed five-column grid; give the cards a responsive grid with real minimum widths and let supporting lines wrap or stack before values overlap.
- When an operator says the spread is “10c, not $500,” treat the quote ladder as quote quality telemetry, not order-size cost; render raw spread width and percentage, and do not multiply by quantity or contract multiplier on that surface.
- When an operator narrows a process-cleanup request from "all processes" to only dev/test processes, verify the live process list again and target just the repo-scoped dev servers and Playwright runners instead of unloading background product services.
- When an operator narrows a scheduled service window, update the launch schedule source, installer/status text, docs, and the live loaded LaunchAgent together; changing only the plist on disk is not enough.
- When the modify-order modal surfaces spread telemetry without displaying quantity, do not scale the dollar figure by `order.totalQuantity`; use the quote-level execution friction the operator can act on, not hidden order-size notional.
- When a quote ladder is shared across non-order tabs like `Company` and `Position`, do not scale spread notional by the held position size; reserve quantity-sized spread friction for explicit order-entry and modify flows.
- When an operator corrects quote presentation on an order ticket, identify the actual shared telemetry component from the screenshot before patching the nearest modal; quote-order bugs can live in `PriceBar` or another shared display layer rather than the modify form you first suspect.
- For order-ticket spread telemetry, render the quote ladder in market convention order (`BID`, `MID`, `ASK`) and show spread width in both dollars and midpoint-based basis points so fill-quality context is visible without manual conversion.
- When an operator says the spread notional on an order ticket is wrong, verify whether the display should scale by displayed quantity as well as contract multiplier; per-contract option points are not the same thing as order-level notional friction.
- `border-collapse: collapse` on a `<table>` breaks `position: sticky` on `<th>` elements in all major browsers; always use `border-collapse: separate; border-spacing: 0` when sticky headers are needed.
- When a helper like `computeNetPrice()` already multiplies by `leg.quantity`, the display layer must not multiply by quantity again; always trace the data flow to verify what's already baked into the value before adding multipliers.
- When IB returns error code 200 ("No security definition"), handle it like code 354 — silently clean up the subscription instead of logging red errors that flood the console for every invalid option strike.
- IB Gateway holds stale client sessions in CLOSED socket state even after the connecting process dies; the relay server cannot simply retry with the same client ID. Implement a client ID pool (e.g. [100, 101, 102]) with automatic rotation on "client id already in use" errors, and extract all `ib.on(...)` handlers into a reusable `wireIBEvents()` function so they can be reattached to the new IB instance after rotation.
- When multiple processes (`concurrently` dev server + standalone `nohup` relay) compete for the same IB client ID, the second connection silently fails or evicts the first. Ensure only one relay instance runs per client ID pool.

## 2026-03-11

- When a user reports stale scheduled market data, verify the live scheduler state, last successful run timestamp, and recent service logs before assuming the route cache is wrong; freshness bugs are often orchestration failures, not rendering bugs.
- For MenthorQ service debugging in this repo, treat the project root `.env` as the credential source of truth before checking `web/.env`; the launch wrappers explicitly source root `.env` for CTA auth.
- When a scheduled workflow depends on Python packages in this repo, do not trust the first `python3` on PATH; resolve an interpreter that actually has the required runtime modules before declaring the service healthy.
- When an external login fails at one timestamp but succeeds later, report it as an observed transient rejection tied to that run; do not overstate it as a permanent credential/account failure without a fresh re-test.
- When a marketing-site card uses split metric tiles with large mono values, treat each tile as a constrained layout container: add `min-w-0` and wrapping rules up front so long tokens cannot bleed across the grid boundary at desktop widths.
- When closing a refactor roadmap, do not leave concrete residual chart-contract gaps only in the final prose; capture them immediately as follow-up tasks with explicit verification targets so the next pass starts from an auditable backlog instead of a narrative summary.
- When a new operator-facing regime label is added to `/regime`, document the state definitions and classification rule in the README and the relevant strategy docs in the same change; do not leave the meaning trapped in code or tooltips.
- When an operator is comparing two regime inputs for signal value, do not stop at parallel time-series lines; add a relationship-first view that makes spread, divergence, and quadrant state explicit.
- For dashboard charts that encode time or magnitude, always include visible axis/tick context or equivalent scale labels; a line-only chart is insufficient for operator interpretation.
- For index-style live feeds, do not assume the broker `close` field is the correct prior-session anchor for a day-over-day UI calculation; compare it against the cached official close from the authoritative daily source before using it in the strip.
- When shipping theme support, audit SVG/chart surfaces and their companion meta panels separately from the shell; hardcoded chart gradients can stay dark even after the page chrome switches to light mode.
- For UI tooltip copy, describe what the operator is seeing and why it matters; do not surface implementation details like charting libraries, websocket feeds, or transport mechanics unless the user explicitly asks for them.
- When a shared `section-header` uses `justify-content: space-between`, never leave a help icon as its own sibling between the title and a right-side status badge; wrap the title and icon into a left cluster.
- When a user asks for docs and a commit checkpoint after a fix, update the relevant docs and create the scoped commit before starting the next feature; do not roll the follow-up feature into the same uncommitted batch.
- When a user reports that a cache-backed bug fix still is not visible, verify the actual on-disk artifact and refresh path immediately; a correct code path is not sufficient if the generated cache never updated successfully.
- When a user reports that a cache-backed UI is still stale after a code fix, verify the on-disk artifact shape and the refresh path before declaring the bug closed; a correct code path is not enough if the live cache never refreshed successfully.
- When a user provides an authoritative upstream fallback source, replace the generic last-resort source in both code and docs immediately; do not keep Yahoo ahead of a vendor-native feed just because it already exists in the codebase.
- When a user provides an authoritative market-data fallback source, move that source ahead of generic last-resort providers in both code and docs immediately; do not leave Yahoo as the fallback if the product spec now requires a stronger primary source.
- When a user corrects a claim about live market-data availability, verify the actual subscription and render path before blaming the provider; the UI may simply be ignoring a live IB field that is already subscribed.
- When matching an existing metric-card pattern, copy the card’s information hierarchy, not just the data source: main value, then daily move line, then muted context subline.

## 2026-03-10

- When a user reports a performance-vs-portfolio mismatch, verify both route freshness windows and the exact ledger date format before trusting a reconstructed equity curve; stale caches and `YYYYMMDD` trade dates can invalidate the anchor logic.
- When documenting or automating IBC control in this repo, treat the canonical service as the secure machine-local wrappers under `~/ibc/bin/`, not as a repo-owned service.
- Repo scripts for IBC remote control must be described as convenience wrappers around `/Users/joemccann/ibc/bin/*secure-ibc-service.sh`, never as the primary service implementation.
- For market index integrations, do not assume a daily historical bar matches the authoritative displayed value on the exchange dashboard; verify the exact field semantics against the live source before wiring the signal.
- For repo changes that touch a monorepo with separate deploy targets, check whether deployment filters are needed so unrelated pushes do not rebuild unaffected apps.
- When a generated output directory appears in the worktree and should not be versioned, add an explicit root `.gitignore` entry immediately instead of leaving it as recurring untracked noise.
- When collaborating on parallel changes in this repo, treat any file the user says they already changed as reserved unless a direct integration change is unavoidable; design around the declared contract first to avoid stomping concurrent work.
- When fixing Codex skill manifests, validate the YAML frontmatter types directly; bracketed placeholder text after `description:` becomes a YAML sequence and the loader expects a plain string.

## 2026-03-18

- For IB combo entry from the options chain, do not derive the BAG `Order.action` from whether the combo is a debit or credit; the chain builder must keep the combo envelope on `BUY` for entry orders and let the leg actions define the structure, or IB will reverse the spread.
- When a chain order builder transitions from a single-leg state to a combo, do not preserve a stale manual net-price override; reset the top-level manual-price flag on structural leg changes so the limit field re-bases to the combo quote.
- For ticker-chain Playwright coverage, do not rely on ad hoc `ws-price` custom events to drive option quotes; use a mocked WebSocket subscription path when the page logic consumes live prices through `usePrices` rather than DOM events.
- When a user asks to harden process memory after a regression, propagate the rule to every instruction surface in the repo (`AGENTS.md`, `CLAUDE.md`, `.pi/AGENTS.md`, structured memory facts, and `tasks/lessons.md`) in the same change; do not stop at the lesson log alone.
- When porting a rule from Claude-specific docs into Codex docs, translate it into Codex-native instruction style and state any runtime-dependent capability explicitly; `chrome-cdp` is available in this repo session, but Codex docs must still define a Playwright fallback because skill availability is host-dependent.
- For `/orders` modify telemetry, never show raw IB bid/ask in isolation when a resting limit order is already working; overlay the live order onto the displayed best bid/ask so the modal reflects the actionable book the user is participating in.
- For `/orders` combo rows, do not disable modify just because the row is a frontend grouping; if IB delivered separate leg orders, synthesize a BAG-style edit target and cancel every underlying leg before placing the replacement combo.
- When a user asks for docs, commit, and push after a fix, verify whether repo-level unrelated failures still block the commit path before optimizing for push completion; if verification is still red, pivot immediately to resolving or explicitly triaging those failures instead of continuing toward commit.
- For sync-based client hooks, `active=false` must mean "no polling/background sync," not "never perform the initial cached read"; otherwise closed-market pages like `/internals` can hang on their loading state forever.
- When a route bug comes from a shared polling hook, audit every other hook that gates on the same `active` flag before stopping; `/internals` and `/portfolio` were the same class of closed-market cached-load failure split across `useSyncHook` and `usePortfolio`.
- When a route must remain implemented but hidden from operators, do not remove it from shared route metadata just to satisfy tests; keep the route entry and add an explicit UI-hidden flag so the sidebar and route-resolution code can diverge safely.
