# Radon Web — Codex Instructions

Applies under `web/`. Mirrors `web/CLAUDE.md`; prefer the Claude file if it is newer or more specific.

## Build / Cache / Auth

- Next.js 16 build uses `next build --experimental-build-mode=compile`.
- `app/error.tsx`, `app/[ticker]/not-found.tsx`, and `app/global-error.tsx` must stay pure JSX with plain `<a>` links, no `next/link`, no `useEffect`, and no `globals.css`.
- Every GET handler reading live disk state (`data/*.json`, `data/menthorq_cache/`) must export `dynamic = "force-dynamic"`.
- New `app/api/**/route.ts(x)` files must `export const radonCapability` (usually GET `"read"`). Pin: `web/tests/assistant-catalog-pin.test.ts`. Chat catalog is derived from that pin plus `scripts/api/assistant_catalog.py`; freshness: `web/tests/assistant-catalog-freshness.test.ts`. Next-only routes also need `web/lib/assistant/nextLoaders.ts`.
- Every client fetch hitting those routes must use `cache: "no-store"`. Contract test: `web/tests/api-routes-no-cache-contract.test.ts`.
- Middleware runs in Edge runtime; no `node:*` imports in `web/middleware.ts`.
- Use `RADON_AUTHLESS_TEST=1` for Playwright.
- WebSocket auth uses FastAPI tickets from `scripts/api/ws_ticket.py`.

## Brand / Theme

- Follow root Radon brand rules. Use tokens, not raw hex, except when defining tokens.
- Single theme source: `web/lib/ThemeContext.tsx`.
- `ThemeBootstrap.tsx` owns pre-paint `data-theme`; do not duplicate theme state.
- SSR theme is pinned to `dark`; do not read localStorage/matchMedia during first render.
- Do not hardcode `data-theme="dark"` in JSX.
- Do not mutate `<meta name="theme-color">` from client code; Next viewport metadata owns it.
- Use `color-mix(in srgb, var(--token) X%, transparent)` for token alpha. Do not bake raw `rgba(...)`.

## UI Verification

- UI changes need focused Vitest plus Playwright E2E when behavior changes.
- Visually verify rendered UI before done. Use `chrome-cdp` if available; otherwise Playwright screenshots.
- Do not click live submit/place buttons during UI verification. If unavoidable, qty 1 max, far-away limit, immediate cancel, then verify IB open orders.

## Combo / BAG Guardrails

- Never map combo `Order.action` from debit vs credit. Entry/open combo envelope stays `BUY`; per-leg actions define structure.
- `ComboLeg.action` = structure, not direction: LONG -> BUY, SHORT -> SELL. Flipping causes IB error 201.
- Structure change invalidates manual net price; recompute from normalized combo quote.
- Natural market uses cross-fields: BUY combo pays ASK on BUY legs and receives BID on SELL legs; SELL combo receives BID on BUY legs and pays ASK on SELL legs.
- Trace before fixing: chain builder -> `/api/orders/place` -> FastAPI bridge -> `scripts/ib_place_order.py`.
- Required regressions: unit payload/ratio/net-price semantics plus browser displayed net and submitted payload.
- IB may silently drop bearish risk reversal BAGs; workaround is split single-leg orders.

## Order-Risk Chokepoint

- Every order surface must render `<OrderRiskGate>` from `@/lib/order/risk`.
- `<OrderConfirmSummary>` accepts only `AugmentedOrderSummary`, produced by `useOrderRisk`.
- Do not hand-build summary literals or cast around the brand.
- `computeOrderRisk` and `augmentOrderLegsWithPortfolioCoverage` are module-private under `web/lib/order/risk/internal/`; production imports are forbidden.
- `portfolio === undefined` means pending coverage; disable submit.
- `portfolio === null` means no portfolio in scope; disable submit unless `state.okToSubmit === true`.
- Close paths pass `closeOut: { entryCostDollars }`; the hook owns cost-basis and realized P&L convention.
- Fuzz suite under `web/tests/fuzz/` protects coverage monotonicity, quantity linearity, stock-cover floor, and null/empty portfolio equivalence.

## Order Errors / Cancel / Modify

- IB rejection text may contain literal `<br>` tokens. Normalize to `\n` before prefix stripping in `web/lib/orderError.ts`.
- Never use `dangerouslySetInnerHTML` for broker text.
- Preserve upstream status/detail from FastAPI through Next routes; do not collapse broker errors to 500.
- Cancel/modify must use subprocess with original clientId; master client can see but cannot cancel/modify.
- Confirm against refreshed open-order snapshots; disappearance after cancel = success.
- Clear VOL fields before modify by resetting `volatility` / `volatilityType` to IB sentinels.

## Calculation Invariants

- Preserve credit/debit signs end to end. Never `Math.abs()` option values where sign matters.
- Limit-priced ticket max-gain / max-loss are structural at the limit. Do not subtract quoted half-spread or estimated exit. A short put's max gain is the credit.
- Daily change percent = Daily P&L / `|yesterday close value|`; never entry cost.
- Same-day positions use entry-cost baseline: Today P&L = Total P&L = `MV - EC`; ignore `ib_daily_pnl`.
- Entry-date fallback: blotter per-contract -> trade_log ticker/structure -> IB fills -> previous portfolio ticker/structure/expiry -> today. Never per-ticker blotter fallback.
- `PortfolioLeg.avg_cost` is per-contract for options and per-share for stocks. Do not multiply option `avg_cost` by 100 again.
- Journal lot-matched basis overrides IB's drifting VWAP; raw IB value is diagnostic.
- Per-leg P&L = `sign * (|MV| - |EC|)`. Position P&L is the sum.
- Open-position Return % = `(MV - EC) / verified risk capital * 100`: exact positive `max_risk` for defined risk; positive isolated broker-observed opening margin with v2 execution and sample provenance when exact max loss is unavailable; debit paid only when it is demonstrably the full max loss, including long stock. Estimated, legacy, or unlinked capital renders unavailable. Never divide an opening credit by its absolute premium.
- Price resolution: stock `prices[ticker].last`; single option `prices[optionKey].last`; spreads from signed leg prices; BAG via `resolveOrderLastPrice()` / `resolveOrderPriceData()`. Show `---` if unavailable.
- Exposure delta sign: normalize provider option delta to canonical option delta first, then apply position direction. LONG Call +, SHORT Call -, LONG Put -, SHORT Put +. Positive provider put deltas may be call-equivalent; convert with `delta - 1` before applying LONG/SHORT.
- Margin warning thresholds live in `web/lib/marginWarning.ts`; toasts are persistent and fire only on transition to worse rank.

## Key Component Notes

- Every product `<table>` uses `SortTh` + `useSort`. Structural exceptions need `data-sortable-exempt` (`chain-layout` | `matrix` | `markdown` | `chrome` | `kit-demo`). Contract: `web/tests/sortable-table-contract.test.ts`.
- Options chain sticky header requires separate borders, sticky header backgrounds, and z-index on `thead`.
- Column visibility persists under `localStorage` key `radon:columns:<tableId>`.
- Dashboard uses 50/50 grid with sticky internal newsfeed rail.
- Mobile shell activates at `<=640px`; PWA service worker must bypass `/api`, `/_next/data`, and `/ws`.
