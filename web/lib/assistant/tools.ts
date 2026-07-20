/**
 * F7 — Assistant tool registry.
 *
 * Declares the tools the agentic loop (`app/api/assistant/route.ts`) exposes to
 * the model. READ tools are thin `radonFetch` wrappers that pull live state from
 * FastAPI; the single MUTATE tool (`place_order`) is flagged `destructive` and is
 * NEVER auto-executed by the loop. Instead the route turns a destructive
 * tool_use block into a structured confirm proposal the ChatPanel renders,
 * mirroring the OrderRiskGate confirm discipline.
 */

import { radonFetch } from "@/lib/radonApi";
import type { LlmTool } from "@/lib/llm/provider";

export type AssistantTool = LlmTool & {
  destructive: boolean;
  /** Executes the tool against FastAPI. Omitted for destructive tools. */
  run?: (input: Record<string, unknown>, token?: string) => Promise<unknown>;
  /** Renders a one-line confirm summary for destructive proposals. */
  summarize?: (input: Record<string, unknown>) => string;
};

export type ToolResult = {
  ok: boolean;
  data?: unknown;
  error?: string;
};

const READ_TIMEOUT_MS = 130_000;
const KNOWLEDGE_TIMEOUT_MS = 30_000;
const KNOWLEDGE_RESULT_LIMIT = 6;
const KNOWLEDGE_CONTENT_CHARS = 1200;
const KNOWLEDGE_SUMMARY_CHARS = 300;
const KNOWLEDGE_TITLE_CHARS = 200;
const KNOWLEDGE_DOC_KEY_CHARS = 160;

function tickerOf(input: Record<string, unknown>): string {
  const raw = typeof input.ticker === "string" ? input.ticker.trim().toUpperCase() : "";
  return raw;
}

function stringListOf(input: Record<string, unknown>, key: string): string[] {
  const raw = input[key];
  if (!Array.isArray(raw)) return [];
  return raw.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
}

function truncateText(text: string, maxChars: number): string {
  return text.length > maxChars ? `${text.slice(0, maxChars)}...` : text;
}

type KnowledgeRow = {
  source?: string;
  scope?: string;
  doc_key?: string;
  chunk_ix?: number;
  title?: string | null;
  summary?: string | null;
  content?: string;
  score?: number;
  last_activity_at?: string;
};

/**
 * Renders one retrieval row as a bounded text block: citation header
 * (source/scope, doc_key#chunk, title, score, recency) + truncated summary +
 * truncated content. Never a raw JSON dump — the loop stringifies tool
 * results verbatim, so boundedness has to be enforced here.
 */
function formatKnowledgeRow(row: KnowledgeRow): string {
  const source = row.source ?? "unknown";
  const scope = row.scope ?? "unknown";
  const docKey = truncateText(row.doc_key ?? "unknown", KNOWLEDGE_DOC_KEY_CHARS);
  const chunk = typeof row.chunk_ix === "number" ? `#${row.chunk_ix}` : "";
  const title =
    typeof row.title === "string" && row.title.trim()
      ? ` | ${truncateText(row.title.trim(), KNOWLEDGE_TITLE_CHARS)}`
      : "";
  const score = typeof row.score === "number" ? row.score.toFixed(3) : "?";
  const activity = row.last_activity_at ? `, ${row.last_activity_at}` : "";

  const lines = [`[${source}/${scope}] ${docKey}${chunk}${title} (score ${score}${activity})`];
  if (typeof row.summary === "string" && row.summary.trim()) {
    lines.push(truncateText(row.summary.trim(), KNOWLEDGE_SUMMARY_CHARS));
  }
  if (typeof row.content === "string" && row.content.trim()) {
    lines.push(truncateText(row.content.trim(), KNOWLEDGE_CONTENT_CHARS));
  }
  return lines.join("\n");
}

function formatKnowledgePayload(data: unknown): Record<string, unknown> {
  const payload = (data ?? {}) as {
    retrieval?: string;
    ticker?: string;
    results?: KnowledgeRow[];
  };
  const rows = Array.isArray(payload.results) ? payload.results : [];
  return {
    ...(typeof payload.ticker === "string" ? { ticker: payload.ticker } : {}),
    retrieval: payload.retrieval ?? "unknown",
    count: rows.length,
    results: rows.map(formatKnowledgeRow),
  };
}

