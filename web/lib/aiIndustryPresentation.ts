import type { AiIndicator } from "./aiInfrastructure";

export const AI_INDUSTRY_STAGES = [
  {
    "id": "adoption",
    "name": "Adoption & spending",
    "short": "Adoption",
    "question": "Are people using AI and paying for it?",
    "subtitle": "Separate activity from willingness to pay.",
    "ids": [
      "D1",
      "D2",
      "D3",
      "D4",
      "D5"
    ],
    "main": "D5",
    "limit": "These are samples of individual platforms, not the whole AI market.",
    "watch": "Look for spending to broaden beyond the largest adopters."
  },
  {
    "id": "compute",
    "name": "Compute economics",
    "short": "Compute",
    "question": "Is AI becoming cheaper to run?",
    "subtitle": "Track the price of computing and useful work.",
    "ids": [
      "C1",
      "C2",
      "C3",
      "C4",
      "C5"
    ],
    "main": "C1",
    "limit": "Asking prices and listed supply do not establish realized revenue or utilization.",
    "watch": "Compare the same hardware and terms before inferring price pressure."
  },
  {
    "id": "buildout",
    "name": "Buildout & delivery",
    "short": "Buildout",
    "question": "Is investment turning into usable capacity?",
    "subtitle": "Follow chips, servers and power from order to delivery.",
    "ids": [
      "H1",
      "H2",
      "P1",
      "P2",
      "S1"
    ],
    "main": "H1",
    "limit": "Orders, backlog and energized capacity represent different stages.",
    "watch": "Reconcile delivery growth with inventories and customer collections."
  },
  {
    "id": "funding",
    "name": "Funding & returns",
    "short": "Funding",
    "question": "Can cash generation sustain the buildout?",
    "subtitle": "Compare investment with the cash that supports it.",
    "ids": [
      "F1",
      "F2"
    ],
    "main": "F1",
    "limit": "Company-wide cash flow and capital spending are not AI-only economics.",
    "watch": "Read cash investment alongside leases and other commitments."
  },
  {
    "id": "capability",
    "name": "Model capability",
    "short": "Capability",
    "question": "Are models improving at useful tasks?",
    "subtitle": "A separate lens on quality, cost and task completion.",
    "ids": [
      "D6"
    ],
    "main": "D6",
    "limit": "A design-task benchmark does not measure industry adoption or general model intelligence.",
    "watch": "Compare quality, cost and completion time within the same task and evaluation version."
  }
] as const;

