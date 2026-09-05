import { HOSTED_MCP_URL } from "./developer-pages";
import { DEMO_APP_URL, SITE_NAME, siteUrl } from "./seo";

export const AGENT_PROMPT_SECTION_HEADINGS = [
  "When to use",
  "Hard nos",
  "How to call",
  "Parameters / constraints",
  "Definition of done for the agent",
] as const;

export const SHARED_HARD_NOS = [
  "Not a broker; no Robinhood routing",
  "No undefined-risk / naked shorts as default path",
  "Live execution requires Interactive Brokers + operator rails",
] as const;

const MCP_NOT_PUBLIC = "not public yet; use markdown + demo";
const MCP_DOCS = `hosted Streamable HTTP at ${HOSTED_MCP_URL}: radon_docs (no token)`;
const MCP_CRI = `hosted Streamable HTTP at ${HOSTED_MCP_URL}: demo_regime (demo Clerk token) or radon_docs (no token)`;
const MCP_GEX = `hosted Streamable HTTP at ${HOSTED_MCP_URL}: demo_gex (demo Clerk token) or radon_docs (no token)`;

export type AgentPrompt = {
  capability: string;
  whenToUse: string;
  hardNos: string[];
  canonicalUrl: string;
  demoUrl: string;
  mcp: string;
  parameters: string[];
  definitionOfDone: string[];
};

export type DeveloperRecipe = {
  id: string;
  title: string;
  prompt: AgentPrompt;
};

export function formatAgentPrompt(prompt: AgentPrompt): string {
  return [
    `# ${SITE_NAME} - ${prompt.capability}`,
    "",
    "## When to use",
    prompt.whenToUse,
    "",
    "## Hard nos",
    ...[...SHARED_HARD_NOS, ...prompt.hardNos].map((item) => `- ${item}`),
    "",
    "## How to call",
    `1. Read ${siteUrl}/llms.txt`,
    `2. Fetch ${prompt.canonicalUrl} with Accept: text/markdown (or .md)`,
    `3. Demo: ${prompt.demoUrl}`,
    `4. MCP: ${prompt.mcp}`,
    "",
    "## Parameters / constraints",
    ...prompt.parameters.map((item) => `- ${item}`),
    "",
    "## Definition of done for the agent",
    ...prompt.definitionOfDone.map((item) => `- ${item}`),
    "",
  ].join("\n");
}

function writeClipboardFallback(text: string): boolean {
  if (typeof document === "undefined") return false;
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.left = "-9999px";
  document.body.appendChild(area);
  area.select();
  try {
    return document.execCommand("copy");
  } finally {
    document.body.removeChild(area);
  }
}

export async function writeClipboard(
  text: string,
): Promise<"copied" | "failed"> {
  try {
    const clipboard = globalThis.navigator?.clipboard;
    if (clipboard?.writeText) {
      await clipboard.writeText(text);
      return "copied";
    }
  } catch {
    // fall through to the textarea path
  }
  try {
    return writeClipboardFallback(text) ? "copied" : "failed";
  } catch {
    return "failed";
  }
}

const DONE_NO_ORDER =
  "The turn explains the read and stops. It does not place, route, or broker an order.";

