/** Every App Router page is covered explicitly, including redirects and gates. */
export type ClearRouteCase = {
  source: string;
  path: string;
  selector?: string;
  text?: string;
  destination?: string;
  regimeTab?: string;
  guarded?: boolean;
};

const regimePages = [
  "ats", "backtest", "bpi", "breadth", "cor", "cot", "credit", "cri",
  "curve", "dispersion", "divyield", "gex", "grg", "hhlev", "hyad", "iei-hyg",
  "iv-spread", "ivrank", "llm", "ma-ratio", "margin", "short", "skew", "skew2d",
  "straddle", "streaks", "trin", "vcg", "vixcor", "vixts",
] as const;

const regimeLoaded: Record<string, string> = {
  ats: '[data-testid="ats-venue-share-table"]', backtest: ".backtest-chart", bpi: '[data-testid="bpi-chart-section"]', breadth: '[data-testid="breadth-history-chart-section"]',
  cor: '[data-testid="cor-chart-section"]', cot: '[data-testid="cot-chart-section"]', credit: '[data-testid="credit-spread-chart-section"]', cri: ".regime-hero, .m-regime-headline",
  curve: '[data-testid="yield-curve-chart-section"]', dispersion: '[data-testid="dispersion-chart-section"]', divyield: '[data-testid="divyield-chart-section"]',
  gex: '[data-testid="gex-laplace-chart"]', grg: '[data-testid="grg-chart"]', hhlev: '[data-testid="hhlev-chart-section"]', hyad: '[data-testid="hyad-chart-section"]',
  "iei-hyg": '[data-testid="iei-hyg-chart-section"]', "iv-spread": '[data-testid="iv-spread-chart-section"]', ivrank: '[data-testid="ivrank-chart-section"]',
  llm: '[data-testid="llm-token-index-chart"]', "ma-ratio": '[data-testid="ma-ratio-chart-section"]', margin: '[data-testid="margin-debt-chart-section"]',
  short: '[data-testid="short-crowding-table"]', skew: '[data-testid="skew-chart-section"]', skew2d: '[data-testid="skew2d-chart-section"]',
  straddle: '[data-testid="straddle-chart-section"]', streaks: '[data-testid="streaks-chart-section"]', trin: '[data-testid="trin-chart-section"]',
  vcg: '[data-testid="vcg-history-chart-section"]', vixcor: '[data-testid="vixcor-chart-section"]', vixts: '[data-testid="vixts-chart-section"]',
};

export const CLEAR_ROUTE_CASES: ClearRouteCase[] = [
  { source: "app/page.tsx", path: "/", selector: '[role="slider"][aria-label="Inspect account value history"]', text: "$1,246,820.42" },
  { source: "app/dashboard/page.tsx", path: "/dashboard", selector: '[role="slider"][aria-label="Inspect account value history"]', text: "$1,246,820.42" },
  { source: "app/portfolio/page.tsx", path: "/portfolio", selector: '[data-testid="position-table"], [data-testid="mobile-position-list"]', text: "AAPL" },
  { source: "app/performance/page.tsx", path: "/performance", selector: '[data-testid="performance-panel"]' },
  { source: "app/orders/page.tsx", path: "/orders", selector: '[data-testid="orders-command-strip"]' },
  { source: "app/scanner/page.tsx", path: "/scanner", selector: '[data-testid="flow-order-link-AAPL"], [data-testid="mobile-scanner-list"]' },
  { source: "app/watchlist/page.tsx", path: "/watchlist", selector: '[data-testid="watchlist-row-AAPL"]' },
  { source: "app/discover/page.tsx", path: "/discover", destination: "/scanner?mode=discover", selector: '[data-testid="discover-order-link-MSFT"]' },
  { source: "app/flow-analysis/page.tsx", path: "/flow-analysis", selector: '[data-testid="mobile-flow-list"], .table-wrap:has(td:text-is("Long Stock"))', text: "AAPL" },
  { source: "app/flow-analysis/[ticker]/page.tsx", path: "/flow-analysis/AAPL", selector: '[data-testid="ticker-flow-report"]', text: "BEARISH" },
  { source: "app/[ticker]/page.tsx", path: "/AAPL?tab=book", selector: ".book-feed-pill", text: "SMART DEPTH" },
  { source: "app/options/page.tsx", path: "/options", selector: '[data-testid="options-workspace"]' },
  { source: "app/options/net-gex/page.tsx", path: "/options/net-gex?symbol=AAPL", selector: '[data-testid="options-exposure-table-wrap"]' },
  { source: "app/options/rv-ratio/page.tsx", path: "/options/rv-ratio?symbol=AAPL", selector: '[data-testid="rv-ratio-stats"]' },
  { source: "app/options/exposure/page.tsx", path: "/options/exposure?symbol=AAPL", destination: "/options/net-gex?symbol=AAPL", selector: '[data-testid="options-exposure-table-wrap"]' },
  { source: "app/journal/page.tsx", path: "/journal", selector: '[data-testid="journal-trade-count"]', text: "MSFT" },
  { source: "app/cta/page.tsx", path: "/cta", selector: '[data-testid="vol-targeting-model"]', text: "SPX" },
  { source: "app/alerts/page.tsx", path: "/alerts", selector: ".alerts-rule", text: "Flow Strength > 70" },
  { source: "app/workflow/page.tsx", path: "/workflow", selector: ".react-flow", text: "Flow Pipeline Composer" },
  { source: "app/preferences/page.tsx", path: "/preferences", selector: '[data-testid="preference-input-RADON_MAX_ORDER_QTY"]' },
  { source: "app/profile/page.tsx", path: "/profile", selector: ".profile-field__input" },
  { source: "app/regime/page.tsx", path: "/regime", regimeTab: "cri", selector: regimeLoaded.cri },
  ...regimePages.map((tab) => ({ source: `app/regime/${tab}/page.tsx`, path: `/regime/${tab}`, regimeTab: tab, selector: regimeLoaded[tab] })),
  { source: "app/regime/vol-cone/page.tsx", path: "/regime/vol-cone", destination: "/scanner?mode=vol-cone", selector: '[data-testid="vol-cone-chart-section"]' },
  { source: "app/internals/page.tsx", path: "/internals", destination: "/regime/cri", selector: regimeLoaded.cri, regimeTab: "cri" },
  { source: "app/setup/page.tsx", path: "/setup", selector: '[data-testid="setup-wizard"]' },
  { source: "app/demo-pending/page.tsx", path: "/demo-pending", text: "Setting up your demo" },
  { source: "app/trial-expired/page.tsx", path: "/trial-expired", text: "Your demo has ended" },
  { source: "app/kit/page.tsx", path: "/kit", text: "Radon Contributor Kit / Component Spec" },
  { source: "app/admin/page.tsx", path: "/admin", guarded: true },
  { source: "app/sign-in/[[...sign-in]]/page.tsx", path: "/sign-in", guarded: true },
  { source: "app/sign-up/[[...sign-up]]/page.tsx", path: "/sign-up", guarded: true },
];
