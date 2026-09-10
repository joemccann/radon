import type { Page } from "@playwright/test";
import { createRequire } from "node:module";
import { CorFixture, CreditSpreadFixture, CurveFixture, DivyieldFixture, HhlevFixture, HyadFixture, IeiHygFixture, IvSpreadFixture, IvrankFixture, MaRatioFixture, MarginDebtFixture, Skew2dFixture, SkewFixture, StraddleFixture, StreaksFixture, VixcorFixture, VixtsFixture } from "./clear-indicator-fixtures";
import { AtsFixture, BreadthFixture, CotFixture, ShortFixture } from "./clear-positioning-fixtures";
import { CtaFixture, DiscoverFixture, GarchFixture, JournalFixture, LeapFixture, PreferencesFixture, VolConeFixture } from "./clear-workspace-fixtures";
import { buildDemoOptionChain, buildDemoOptionExpirations, buildDemoOptionsExposure } from "../lib/demo/fixtures/options";
// Playwright's CommonJS transform supports the application's JSON calendar
// import, unlike native ESM's required JSON import attributes.
const require = createRequire(import.meta.url);
const { buildDemoPerformance } = require("../lib/demo/fixtures/performance") as typeof import("../lib/demo/fixtures/performance");
const { buildDemoOrders } = require("../lib/demo/fixtures/orders") as typeof import("../lib/demo/fixtures/orders");
const { buildCompleteRvRatioFixture } = require("../tests/rv-ratio-fixture") as typeof import("../tests/rv-ratio-fixture");
const { buildDemoFlowReport } = require("../lib/demo/fixtures/flowAnalysis") as typeof import("../lib/demo/fixtures/flowAnalysis");
const { buildDemoThetaHarvester } = require("../lib/demo/fixtures/thetaHarvester") as typeof import("../lib/demo/fixtures/thetaHarvester");
const { buildDemoBpiFixture, buildDemoCriFixture, buildDemoDispersionFixture, buildDemoGammaRotationFixture, buildDemoGexFixture, buildDemoTrinFixture, buildDemoVcgFixture } = require("../lib/demo/fixtures/regime") as typeof import("../lib/demo/fixtures/regime");

export const CLEAR_FIXTURE_TIME = "2026-09-04T18:00:00.000Z";
const now = new Date(CLEAR_FIXTURE_TIME);

export const CLEAR_PORTFOLIO = {
  bankroll: 1_246_820.42,
  peak_value: 1_260_000,
  last_sync: CLEAR_FIXTURE_TIME,
  total_deployed_pct: 3.85,
  total_deployed_dollars: 48_000,
  remaining_capacity_pct: 96.15,
  position_count: 2,
  defined_risk_count: 2,
  undefined_risk_count: 0,
  avg_kelly_optimal: null,
  exposure: {},
  violations: [],
  positions: [
    { id: 1, ticker: "AAPL", structure: "Long Stock", structure_type: "Stock", risk_profile: "defined", expiry: null, contracts: 100, direction: "LONG", entry_cost: 22_000, max_risk: 22_000, market_value: 23_218, ib_daily_pnl: 184, entry_date: "2026-08-03", kelly_optimal: null, target: null, stop: null, legs: [{ direction: "LONG", contracts: 100, type: "Stock", strike: null, entry_cost: 22_000, avg_cost: 220, market_price: 232.18, market_value: 23_218 }] },
    { id: 2, ticker: "MSFT", structure: "Long Call", structure_type: "Long Call", risk_profile: "defined", expiry: "2027-01-15", contracts: 2, direction: "LONG", entry_cost: 1_680, max_risk: 1_680, market_value: 1_810, ib_daily_pnl: 42, entry_date: "2026-08-10", kelly_optimal: null, target: null, stop: null, legs: [{ direction: "LONG", contracts: 2, type: "Call", strike: 530, entry_cost: 1_680, avg_cost: 840, market_price: 9.05, market_value: 1_810 }] },
  ],
  account_summary: { net_liquidation: 1_246_820.42, daily_pnl: 2_840.32, unrealized_pnl: 1_348, realized_pnl: 0, settled_cash: 802_840, maintenance_margin: 164_450, excess_liquidity: 1_082_370.42, buying_power: 2_144_850, dividends: 0 },
};