export const capabilityPrompts: Record<string, AgentPrompt> = {
  flow: {
    capability: "Flow scorer",
    whenToUse:
      "Score Unusual Whales dark-pool or OTC prints for accumulation or distribution that has not moved the lit price.",
    hardNos: [
      "Do not invent Yahoo as the primary print source. Interactive Brokers first, Unusual Whales second.",
    ],
    canonicalUrl: `${siteUrl}/convex-options-from-dark-pool-flow`,
    demoUrl: `${DEMO_APP_URL}/flow-analysis`,
    mcp: MCP_NOT_PUBLIC,
    parameters: [
      "Off-exchange and OTC prints, sweeps, and options flow from Unusual Whales, reconciled against the Interactive Brokers realtime tape.",
      "Volume is venue-weighted and normalized to a rolling z-score; directional pressure comes from print-side and size clustering.",
      "A threshold-crossing flow score that precedes the lit move, with the lead window measured per ticker.",
    ],
    definitionOfDone: [
      "Reports accumulation, distribution, or below-threshold, plus the lead window when one exists.",
      DONE_NO_ORDER,
    ],
  },
  gates: {
    capability: "Gate stack",
    whenToUse:
      "Force a candidate through four sequential gates before a contract exists. A failure at any gate stops the trade and names the gate.",
    hardNos: [
      "Do not skip a failed gate or award partial credit for clearing three of four.",
      "Gate 04 is disabled by operator policy; do not treat it as active.",
    ],
    canonicalUrl: `${siteUrl}/convex-options-from-dark-pool-flow`,
    demoUrl: DEMO_APP_URL,
    mcp: MCP_NOT_PUBLIC,
    parameters: [
      "Gate 01 Convexity: defined-risk only, gain at least 2x loss, capped loss known at submit.",
      "Gate 02 Edge: a specific dark-pool or OTC signal that has not yet moved the lit price.",
      "Gate 03 Risk: fractional Kelly with a hard ceiling of 2.5% of bankroll per position.",
      "Gate 04 Naked shorts: historically blocked undefined-risk shorts; disabled 2026-04-30, logic preserved.",
    ],
    definitionOfDone: [
      "Names each gate pass or fail in order and stops on the first failure.",
      DONE_NO_ORDER,
    ],
  },
  "gate-01": {
    capability: "Gate 01 Convexity",
    whenToUse:
      "Check whether the payoff is asymmetric before any other gate runs. Defined-risk structures only, with capped loss known at submit.",
    hardNos: [
      "Do not admit undefined-risk or naked shorts as the default path.",
    ],
    canonicalUrl: `${siteUrl}/convex-options-from-dark-pool-flow`,
    demoUrl: `${DEMO_APP_URL}/options`,
    mcp: MCP_NOT_PUBLIC,
    parameters: [
      "Rule: gain ≥ 2 × loss.",
      "Defined-risk structures only. The capped loss is known before the order is submitted.",
    ],
    definitionOfDone: [
      "States max gain, max loss, and whether gain is at least 2x loss.",
      DONE_NO_ORDER,
    ],
  },
  "gate-02": {
    capability: "Gate 02 Edge",
    whenToUse:
      "Confirm a specific, data-backed dark-pool or OTC signal that has not yet moved the lit price.",
    hardNos: [
      "Seasonality, analyst ratings, and news are context, not this gate.",
    ],
    canonicalUrl: `${siteUrl}/convex-options-from-dark-pool-flow`,
    demoUrl: `${DEMO_APP_URL}/flow-analysis`,
    mcp: MCP_NOT_PUBLIC,
    parameters: [
      "Rule: signal precedes price.",
      "The print stream nominates candidates; a score that misses its threshold is discarded.",
    ],
    definitionOfDone: [
      "Names the print or flow evidence and whether it still leads the lit move.",
      DONE_NO_ORDER,
    ],
  },
  "gate-03": {
    capability: "Gate 03 Risk",
    whenToUse:
      "Size the position with fractional Kelly and refuse anything above the hard per-position ceiling.",
    hardNos: [
      "Do not override the 2.5% bankroll cap. The cap is a ceiling, not a target.",
    ],
    canonicalUrl: `${siteUrl}/fractional-kelly-position-sizing`,
    demoUrl: DEMO_APP_URL,
    mcp: MCP_NOT_PUBLIC,
    parameters: [
      "Rule: ≤ 2.5% bankroll.",
      "Quarter-Kelly from max gain over max loss and the signal odds, then min(that, 2.5%).",
    ],
    definitionOfDone: [
      "Reports the Kelly fraction, the cap, and the binding size.",
      DONE_NO_ORDER,
    ],
  },
  "gate-04": {
    capability: "Gate 04 Naked shorts",
    whenToUse:
      "Read the published no-naked-shorts rule. Disabled by operator policy on 2026-04-30. The blocking logic is preserved for re-enable.",
    hardNos: [
      "Do not re-enable the gate or treat undefined-risk shorts as the default path.",
    ],
    canonicalUrl: `${siteUrl}/convex-options-from-dark-pool-flow`,
    demoUrl: DEMO_APP_URL,
    mcp: MCP_NOT_PUBLIC,
    parameters: [
      "Rule: no naked shorts.",
      "Disabled by operator policy; logic preserved. Re-enable is a documented operator path, not an agent action.",
    ],
    definitionOfDone: [
      "States that Gate 04 is disabled and that undefined-risk shorts stay off the default path.",
      DONE_NO_ORDER,
    ],
  },
  cri: {
    capability: "Crash Risk Index",
    whenToUse:
      "Read CRI, the tail-risk regime that forces systematic selling, and pair it with GEX, VCG-R, or GRG when the job is corroboration.",
    hardNos: [
      "Do not use CRI as a SpotGamma-style levels dashboard or as a standalone trade.",
    ],
    canonicalUrl: `${siteUrl}/crash-risk-index`,
    demoUrl: `${DEMO_APP_URL}/regime/cri`,
    mcp: MCP_CRI,
    parameters: [
      "Four components, each scored 0 to 25: VIX level and rate of change, VVIX and its ratio to VIX, COR1M implied correlation, and SPX distance from its 100-day average.",
      "Published weights and thresholds. The scores sum to a 0 to 100 crash regime read.",
    ],
    definitionOfDone: [
      "Reports the 0 to 100 CRI read, the published band, and the four component scores when present.",
      DONE_NO_ORDER,
    ],
  },
  gex: {
    capability: "GEX walls and magnets",
    whenToUse:
      "Read dealer gamma (GEX) as walls and magnets, then pair it with CRI, VCG-R, or GRG. Walls set resistance; magnets set gravity.",
    hardNos: [
      "Do not treat GEX levels as a trade by themselves or as a SpotGamma-style dashboard substitute.",
    ],
    canonicalUrl: `${siteUrl}/`,
    demoUrl: `${DEMO_APP_URL}/regime/gex`,
    mcp: MCP_GEX,
    parameters: [
      "Aggregate dealer gamma by strike. Positive gamma pins and dampens; negative gamma amplifies.",
      "Surfaces walls (resistance) and magnets (gravity) as price levels for targets and structure.",
    ],
    definitionOfDone: [
      "Names walls and magnets for the requested ticker and the directional bias when present.",
      DONE_NO_ORDER,
    ],
  },
  structures: {
    capability: "Structure catalog",
    whenToUse:
      "Choose a defined-risk options structure with gain at least 2x loss. The catalog is a risk policy, not a menu.",
    hardNos: [
      "Do not recommend undefined-risk or naked shorts as the default path.",
    ],
    canonicalUrl: `${siteUrl}/defined-risk-options-structures`,
    demoUrl: `${DEMO_APP_URL}/options`,
    mcp: MCP_NOT_PUBLIC,
    parameters: [
      "Filter on defined-risk, guard ALLOW, and gain at least 2x loss.",
      "Gate 04 is disabled; catalog guard decisions still describe the preserved no-naked-shorts logic.",
    ],
    definitionOfDone: [
      "Lists matching catalog entries with risk verdict and guard decision.",
      DONE_NO_ORDER,
    ],
  },
  kelly: {
    capability: "Fractional Kelly sizer",
    whenToUse:
      "Size a defined-risk structure with fractional Kelly, hard-capped at 2.5% of bankroll per position.",
    hardNos: [
      "Do not override the 2.5% cap. The cap is a ceiling, not a target.",
    ],
    canonicalUrl: `${siteUrl}/fractional-kelly-position-sizing`,
    demoUrl: DEMO_APP_URL,
    mcp: MCP_NOT_PUBLIC,
    parameters: [
      "Signal odds b = max gain / max loss on the chosen defined-risk structure.",
      "Quarter-Kelly, then min(that fraction, 2.5% of bankroll). If Kelly is smaller, take the smaller number.",
    ],
    definitionOfDone: [
      "Reports b, quarter-Kelly, the 2.5% cap, and which number binds.",
      DONE_NO_ORDER,
    ],
  },
  "ib-terminal": {
    capability: "Interactive Brokers dark pool terminal",
    whenToUse:
      "Explain the seven-milestone path from a print to an Interactive Brokers combo order. IB supplies tape, account state, and execution; Unusual Whales supplies the prints.",
    hardNos: [
      "There is no public order-placement API and no anonymous FastAPI /docs.",
    ],
    canonicalUrl: `${siteUrl}/interactive-brokers-dark-pool-terminal`,
    demoUrl: DEMO_APP_URL,
    mcp: MCP_NOT_PUBLIC,
    parameters: [
      "Interactive Brokers first for realtime data; Unusual Whales second for dark-pool prints and sweeps.",
      "Live routing stays on the operator terminal behind Clerk.",
    ],
    definitionOfDone: [
      "Explains the IB jobs (tape, account, execution, journal) without inventing a public order API.",
      DONE_NO_ORDER,
    ],
  },
  "uw-ib": {
    capability: "Unusual Whales to Interactive Brokers",
    whenToUse:
      "Walk the disciplined pipeline from an Unusual Whales print to an Interactive Brokers defined-risk combo.",
    hardNos: [
      "An alert is a read, not a route. Do not treat Unusual Whales as a broker.",
    ],
    canonicalUrl: `${siteUrl}/unusual-whales-interactive-brokers`,
    demoUrl: DEMO_APP_URL,
    mcp: MCP_NOT_PUBLIC,
    parameters: [
      "Prints and options flow drive the flow score; open-interest changes are required confirmation; seasonality never gates a trade.",
      "Four sequential gates and fractional Kelly sit between the read and any IB combo.",
    ],
    definitionOfDone: [
      "Names ingest surfaces and the stages a print passes through, then stops before routing.",
      DONE_NO_ORDER,
    ],
  },
};

