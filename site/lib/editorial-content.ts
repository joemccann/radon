// Verbatim editorial copy for the Radon market-structure landing.
// Single source of truth so sections stay in lockstep with the design.

export type EditorialNavLink = {
  label: string;
  href: string;
};

export const editorialNavLinks: EditorialNavLink[] = [
  { label: "Flow", href: "#flow" },
  { label: "Convexity", href: "#convexity" },
  { label: "Regime", href: "#regime" },
  { label: "Method", href: "#pipeline" },
  { label: "Registry", href: "#registry" },
  { label: "Evidence", href: "#evidence" },
  { label: "FAQ", href: "#faq" },
];

// The one product destination on the page: the free demo instance
// (public signups live there; app.radon.run is operator-allowlisted).
export const DEMO_URL = "https://demo.radon.run";

// Data-source reference links (from README.md; UW carries the referral).
export const IB_URL = "https://www.interactivebrokers.com/";
export const UW_URL =
  "https://unusualwhales.com/referral#39985a64-656c-4642-a051-db89f6324d64";

export type ScannerRow = {
  ticker: string;
  read: string;
  tone: "accum" | "dist" | "neutral";
  lead: string;
  score: number;
};

export const scannerRows: ScannerRow[] = [
  { ticker: "NVDA", read: "Accumulation", tone: "accum", lead: "4d", score: 84 },
  { ticker: "WULF", read: "Accumulation", tone: "accum", lead: "6d", score: 79 },
  { ticker: "SMCI", read: "Accumulation", tone: "accum", lead: "3d", score: 71 },
  { ticker: "SPY", read: "Neutral", tone: "neutral", lead: "·", score: 48 },
  { ticker: "TSLA", read: "Distribution", tone: "dist", lead: "5d", score: 33 },
];

export type ArgumentStep = {
  stage: string;
  body: string;
};

export const flowArgumentSteps: ArgumentStep[] = [
  {
    stage: "Source",
    body:
      "Off-exchange and OTC prints, sweeps, and options flow from Unusual Whales, reconciled against Interactive Brokers realtime tape.",
  },
  {
    stage: "Mechanism",
    body:
      "Volume is venue-weighted and normalized to a rolling z-score, then directional pressure is inferred from print-side and size clustering.",
  },
  {
    stage: "Evidence",
    body:
      "A threshold-crossing flow score that precedes the lit move, with the lead window measured per ticker and surfaced in the scanner.",
  },
];

export type Gate = {
  no: string;
  name: string;
  body: string;
  rule: string;
  disabled?: boolean;
};

export const gates: Gate[] = [
  {
    no: "Gate 01",
    name: "Convexity",
    body:
      "The payoff must be asymmetric. Defined-risk structures only, with capped loss known at submit.",
    rule: "gain ≥ 2 × loss",
  },
  {
    no: "Gate 02",
    name: "Edge",
    body:
      "A specific, data-backed dark-pool or OTC signal that has not yet moved the lit price.",
    rule: "signal precedes price",
  },
  {
    no: "Gate 03",
    name: "Risk",
    body:
      "Position size is fractional Kelly with a hard ceiling. No single position exceeds the cap.",
    rule: "≤ 2.5% bankroll",
  },
  {
    no: "Gate 04",
    name: "Naked Shorts",
    body:
      "Historically blocked undefined-risk shorts. Disabled by operator policy; logic preserved for re-enable.",
    rule: "no naked shorts",
    disabled: true,
  },
];

export type RegimeModel = {
  code: string;
  reads: string;
  name: string;
  method: string;
};

export const regimeModels: RegimeModel[] = [
  {
    code: "CRI",
    reads: "reads · tail risk",
    name: "Crash Risk Index",
    method:
      "Composite of skew steepness, term-structure inversion, and realized-implied gap. Calibrated to historical drawdown onsets. Rises before the tape acknowledges stress.",
  },
  {
    code: "GEX",
    reads: "reads · dealer positioning",
    name: "Gamma Exposure",
    method:
      "Aggregate dealer gamma by strike. Positive gamma pins and dampens; negative gamma amplifies. Surfaces walls (resistance) and magnets (gravity) as price levels.",
  },
  {
    code: "VCG-R",
    reads: "reads · panic / capitulation",
    name: "Volatility-Credit Gap",
    method:
      "Tracks the spread between equity-implied vol and credit-implied stress. A widening gap with credit leading flags panic that the options market has not yet priced.",
  },
  {
    code: "GRG",
    reads: "reads · rotation",
    name: "Gamma Rotation Gap",
    method:
      "Measures the migration of dealer gamma across sectors and tenors. A rotating gap signals where positioning is moving next, ahead of the flow following it.",
  },
];

