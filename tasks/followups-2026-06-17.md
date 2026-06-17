# Follow-ups — 2026-06-17 session

(`tasks/todo.md` is the UW-roadmap plan; this file tracks loose ends from the
mobile-redesign / watchdog / order-risk / VIX work in this session.)

## Shipped + verified (deployed, CI green)
- Mobile redesign: foundation `5c4b3b4`, per-page `8e86013`, header-overflow `ccdd02b` — verified live at 390px.
- Watchdog push-flood fix `4fce4de` — orders-sync autonomous 5-min refresh + dormant-service suppression. Verified live (intraday/daily buckets `fired=0`).
- Close-position P&L `d0c8122` — buy-to-close a SHORT shows realized P&L, not opening risk. Verified live (MU short-call ticket: Close Debit + Est. Realized P&L, no Max Gain/Loss).
- VIX option underlying `e02f4bd` — per-expiry forward (`resolveUnderlyingSpot`) not spot. Unit-verified (6 tests); live visual blocked by Chrome Debug.

## Open
- [ ] **VIX `fwdCurve` relay publishing** — confirm `ib_realtime_server.js` publishes the FAR-month forward in `prices.VIX.fwdCurve[YYYYMMDD]` for held expiries (e.g. August). If absent, the underlying falls back to front-month `fwd`. Server-side, no browser needed.
- [ ] **Live visual re-verify** (VIX underlying + mobile + close-short) once Chrome Debug / VPN-on-VPN is resolved. App is healthy; Chrome Debug was wedged (`ERR_SOCKET_NOT_CONNECTED`) while curl worked.
- [ ] **Order ticket / chain mobile** — buy/sell encoding + debit/credit submit + ATM auto-scroll deployed + test-clean but not exercised with a real order at RTH. Market-hours pass.
- [ ] **Regime CRI page** sits on the `cri_scan.py` loader on load (desktop + mobile) — investigate scan slowness/freshness (separate from the shipped mobile layout).
- [ ] **Mobile ticker cockpit** — densest surface (deck letter-tabs pre-existing); candidate for a focused pass.

## Dormant services (intentionally NOT alerting after `4fce4de`)
- [ ] **llm-token-index** — activate by signing up at artificialanalysis.ai + setting `ARTIFICIAL_ANALYSIS_API_KEY` in the VPS `.env`.
- [ ] **preset-rebalance** — wire its weekly writer or leave dormant.