export function getCapabilityPrompt(id: string): AgentPrompt {
  const prompt = capabilityPrompts[id];
  if (!prompt) {
    throw new Error(`Unknown agent capability: ${id}`);
  }
  return prompt;
}

export const developerRecipes: DeveloperRecipe[] = [
  {
    id: "score-flow",
    title: "Score flow for TICKER",
    prompt: {
      ...capabilityPrompts.flow,
      whenToUse: `Score Unusual Whales dark-pool or OTC prints on TICKER for accumulation or distribution that has not moved the lit price.`,
      demoUrl: `${DEMO_APP_URL}/flow-analysis/TICKER`,
      parameters: [
        "Replace TICKER with the symbol under review.",
        ...capabilityPrompts.flow.parameters,
      ],
      definitionOfDone: [
        "Reports the TICKER flow score, accumulation or distribution or below-threshold, and the lead window when one exists.",
        DONE_NO_ORDER,
      ],
    },
  },
  {
    id: "evaluate-gates",
    title: "Evaluate gates for a structure",
    prompt: {
      ...capabilityPrompts.gates,
      whenToUse:
        "Evaluate STRUCTURE_ID through the four sequential gates. A failure at any gate stops the trade and names the gate.",
      parameters: [
        "Replace STRUCTURE_ID with a catalog name such as Long Call or Bull Call Spread.",
        ...capabilityPrompts.gates.parameters,
      ],
      definitionOfDone: [
        "Names STRUCTURE_ID and each gate pass or fail in order, and stops on the first failure.",
        DONE_NO_ORDER,
      ],
    },
  },
  {
    id: "read-cri",
    title: "Read CRI regime",
    prompt: capabilityPrompts.cri,
  },
  {
    id: "read-gex",
    title: "Read GEX walls and magnets for TICKER",
    prompt: {
      ...capabilityPrompts.gex,
      whenToUse:
        "Read dealer gamma (GEX) walls and magnets for TICKER, then pair the levels with CRI, VCG-R, or GRG.",
      demoUrl: `${DEMO_APP_URL}/regime/gex`,
      parameters: [
        "Replace TICKER with the symbol whose dealer-gamma surface you need.",
        ...capabilityPrompts.gex.parameters,
      ],
      definitionOfDone: [
        "Names walls and magnets for TICKER and the directional bias when present.",
        DONE_NO_ORDER,
      ],
    },
  },
  {
    id: "list-structures",
    title: "List convex structures",
    prompt: {
      ...capabilityPrompts.structures,
      whenToUse:
        "List catalog entries that clear the defined-risk convexity gate (gain at least 2x loss, guard ALLOW).",
      parameters: [
        "Optional filter: family id (vertical, butterfly, condor) or STRUCTURE_ID.",
        ...capabilityPrompts.structures.parameters,
      ],
    },
  },
  {
    id: "size-kelly",
    title: "Size with fractional Kelly",
    prompt: {
      ...capabilityPrompts.kelly,
      whenToUse:
        "Size a defined-risk structure from MAX_GAIN and MAX_LOSS with fractional Kelly, hard-capped at 2.5% of bankroll.",
      parameters: [
        "Replace MAX_GAIN and MAX_LOSS with the structure's dollar extremes. Signal odds b = MAX_GAIN / MAX_LOSS.",
        "Quarter-Kelly, then min(that fraction, 2.5% of bankroll). If Kelly is smaller, take the smaller number.",
      ],
      definitionOfDone: [
        "Reports b from MAX_GAIN / MAX_LOSS, quarter-Kelly, the 2.5% cap, and which number binds.",
        DONE_NO_ORDER,
      ],
    },
  },
  {
    id: "bootstrap",
    title: "What is Radon / when to use",
    prompt: {
      capability: "When to use",
      whenToUse:
        "Reach for Radon Terminal when the job is scoring Unusual Whales dark-pool or OTC prints; reading GEX walls and magnets; reading CRI, VCG-R, or GRG regimes; choosing a defined-risk options structure with gain at least 2x loss; or sizing with fractional Kelly hard-capped at 2.5% of bankroll.",
      hardNos: [
        "Do not treat Radon as a broker, a Robinhood integration, a public order API, or an order-routing MCP.",
        "Do not invent Yahoo as the primary data source. Interactive Brokers first, Unusual Whales second, Yahoo last.",
      ],
      canonicalUrl: `${siteUrl}/agent-instructions`,
      demoUrl: DEMO_APP_URL,
      mcp: MCP_DOCS,
      parameters: [
        "Start at llms.txt, then fetch the matching URL with Accept: text/markdown or the .md suffix.",
        "The working product surface without brokerage credentials is the free demo.",
      ],
      definitionOfDone: [
        "Explains when Radon is the right tool, how to call it, and the hard nos.",
        DONE_NO_ORDER,
      ],
    },
  },
];
