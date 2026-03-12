# Lessons

## 2026-03-12 (Cloud Migration — Reverted)

- Before refactoring a route from `spawn()` to `fetch()`, read the original wrapper code to understand output format (stdout JSON vs file write vs human text). `ib_sync.py` writes to `portfolio.json` and prints human-readable text — the FastAPI endpoint and fallback both assumed JSON stdout.
- When adding a new service dependency (FastAPI), the app must work identically without it. The "fetch failed" error surfaced in production because the fallback wasn't tested.
- Don't report refactored routes as "complete" without `curl`-testing both GET and POST. The portfolio POST was broken but reported as done.
- Three-service startup via `concurrently` (Next.js + IB WS + uvicorn) is fragile — port conflicts cause silent failures. Prefer two-service until the third is proven stable.
- When shelving incomplete work, save all code to a tmp directory with documentation before reverting, so it can be resumed later.

## 2026-03-12

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