export const ASSISTANT_TOOLS: AssistantTool[] = [
  {
    name: "get_flow",
    description:
      "Fetch institutional dark-pool / OTC flow analysis for a ticker. Returns net premium, sweep activity, and directional bias.",
    destructive: false,
    input_schema: {
      type: "object",
      properties: {
        ticker: { type: "string", description: "Underlying symbol, e.g. SPY" },
      },
      required: ["ticker"],
    },
    run: (input, token) =>
      radonFetch(`/flow-analysis/${encodeURIComponent(tickerOf(input))}`, {
        method: "POST",
        timeout: READ_TIMEOUT_MS,
        token,
      }),
  },
  {
    name: "run_scan",
    description: "Run the market-wide flow scan and return the ranked convex opportunities.",
    destructive: false,
    input_schema: {
      type: "object",
      properties: {},
    },
    run: (_input, token) =>
      radonFetch("/scan", { method: "POST", timeout: READ_TIMEOUT_MS, token }),
  },
  {
    name: "get_gex",
    description: "Fetch the current Gamma Exposure (GEX) levels: flip point, walls, magnets, and dealer bias.",
    destructive: false,
    input_schema: {
      type: "object",
      properties: {},
    },
    run: (_input, token) =>
      radonFetch("/gex/scan", { method: "POST", timeout: READ_TIMEOUT_MS, token }),
  },
  {
    name: "get_portfolio",
    description: "Fetch current open positions, bankroll, exposure, and account summary.",
    destructive: false,
    input_schema: {
      type: "object",
      properties: {},
    },
    run: (_input, token) =>
      radonFetch("/portfolio/sync", { method: "POST", timeout: 35_000, token }),
  },
  {
    name: "search_knowledge",
    description:
      "Search Radon's knowledge base (journal, evals, docs, newsfeed, incidents) for prior theses, evaluations, incidents, and lessons. Returns compact scored excerpts with doc_keys for citation.",
    destructive: false,
    input_schema: {
      type: "object",
      properties: {
        query: { type: "string", description: "Search query, e.g. 'NVDA gamma squeeze thesis'" },
        scopes: {
          type: "array",
          items: { type: "string", enum: ["trading", "research", "ops"] },
          description: "Optional scope filter",
        },
        sources: {
          type: "array",
          items: { type: "string", enum: ["journal", "evals", "docs", "newsfeed", "incidents"] },
          description: "Optional source filter",
        },
      },
      required: ["query"],
    },
    run: async (input, token) => {
      const body: Record<string, unknown> = {
        query: typeof input.query === "string" ? input.query.trim() : "",
        compact: true,
        limit: KNOWLEDGE_RESULT_LIMIT,
      };
      const scopes = stringListOf(input, "scopes");
      if (scopes.length) body.scopes = scopes;
      const sources = stringListOf(input, "sources");
      if (sources.length) body.sources = sources;
      const data = await radonFetch("/knowledge/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        timeout: KNOWLEDGE_TIMEOUT_MS,
        token,
      });
      return formatKnowledgePayload(data);
    },
  },
  {
    name: "find_prior_evals",
    description:
      "Find prior Radon evaluations and journal history for a ticker. Use before forming a new thesis so past evals and outcomes inform the current one.",
    destructive: false,
    input_schema: {
      type: "object",
      properties: {
        ticker: { type: "string", description: "Underlying symbol, e.g. NVDA" },
      },
      required: ["ticker"],
    },
    run: async (input, token) => {
      const ticker = tickerOf(input);
      const data = await radonFetch(
        `/knowledge/prior-evals?ticker=${encodeURIComponent(ticker)}&limit=${KNOWLEDGE_RESULT_LIMIT}&compact=true`,
        { timeout: KNOWLEDGE_TIMEOUT_MS, token },
      );
      return formatKnowledgePayload(data);
    },
  },
  {
    name: "place_order",
    description:
      "Propose an options or stock order. This is a destructive action: it is NOT executed automatically. The user must confirm the proposal before any order is sent.",
    destructive: true,
    input_schema: {
      type: "object",
      properties: {
        ticker: { type: "string", description: "Underlying symbol" },
        action: { type: "string", enum: ["BUY", "SELL"], description: "Order direction" },
        quantity: { type: "number", description: "Number of contracts or shares" },
        limit_price: { type: "number", description: "Limit price per contract/share" },
        structure: { type: "string", description: "Optional structure label, e.g. long call, bull call spread" },
      },
      required: ["ticker", "action", "quantity"],
    },
    summarize: (input) => {
      const action = typeof input.action === "string" ? input.action.toUpperCase() : "ORDER";
      const quantity = typeof input.quantity === "number" ? input.quantity : "?";
      const ticker = tickerOf(input) || "?";
      const structure = typeof input.structure === "string" && input.structure.trim() ? ` ${input.structure.trim()}` : "";
      const limit = typeof input.limit_price === "number" ? ` @ ${input.limit_price}` : "";
      return `${action} ${quantity} ${ticker}${structure}${limit}`;
    },
  },
];

const TOOL_BY_NAME = new Map<string, AssistantTool>(ASSISTANT_TOOLS.map((tool) => [tool.name, tool]));

export function getTool(name: string): AssistantTool | undefined {
  return TOOL_BY_NAME.get(name);
}

export function isDestructiveTool(name: string): boolean {
  return Boolean(TOOL_BY_NAME.get(name)?.destructive);
}

/** The tool schema the model sees: name + description + input_schema only. */
export function toolSchemas(): LlmTool[] {
  return ASSISTANT_TOOLS.map(({ name, description, input_schema }) => ({ name, description, input_schema }));
}

/**
 * Executes a READ tool. Destructive tools throw — they must be routed through a
 * confirm proposal, never executed inside the loop.
 */
export async function executeTool(
  name: string,
  input: Record<string, unknown>,
  token?: string,
): Promise<ToolResult> {
  const tool = TOOL_BY_NAME.get(name);
  if (!tool) {
    return { ok: false, error: `Unknown tool: ${name}` };
  }
  if (tool.destructive || !tool.run) {
    return { ok: false, error: `Tool ${name} is destructive and requires user confirmation.` };
  }
  try {
    const data = await tool.run(input, token);
    return { ok: true, data };
  } catch (error) {
    const message = error instanceof Error ? error.message : "Tool execution failed.";
    return { ok: false, error: message };
  }
}

export function summarizeProposal(name: string, input: Record<string, unknown>): string {
  const tool = TOOL_BY_NAME.get(name);
  if (tool?.summarize) return tool.summarize(input);
  return `${name} ${JSON.stringify(input)}`;
}