const fixtures: Record<string, unknown> = {
  "/api/portfolio": CLEAR_PORTFOLIO,
  "/api/orders": buildDemoOrders({ last_sync: CLEAR_FIXTURE_TIME, open_orders: [], executed_orders: [], open_count: 0, executed_count: 0 }, now),
  "/api/performance": buildDemoPerformance(now),
  "/api/regime": buildDemoCriFixture(now),
  "/api/vcg": buildDemoVcgFixture(now),
  "/api/gex": buildDemoGexFixture(now),
  "/api/gamma-rotation": buildDemoGammaRotationFixture(now),
  "/api/dispersion": buildDemoDispersionFixture(now),
  "/api/trin": buildDemoTrinFixture(now),
  "/api/bpi": buildDemoBpiFixture(now),
  "/api/cor": CorFixture(),
  "/api/skew": SkewFixture(),
  "/api/skew2d": Skew2dFixture(),
  "/api/straddle": StraddleFixture(),
  "/api/margin-debt": MarginDebtFixture(),
  "/api/ivrank": IvrankFixture(),
  "/api/iv-spread": IvSpreadFixture(),
  "/api/vixts": VixtsFixture(),
  "/api/vixcor": VixcorFixture(),
  "/api/hyad": HyadFixture(),
  "/api/hhlev": HhlevFixture(),
  "/api/ma-ratio": MaRatioFixture(),
  "/api/credit-spread": CreditSpreadFixture(),
  "/api/iei-hyg": IeiHygFixture(),
  "/api/divyield": DivyieldFixture(),
  "/api/streaks": StreaksFixture(),
  "/api/yield-curve": CurveFixture(),
  "/api/breadth": BreadthFixture(),
  "/api/equibles-cot-positioning": CotFixture(),
  "/api/equibles-ats-venue-share": AtsFixture(),
  "/api/equibles-short-crowding": ShortFixture(),
  "/api/llm-token-index": { count: 60, days: 180, rows: Array.from({ length: 60 }, (_, i) => ({ date: new Date(now.getTime() - (59 - i) * 86_400_000).toISOString().slice(0, 10), index_value: 1 + i * 0.005, raw_avg_usd: 4 + i * 0.02, methodology_version: 1 })) },
  "/api/backtest/cri": { strategy: "cri", label: "Crash Risk Index (CRI)", status: "ok", wired: true, horizon: 5, trades: Array.from({ length: 24 }, (_, i) => ({ date: new Date(now.getTime() - (23 - i) * 7 * 86_400_000).toISOString().slice(0, 10), position: 1, forward_return: 0.01, gross_return: 0.01, net_return: 0.009 })), metrics: { n_trades: 24, sharpe: 1.35, sortino: 1.6, calmar: 1.1, max_drawdown: -0.04, hit_rate: 0.625, expectancy: 0.009, annualized_return: 0.14, equity_curve: Array.from({ length: 24 }, (_, i) => 100 + i * 0.9) } },
  "/api/scanner/theta": buildDemoThetaHarvester({ now }),
  "/api/scanner": { scan_time: CLEAR_FIXTURE_TIME, tickers_scanned: 50, signals_found: 1, top_signals: [{ ticker: "AAPL", sector: "Technology", score: 72.5, signal: "STRONG", direction: "ACCUMULATION", strength: 78, buy_ratio: 0.68, num_prints: 24, sustained_days: 3, recent_direction: "ACCUMULATION", recent_strength: 75 }] },
  "/api/discover": DiscoverFixture(),
  "/api/leap": LeapFixture(),
  "/api/garch-convergence": GarchFixture(),
  "/api/vol-cone": VolConeFixture(),
  "/api/flow-analysis": { analysis_time: CLEAR_FIXTURE_TIME, positions_scanned: 1, supports: [{ ticker: "AAPL", position: "Long Stock", direction: "LONG", flow_direction: "ACCUMULATION", flow_label: "Supports", flow_class: "supports", strength: 78, buy_ratio: 0.68, note: "Sample accumulation supports the open stock position." }], against: [], watch: [], neutral: [] },
  "/api/profile": { username: "Sample Operator", avatar_url: null, email: "sample@example.invalid" },
  "/api/watchlist": { watchlist: [{ id: "clear-aapl", symbol: "AAPL", sector: "Technology", added_at: CLEAR_FIXTURE_TIME }, { id: "clear-msft", symbol: "MSFT", sector: "Technology", added_at: CLEAR_FIXTURE_TIME }] },
  "/api/blotter": { as_of: CLEAR_FIXTURE_TIME, summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] },
  "/api/journal": JournalFixture(),
  "/api/service-health": { services: [] },
  "/api/ib-status": { connected: false },
  "/api/flex-token": { ok: true, days_until_expiry: 14 },
  "/api/menthorq/cta": CtaFixture(),
  "/api/headlines": { items: [], headlines: [] },
  "/api/preferences": PreferencesFixture(),
  "/api/alerts": { rules: [{ id: "clear-alert", ticker: "AAPL", metric: "flow_strength", op: ">", threshold: 70, channel: "service_health", enabled: true, created_at: CLEAR_FIXTURE_TIME, last_fired_at: null }] },
  "/api/catalysts": { catalysts: [], events: [] },
};

