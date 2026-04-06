export type ProofItem = {
  label: string;
  value: string;
  detail: string;
  tone?: "core" | "strong" | "neutral";
};

export type StrategyItem = {
  name: string;
  description: string;
  edge: string;
  instruments: string;
  holdPeriod: string;
  winRate: string;
  riskType: "Defined Risk" | "Undefined Risk" | "Overlay";
  command: string;
  tone?: "core" | "strong" | "warn" | "violet";
};

export type ExecutionItem = {
  step: string;
  summary: string;
  metadata: string;
};

export type SurfaceItem = {
  name: string;
  headline: string;
  metrics: Array<{ label: string; value: string }>;
  details: string[];
};

export type AuditItem = {
  title: string;
  detail: string;
};

export const headerLinks = [
  { href: "#strategies", label: "Strategies" },
  { href: "#execution", label: "Execution" },
  { href: "#surfaces", label: "Surfaces" },
  { href: "#methodology", label: "Methodology" },
];

export const proofItems: ProofItem[] = [
  {
    label: "Active Strategies",
    value: "7",
    detail: "Defined, undefined, and overlay structures surfaced in one terminal.",
    tone: "core",
  },
  {
    label: "Option Structures",
    value: "58",
    detail: "Catalogued structures with guard decisions, risk profiles, and P&L attribution labels.",
    tone: "strong",
  },
  {
    label: "Execution Path",
    value: "5-Step",
    detail: "Evaluate, structure, Kelly, execute, and track with the same operator context.",
    tone: "strong",
  },
  {
    label: "Explainability",
    value: "Full",
    detail: "Metrics, methodology, and signal states stay inspectable instead of hidden.",
    tone: "neutral",
  },
  {
    label: "Control Surface",
    value: "Open",
    detail: "Source-visible workflows for traders who do not outsource conviction.",
    tone: "core",
  },
];

export const heroCommandItems = [
  { label: "Dark Pool Flow", state: "Defined", command: "evaluate AAPL" },
  { label: "LEAP IV Mispricing", state: "Defined", command: "leap-scan NVDA" },
  { label: "GEX Levels", state: "Overlay", command: "gex-scan" },
];

export const heroMetrics = [
  { label: "COR1M", value: "28.97", detail: "Implied correlation" },
  { label: "VIX", value: "22.77", detail: "Volatility state" },
  { label: "Kelly Cap", value: "7.5%", detail: "Sizing discipline" },
  { label: "Signal Stack", value: "7", detail: "Deployable modules" },
];

export type ModuleTab = "flow" | "performance" | "structure" | "execution";

export type ModuleContent = {
  metrics: Array<{ label: string; value: string; detail: string }>;
  commands: Array<{ label: string; state: string; command: string }>;
  commandLabel: string;
  commandPill: string;
};

export const moduleContents: Record<ModuleTab, ModuleContent> = {
  flow: {
    metrics: [
      { label: "COR1M", value: "28.97", detail: "Implied correlation" },
      { label: "VIX", value: "22.77", detail: "Volatility state" },
      { label: "Kelly Cap", value: "7.5%", detail: "Sizing discipline" },
      { label: "Signal Stack", value: "7", detail: "Deployable modules" },
    ],
    commands: [
      { label: "Dark Pool Flow", state: "Defined", command: "evaluate AAPL" },
      { label: "LEAP IV Mispricing", state: "Defined", command: "leap-scan NVDA" },
      { label: "Crash Risk Index", state: "Overlay", command: "cri-scan" },
    ],
    commandLabel: "Command Surface",
    commandPill: "Operator Visible",
  },
  performance: {
    metrics: [
      { label: "Sharpe", value: "1.84", detail: "Risk-adjusted return" },
      { label: "Sortino", value: "2.61", detail: "Downside deviation" },
      { label: "Max DD", value: "-8.2%", detail: "Peak drawdown" },
      { label: "Win Rate", value: "62%", detail: "Closed positions" },
    ],
    commands: [
      { label: "YTD Equity Curve", state: "Tracked", command: "portfolio" },
      { label: "Trade Journal", state: "Append-only", command: "journal" },
      { label: "Fill Blotter", state: "Live", command: "blotter" },
    ],
    commandLabel: "Performance Surface",
    commandPill: "Net Liq Anchored",
  },
  structure: {
    metrics: [
      { label: "CRI", value: "34.2", detail: "Crash risk index" },
      { label: "VVIX", value: "96.4", detail: "Vol-of-vol state" },
      { label: "RVOL", value: "18.7%", detail: "Realized volatility" },
      { label: "Regime", value: "Calm", detail: "Current classification" },
    ],
    commands: [
      { label: "Regime Monitor", state: "Continuous", command: "cri-scan" },
      { label: "Vol-Credit Gap", state: "Overlay", command: "vcg" },
      { label: "GEX Levels", state: "Dealer Gamma", command: "gex-scan" },
    ],
    commandLabel: "Structure Surface",
    commandPill: "Regime Aware",
  },
  execution: {
    metrics: [
      { label: "Gates", value: "4", detail: "Sequential filters" },
      { label: "Fill Path", value: "IB", detail: "Direct routing" },
      { label: "Audit", value: "Full", detail: "Post-trade log" },
      { label: "Guard", value: "Active", detail: "Naked short block" },
    ],
    commands: [
      { label: "Order Placement", state: "Operator-led", command: "execute" },
      { label: "Position Sync", state: "IB Source", command: "sync" },
      { label: "Exit Service", state: "Pending", command: "exit-orders" },
    ],
    commandLabel: "Execution Surface",
    commandPill: "Gate Protected",
  },
};

