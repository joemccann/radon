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

function tickerOf(input: Record<string, unknown>): string {
  const raw = typeof input.ticker === "string" ? input.ticker.trim().toUpperCase() : "";
  return raw;
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
