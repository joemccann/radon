// Snapshot the existing source-shaped Playwright fixtures for the native ESM SDK.
// Its loader does not resolve the application's extensionless TS imports.
import { writeFileSync } from 'node:fs';
import { installClearFixtures } from '../e2e/clear-fixtures';
import { CLEAR_ROUTE_CASES } from '../e2e/clear-route-inventory';
let handler: any;
let init = '';
await installClearFixtures({ clock: { setFixedTime: async () => {} }, addInitScript: async (fn: Function) => { init = `(${fn.toString()})();`; }, route: async (_: string, callback: any) => { handler = callback; } } as any);
const paths = ['/api/portfolio','/api/orders','/api/performance','/api/regime','/api/vcg','/api/gex','/api/gamma-rotation','/api/dispersion','/api/trin','/api/bpi','/api/cor','/api/skew','/api/skew2d','/api/straddle','/api/margin-debt','/api/ivrank','/api/iv-spread','/api/vixts','/api/vixcor','/api/hyad','/api/hhlev','/api/ma-ratio','/api/rsi-oversold','/api/calm-streak','/api/credit-spread','/api/iei-hyg','/api/credit-vix','/api/divyield','/api/streaks','/api/yield-curve','/api/breadth','/api/equibles-cot-positioning','/api/equibles-ats-venue-share','/api/equibles-short-crowding','/api/llm-token-index','/api/backtest/cri','/api/scanner/theta','/api/scanner','/api/discover','/api/leap','/api/garch-convergence','/api/vol-cone','/api/flow-analysis','/api/profile','/api/watchlist','/api/blotter','/api/journal','/api/service-health','/api/ib-status','/api/flex-token','/api/menthorq/cta','/api/headlines','/api/preferences','/api/alerts','/api/catalysts','/api/options/expirations','/api/options/chain','/api/options/exposure','/api/options/rv-ratio','/api/flow-analysis/AAPL','/api/ticker/info'];
const fixtures: Record<string, unknown> = {};
for (const path of paths) await handler({ request: () => ({ url: () => `http://localhost:3341${path}?symbol=${path === "/api/streaks" ? "SPY" : "AAPL"}`, method: () => 'GET' }), fulfill: async (response: any) => { fixtures[path] = JSON.parse(response.body); } });
writeFileSync(new URL('./fixtures.json', import.meta.url), JSON.stringify({ init, fixtures, routes: CLEAR_ROUTE_CASES }, null, 2) + '\n');