/** Browser-bound fixtures: no API request or mutation reaches a live service. */
export async function installClearFixtures(page: Page) {
  const apiRequests: string[] = [];
  await page.clock.setFixedTime(now);
  await page.addInitScript(() => {
    localStorage.setItem("theme", "light");
    // Client-only SDK fixture. It exercises identity-scoped watchlist/profile
    // state without impersonating a principal at middleware or route guards.
    // Every API request is intercepted below and never leaves this browser.
    const user = { id: "clear-browser-sample", username: "Sample Operator", fullName: "Sample Operator", imageUrl: "", primaryEmailAddress: { emailAddress: "sample@example.invalid" }, publicMetadata: {}, organizationMemberships: [] };
    const session = { id: "clear-browser-session", status: "active", user, lastActiveToken: { jwt: { claims: { sub: user.id, sid: "clear-browser-session", metadata: {} } } }, getToken: async () => null, factorVerificationAge: [0, 0] };
    const state = { user, session, organization: null, client: { sessions: [session], activeSessions: [session] } };
    Object.defineProperty(window, "Clerk", { configurable: true, value: {
      ...state,
      loaded: true,
      status: "ready",
      __internal_lastEmittedResources: state,
      isSignedIn: true,
      load: async () => {},
      addListener: (listener: (value: typeof state) => void) => { listener(state); return () => {}; },
      on: () => {}, off: () => {},
      __internal_updateProps: async () => {},
      __internal_setIsSatellite: () => {},
      __internal_setProxyUrl: () => {},
      __internal_setDomain: () => {},
      __unstable__updateProps: () => {},
      signOut: async () => {},
      telemetry: { record: () => {} },
    } });
  });
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    apiRequests.push(`${request.method()} ${path}`);
    const symbol = url.searchParams.get("symbol") ?? url.searchParams.get("ticker") ?? "AAPL";
    let body = fixtures[path];
    if (path === "/api/options/expirations") body = buildDemoOptionExpirations(symbol, now);
    if (path === "/api/options/chain") body = buildDemoOptionChain(symbol, url.searchParams.get("expiry") ?? undefined, now);
    if (path === "/api/options/exposure") body = buildDemoOptionsExposure(symbol, "eod", now);
    if (path === "/api/options/rv-ratio") body = buildCompleteRvRatioFixture({ symbol, endDate: "2026-09-04" });
    if (path === "/api/streaks") body = StreaksFixture(symbol);
    if (path.startsWith("/api/flow-analysis/")) body = buildDemoFlowReport(path.split("/").at(-1) ?? "AAPL", now);
    if (path === "/api/ticker/info") body = { ticker: symbol, uw_info: { name: `${symbol} Sample Company`, sector: "Technology", description: "Isolated browser-test sample." }, stock_state: { open: 230, high: 234, low: 229, close: 232.18, prev_close: 230.34, volume: 1_000_000 }, profile: {}, stats: {} };
    // Unknown data surfaces explicitly exercise their unavailable-data state.
    // Returning a success-shaped empty object would conceal contract errors.
    await route.fulfill({ status: body === undefined ? 503 : 200, contentType: "application/json", headers: { "Cache-Control": "no-store" }, body: JSON.stringify(body ?? { error: "No sample measurement is available for this source.", missing: true }) });
  });
  return apiRequests;
}