export const strategies: StrategyItem[] = [
  {
    name: "Dark Pool Flow",
    description:
      "Institutional flow detection for passive accumulation or distribution that has not yet expressed through the lit market.",
    edge: "Passive buying and selling in dark pools often precedes the visible price move by days to weeks.",
    instruments:
      "ATM and OTM calls or puts with 2 to 6 week expiry, often expressed through verticals.",
    holdPeriod: "2 to 6 weeks",
    winRate: "20 to 40%",
    riskType: "Defined Risk",
    command: "scan -> evaluate [TICKER]",
    tone: "core",
  },
  {
    name: "LEAP IV Mispricing",
    description:
      "Long-dated volatility dislocations where realized volatility exceeds LEAP IV and the market has not repriced the option yet.",
    edge: "HV20 and HV60 exceed LEAP IV by at least 15 points while IV rank stays compressed.",
    instruments: "6 to 18 month LEAP calls, usually 30 to 50 delta.",
    holdPeriod: "Weeks to 9 months",
    winRate: "40 to 55%",
    riskType: "Defined Risk",
    command: "leap-scan [TICKERS]",
    tone: "strong",
  },
  {
    name: "GARCH Convergence Spreads",
    description:
      "Cross-asset vega arbitrage when correlated assets reprice implied volatility at different speeds after a catalyst.",
    edge: "Volatility lag between related assets creates measurable divergence from the 60-day mean.",
    instruments: "30 to 40 delta calls on the lagger, usually 3 to 6 month expiry.",
    holdPeriod: "2 to 8 weeks",
    winRate: "50 to 65%",
    riskType: "Defined Risk",
    command: "garch-convergence [TICKERS]",
    tone: "core",
  },
  {
    name: "Risk Reversal",
    description:
      "Skew exploitation that sells the rich side of the surface to fund directional convexity on the cheap side.",
    edge: "OTM puts often trade at materially higher implied volatility than equivalent-delta OTM calls.",
    instruments: "Sell OTM put plus buy OTM call, generally 2 to 8 week expiry.",
    holdPeriod: "2 to 8 weeks",
    winRate: "30 to 50%",
    riskType: "Undefined Risk",
    command: "risk-reversal [TICKER]",
    tone: "warn",
  },
  {
    name: "Volatility-Credit Gap",
    description:
      "Portfolio hedge overlay when volatility reprices faster than cash credit and the gap becomes statistically dislocated.",
    edge: "VVIX > 110 with stable credit plus a >2 sigma residual exposes artificial calm in HYG and JNK.",
    instruments: "Short-dated HYG puts or bear put spreads.",
    holdPeriod: "1 to 5 days",
    winRate: "Overlay",
    riskType: "Overlay",
    command: "vcg",
    tone: "violet",
  },
  {
    name: "Crash Risk Index",
    description:
      "CTA deleveraging detector built from volatility, implied correlation, and momentum stress signals.",
    edge: "VIX, VVIX, COR1M, and momentum convergence often precede forced systematic selling over 3 to 5 days.",
    instruments: "SPY puts, bear put spreads, and tail hedges.",
    holdPeriod: "3 to 5 days",
    winRate: "Overlay",
    riskType: "Overlay",
    command: "cri-scan",
    tone: "violet",
  },
  {
    name: "Gamma Exposure Levels",
    description:
      "Dealer gamma positioning by strike to identify price magnets, accelerators, and the GEX flip where market maker hedging shifts from stabilizing to destabilizing.",
    edge: "Positive gamma zones attract price as dealers buy dips and sell rallies. Negative gamma zones amplify moves as dealers hedge in the same direction as price.",
    instruments: "SPX and SPY options. Informs structure selection and entry timing.",
    holdPeriod: "Intraday to 3 days",
    winRate: "Overlay",
    riskType: "Overlay",
    command: "gex-scan",
    tone: "core",
  },
];

