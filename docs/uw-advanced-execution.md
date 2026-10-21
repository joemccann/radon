# UW Advanced execution and rollout

## Delivery

The October 14 roadmap now has a private research domain: shared REST admission, durable stream collection, Advanced research snapshots, Profile configuration, authenticated API/MCP reads and a desktop/mobile Flow report panel. IB remains the quote, portfolio and execution authority.

This is an implementation checkpoint, not completed production promotion. Live candidate ranking, event-driven invalidation of existing news/evaluation caches, shadow parity and broad tape capture remain gated on pilot measurements. New observations do not enter trade execution automatically. Research adapters and archived observations do not establish survivorship or corporate-action parity for an existing backtest.

## Account admission evidence (2026-10-21)

Bounded REST probes returned HTTP 200 for economic-calendar, AAPL company profile, futures contracts, multi-leg trades, Options Pulse and volatility context. Responses omitted quota/count headers; neither unlimited allowance nor higher RPM is verified. Conservative 120 RPM / 40,000 local daily allowance remains. An initial SPY company-profile 404 was followed by AAPL 200; an ETF response cannot establish issuer-profile denial.

No websocket was opened locally because a second host could conflict with the token's single-owner feed. Initial public and tailnet SSH attempts timed out; read access was restored through the existing Tailscale transport. The new units are not installed or active, and the credential resides in the protected app-plane store rather than the readable host environment. Same-VPS channel ACKs, service activation, lag measurements and staged promotion remain unverified.

## Dependency graph

- T1 account/config receipts depends_on: []
- T2 REST governor depends_on: [T1]
- T3 stream and durable store depends_on: [T1]
- T4 Advanced research depends_on: [T1]
- T5 private API/UI/MCP depends_on: [T2,T3,T4]
- T6 measured promotion depends_on: [T5]

T1 -> {T2,T3,T4} -> T5 -> T6. Unknown account limits block increased allowance and verified-scope claims; they do not prevent disabled-by-default code delivery.

## Activation

After exact-head CI and production deployment, use Profile > Credentials > Unusual Whales. UW_TOKEN is the provider credential; no vendor secret reaches the browser or MCP. Select channels and scheduled tickers, then obtain a same-VPS channel ACK before activation. Restart the research collector through the existing admin service controller and enable the hourly timer through controlled operations.

Inspect feed coverage and health, record queue/lag/disk metrics, compare against existing REST calculations, then promote one watchlist before the portfolio. Issuer, macro and futures pilots depend on their verified access. Channel-wide freshness is labeled separately from each observation's source and receipt timestamps; completeness and reconciliation basis remain visible.

## Full tape capacity decision

Do not enable market-wide option trades by default. Bounded queue/storage watermarks and stream counters support a selected-ticker pilot. No live rate, compression or disk measurements were obtained, so U11 does not authorize broad capture or external archival. Kafka and historical tape purchases remain separate decisions.

## Verification

Focused Python fixtures cover admission, cache/budget behavior, stream semantics/storage, research contracts and API boundaries. Vitest covers the proxy, typed observations and provenance. Playwright covers desktop/mobile retained data, gaps, signed strategies and explicit refresh. All suites run on GitHub runners; local checks are static only. Exact-head CI, screenshot artifacts and independent Sol/Luna review are the release evidence.

## Risk and rollback

The collectors use the existing app-plane secret-store grant and shared data mount. They are fully trusted consumers of the staged master key and data, not cryptographically UW-only principals. A server-side child enforces the web proxy's wall deadline; no vendor token enters its arguments.

Set RADON_UW_STREAM_ENABLED=0 and stop the research timer to retain snapshots without live collection. Reverting restores the prior Next request path. No broker storage or order-risk migration is introduced. Preserve raw journals and archives for provenance; partial observations must not be promoted to complete coverage.