export type Milestone = {
  name: string;
  body: string;
  tag: string;
};

export const milestones: Milestone[] = [
  {
    name: "Detect",
    body:
      "Flow score crosses the accumulation or distribution threshold with a positive lead window. The signal is logged with its source and confidence.",
    tag: "gate · edge",
  },
  {
    name: "Corroborate",
    body:
      "Cross-check the read against the four regime models. A signal fighting the regime is downgraded, not ignored. Agreement raises conviction.",
    tag: "regime · CRI / GEX / VCG-R / GRG",
  },
  {
    name: "Locate",
    body:
      "Map dealer gamma to find walls and magnets. These define realistic targets and the strikes where structure resists or accelerates price.",
    tag: "levels · walls / magnets",
  },
  {
    name: "Structure",
    body:
      "Select a defined-risk options structure that expresses the thesis with convexity. Multi-leg combos are preferred over single-leg directional bets.",
    tag: "convexity · gain ≥ 2× loss",
  },
  {
    name: "Size",
    body:
      "Fractional Kelly sets the position against bankroll, capped hard at the per-position ceiling. Conviction adjusts within the cap, never past it.",
    tag: "risk · ≤ 2.5% bankroll",
  },
  {
    name: "Verify",
    body:
      "The mandatory pre-trade chokepoint computes max-loss, margin impact, and naked-short exposure on the assembled combo. It is not optional and cannot be bypassed.",
    tag: "chokepoint · pre-trade risk",
  },
  {
    name: "Route",
    body:
      "Only a structure that cleared every prior milestone is assembled into an Interactive Brokers BAG combo and submitted. The audit trail records the full chain.",
    tag: "execute · IB BAG combo",
  },
];

export type RegistryEntry = {
  name: string;
  tag: string;
  edge: string;
  mechanism: string;
  structure: string;
  convexity: string;
  risk: string;
};

export const registryEntries: RegistryEntry[] = [
  {
    name: "Dark Lead",
    tag: "flow",
    edge: "Off-exchange accumulation",
    mechanism:
      "Build a long call structure into a confirmed dark-pool lead before the lit move.",
    structure: "Call debit spread",
    convexity: "≥ 3.0×",
    risk: "Defined",
  },
  {
    name: "Gamma Pin",
    tag: "positioning",
    edge: "Dealer gamma walls",
    mechanism:
      "Sell convexity into a positive-gamma wall where dealers dampen movement toward expiry.",
    structure: "Iron condor",
    convexity: "≥ 2.2×",
    risk: "Defined",
  },
  {
    name: "Panic Gap",
    tag: "vol / credit",
    edge: "Widening VCG-R",
    mechanism:
      "Buy tail convexity when credit-implied stress leads equity vol and the gap is widening.",
    structure: "Put backspread",
    convexity: "≥ 4.0×",
    risk: "Defined",
  },
  {
    name: "Rotation Front",
    tag: "flow",
    edge: "GRG sector migration",
    mechanism:
      "Position ahead of gamma rotating into a sector before the following flow arrives.",
    structure: "Call calendar",
    convexity: "≥ 2.5×",
    risk: "Defined",
  },
  {
    name: "Crash Hedge",
    tag: "tail",
    edge: "Elevated CRI",
    mechanism:
      "Layer cheap tail protection when the Crash Risk Index enters the elevated band.",
    structure: "Put debit spread",
    convexity: "≥ 5.0×",
    risk: "Defined",
  },
  {
    name: "LEAP Mispricing",
    tag: "vol surface",
    edge: "IV term mispricing",
    mechanism:
      "Buy long-dated convexity where the surface underprices realized term vol versus the model.",
    structure: "Long LEAP call",
    convexity: "≥ 2.0×",
    risk: "Defined",
  },
];

export type AuditEntry = {
  date: string;
  ticker: string;
  detail: string;
  result: string;
  positive: boolean;
};

export const auditEntries: AuditEntry[] = [
  { date: "06-12", ticker: "NVDA", detail: "call debit spread · Dark Lead", result: "+2.7R", positive: true },
  { date: "06-09", ticker: "SPX", detail: "iron condor · Gamma Pin", result: "+1.9R", positive: true },
  { date: "06-05", ticker: "WULF", detail: "call calendar · Rotation Front", result: "−1.0R", positive: false },
  { date: "05-30", ticker: "SPY", detail: "put debit spread · Crash Hedge", result: "+4.1R", positive: true },
  { date: "05-22", ticker: "SMCI", detail: "call debit spread · Dark Lead", result: "+3.3R", positive: true },
];