export const AI_MEASURE_COPY: Record<string, { name: string; definition: string; relevance: string; limit: string }> = {
  "D1": {
    "name": "AI usage",
    "definition": "The amount of text processed through OpenRouter. Tokens are pieces of text, so more tokens can mean more requests, longer conversations or both.",
    "relevance": "Helps you judge whether measured AI use is expanding before comparing it with paid adoption.",
    "limit": "This covers one platform’s public traffic. It does not measure unique customers or revenue, and excludes private and zero-data-retention traffic."
  },
  "D2": {
    "name": "Application activity",
    "definition": "Requests sent to AI applications, plus the amount of text processed per request.",
    "relevance": "Separates growth in the number of requests from growth in the work each request performs.",
    "limit": "This uses the same OpenRouter data as AI usage. The two measures are not independent confirmation."
  },
  "D3": {
    "name": "Gateway mix",
    "definition": "How a gateway’s activity is divided among providers. Token share, request share and spending share each answer a different question.",
    "relevance": "Shows where developers are directing activity and how competition is changing within that gateway.",
    "limit": "A provider can gain share while the overall market shrinks. Shares do not reveal total demand, revenue or the price paid per token."
  },
  "D4": {
    "name": "Independent host activity",
    "definition": "Activity reported by another AI host. A verified independent source could help check the OpenRouter picture.",
    "relevance": "Would help distinguish a broad change in AI use from a change confined to one platform.",
    "limit": "Independent collection and estimation methodology must be verified. Estimated spending does not count as confirmation."
  },
  "D5": {
    "name": "Business AI spending",
    "definition": "Typical monthly AI spending per employee among businesses measured by Ramp. This captures payments for AI tools rather than free use.",
    "relevance": "Helps you judge whether business adoption is turning into paid demand.",
    "limit": "Ramp customers are a business sample, not the whole market. Keep the median separate from the highest-spending 10% and 1%."
  },
  "C1": {
    "name": "GPU rental prices",
    "definition": "Advertised rental prices for comparable GPUs, the processors used to train and run AI models. Hardware, region and rental terms must match.",
    "relevance": "Lower prices can reduce costs for AI applications while pressuring the companies that rent out computing capacity.",
    "limit": "An asking price is not a completed rental. Better technology can also lower prices, even when demand grows."
  },
  "C2": {
    "name": "Rentable GPU supply",
    "definition": "GPUs currently listed as rentable on a marketplace, using the same search filters over time.",
    "relevance": "Adds supply context to rental prices. Read the two together before drawing conclusions about capacity.",
    "limit": "A listing can disappear for reasons other than a rental. Listings do not tell you how much of the installed GPU fleet is in use."
  },
  "C3": {
    "name": "Model inference prices",
    "definition": "Quoted prices for running the same group of AI models. Inference means using a trained model to produce an answer or complete a task.",
    "relevance": "Helps you assess changes in the cost of running AI applications and pressure on model-provider pricing.",
    "limit": "The model group and mix of input and output tokens must stay fixed. Missing members make the basket incomplete."
  },
  "C4": {
    "name": "Cost of useful work",
    "definition": "The cost of completing a defined task successfully, while meeting the same quality and speed requirements.",
    "relevance": "Tests whether cheaper AI translates into cheaper useful work. A low token price alone does not answer that question.",
    "limit": "A licensed benchmark and comparable accuracy, speed and context constraints are required before interpreting this measure."
  },
  "H1": {
    "name": "Hardware delivery",
    "definition": "Hardware revenue recognized by an issuer. Orders, backlog and guidance describe different stages and should be read separately.",
    "relevance": "Helps you check whether planned investment is reaching suppliers’ reported results.",
    "limit": "Revenue is not profit or cash received. NVIDIA data-center revenue must stay separate from total-company guidance."
  },
  "H2": {
    "name": "Inventory & collections",
    "definition": "How long inventory sits and how quickly customers pay, using average balances and the actual length of the fiscal quarter.",
    "relevance": "Rising inventory or slower collections can challenge an otherwise strong shipment story.",
    "limit": "Product launches and seasonality also affect working capital. Compare the same issuer and accounting definitions."
  },
  "P1": {
    "name": "Power demand context",
    "definition": "Electricity demand in the measured Dominion region. Weather observations help explain changes in that demand.",
    "relevance": "Provides physical context for power and infrastructure research, alongside company disclosures.",
    "limit": "Regional electricity use is not AI electricity use. Weather is an input to the analysis, not a second AI-demand signal."
  },
  "P2": {
    "name": "Power coming online",
    "definition": "How power projects progress from requested capacity to contracts, construction and an energized connection.",
    "relevance": "Helps you separate planned equipment demand from capacity that can actually operate.",
    "limit": "Project announcements and backlog are not energized megawatts. Overlapping project phases must be counted only once."
  },
  "F1": {
    "name": "Cash investment coverage",
    "definition": "Operating cash generated over the past year, divided by cash investment in property and equipment. At 1×, the two amounts are equal.",
    "relevance": "Helps you assess whether operations can fund a company’s cash buildout.",
    "limit": "Below 1× does not mean insolvency; above 1× does not prove AI profitability. Leases, debt and other cash needs still matter."
  },
  "F2": {
    "name": "Future revenue & obligations",
    "definition": "Future contracted revenue, leases and other obligations, with their stated timing and definitions.",
    "relevance": "Adds context to the gap between today’s investment commitments and the cash those investments may eventually generate.",
    "limit": "Contracted future revenue is not revenue today. Definitions and conversion timing differ by company."
  },
  "M1": {
    "name": "Market pricing context",
    "definition": "Ticker prices, option volatility, institutional flow and next-day open interest from Radon’s existing research tools.",
    "relevance": "Connects an industry thesis with what the market already prices and the risks in an actual portfolio.",
    "limit": "A thematic ticker list is not measured portfolio exposure. These industry measures do not establish a trade edge or a Kelly probability."
  },
  "C5": {
    "name": "Independent GPU index",
    "definition": "A separate published index of US GPU prices, expressed in dollars per GPU per hour.",
    "relevance": "Provides another price reference to investigate alongside rental-market evidence.",
    "limit": "Its methodology is opaque until licensed. A single date cannot establish a trend. Do not use this index to extend another index’s history."
  },
  "D6": {
    "name": "Design-task model quality",
    "definition": "Model quality, cost and completion time on defined design tasks, such as dashboards and mobile interfaces.",
    "relevance": "Helps you investigate whether models are becoming more useful for a particular kind of work.",
    "limit": "A design-task score does not measure general intelligence, paid adoption or GPU scarcity. Task and evaluation versions must match."
  },
  "S1": {
    "name": "Structural compute context",
    "definition": "Largest published frontier-model training compute (FLOP) and the largest listed data-center current H100-equivalent count, with that site's current power in MW. Source: Epoch AI (CC BY 4.0).",
    "relevance": "Research context for how large published training runs and listed data-center capacity are. This is structural evidence, not a cycle timing input.",
    "limit": "Epoch figures are published estimates, not meter readings. FLOP, H100e and MW stay in their own units. Chip-stock totals are omitted. Source: Epoch AI."
  }
};

export function industryStage(indicator: AiIndicator): string {
  const stage = AI_INDUSTRY_STAGES.find(item => (item.ids as readonly string[]).includes(indicator.id));
  if (stage) return stage.id;
  if (indicator.id === "M1") return "market";
  return ({ demand: "adoption", compute: "compute", delivery: "buildout", finance: "funding" })[indicator.pane] ?? "additional";
}

export function measureCopy(indicator: AiIndicator) {
  return AI_MEASURE_COPY[indicator.id] ?? { name: indicator.title, definition: indicator.methodology, relevance: "Additional source evidence. Review its coverage and methodology before connecting it to an investment thesis.", limit: indicator.reason };
}