export const executionItems: ExecutionItem[] = [
  {
    step: "Evaluate",
    summary:
      "Every candidate is forced through a structured gate sequence instead of discretionary skipping.",
    metadata: "Ticker, seasonality, analysts, news & catalysts, dark pool, options, OI",
  },
  {
    step: "Structure",
    summary:
      "Directional view is converted into a defined or explicitly managed options structure with convexity in mind. 58 catalogued structures enforce consistent classification, risk labeling, and P&L attribution.",
    metadata: "Expiry, delta, spread width, max loss, target R:R",
  },
  {
    step: "Kelly",
    summary:
      "Position size is bounded by bankroll logic so conviction does not outrun survivability.",
    metadata: "Kelly sizing, caps, deployed capital, portfolio fit",
  },
  {
    step: "Execute",
    summary:
      "Execution remains visible and operator-led. Naked short protection blocks uncovered calls and stock shorts before the order reaches the exchange.",
    metadata: "IB quotes, live pricing, fill path, naked short guard, trade log",
  },
  {
    step: "Track",
    summary:
      "Performance, regime, and exposure stay linked after entry so the thesis can be audited under stress.",
    metadata: "Portfolio state, YTD metrics, flow drift, regime overlay",
  },
];

export const surfaceItems: SurfaceItem[] = [
  {
    name: "Radon Flow",
    headline: "Detect non-random positioning before the lit move becomes obvious.",
    metrics: [
      { label: "Signal Classes", value: "3" },
      { label: "Workflow", value: "Scan -> Evaluate" },
    ],
    details: [
      "Dark pool flow scored against sustained direction and buy ratio.",
      "Options flow, OI changes, and news catalysts brought into the same decision surface.",
      "Material events like buybacks and M&A surface alongside flow to explain institutional patterns.",
      "Candidates ranked for action instead of dumped into a watchlist graveyard.",
    ],
  },
  {
    name: "Radon Performance",
    headline: "Reconstruct the YTD equity curve and tie it back to current net liquidation.",
    metrics: [
      { label: "Metrics", value: "Institutional" },
      { label: "Anchor", value: "Net Liq" },
    ],
    details: [
      "Sharpe, Sortino, beta, drawdown, VaR, and benchmark-relative context.",
      "Every core card exposes calculation logic in place.",
      "Warnings surface when reconstruction assumptions or data gaps matter.",
    ],
  },
  {
    name: "Radon Structure",
    headline: "Track regime pressure, implied correlation, and portfolio-level stress context.",
    metrics: [
      { label: "COR1M", value: "Integrated" },
      { label: "CTA Model", value: "Visible" },
    ],
    details: [
      "CRI overlays VIX, VVIX, COR1M, and momentum into a single stress lens.",
      "Crash conditions are explicit rather than implied through vague sentiment.",
      "Signal quality improves when regime context stays attached to execution.",
    ],
  },
];

export const auditItems: AuditItem[] = [
  {
    title: "Methodology Exposed",
    detail: "Signal logic, performance formulas, and regime thresholds stay inspectable.",
  },
  {
    title: "Source Transparency",
    detail: "Data provenance remains visible so confidence and recency can be judged directly.",
  },
  {
    title: "Operator Control",
    detail: "The terminal helps the trader reason and execute. It does not ask for blind trust.",
  },
  {
    title: "Open Architecture",
    detail: "Workflows are auditable, extensible, and built for users who care how the system thinks.",
  },
  {
    title: "Structure Catalog",
    detail: "58 options structures catalogued with guard decisions, risk profiles, bias labels, and P&L attribution formulas. Reference used by order entry, audit scripts, and agent evaluation.",
  },
];

export const footerColumns = [
  {
    title: "Terminal",
    links: [
      { href: "#strategies", label: "Strategies" },
      { href: "#execution", label: "Execution" },
      { href: "#surfaces", label: "Surfaces" },
    ],
  },
  {
    title: "Protocol",
    links: [
      { href: "#methodology", label: "Methodology" },
      { href: "https://github.com/joemccann/radon", label: "Source" },
      { href: "https://github.com/joemccann/radon/blob/main/docs/strategies.md", label: "Registry" },
    ],
  },
];
