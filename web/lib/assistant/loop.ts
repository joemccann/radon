/**
 * F7 — Agentic tool-calling loop.
 *
 * Drives multiple rounds over F2's provider-agnostic `chat()`:
 *   1. Call the model with the tool schemas.
 *   2. If it returns tool_use blocks:
 *        - a destructive tool (place_order) halts the loop and yields a confirm
 *          proposal — NEVER executed here (mirrors OrderRiskGate discipline).
 *        - READ tools execute via `executeTool` (radonFetch) and their results
 *          feed back as a tool_result turn.
 *   3. Repeat until the model stops requesting tools or the round cap is hit.
 *
 * The conversation carries structured content blocks (text / tool_use /
 * tool_result) so the model sees its own calls and their outputs. `chat()` maps
 * `message.content` straight onto the Anthropic Messages body, so passing block
 * arrays is the native shape; string turns from the UI stay strings.
 */

import { chat, type LlmMessage, type LlmToolCall } from "@/lib/llm/provider";
import {
  executeTool,
  isDestructiveTool,
  summarizeProposal,
  toolSchemas,
} from "@/lib/assistant/tools";

const MAX_ROUNDS = 6;

export type AssistantTurn = {
  role: "user" | "assistant";
  content: string;
};

export type ToolEvent = {
  name: string;
  input: Record<string, unknown>;
  ok: boolean;
  error?: string;
};

export type OrderProposal = {
  tool: string;
  destructive: true;
  input: Record<string, unknown>;
  summary: string;
  toolUseId: string;
};

export type AssistantLoopResult = {
  content: string;
  model: string;
  toolEvents: ToolEvent[];
  proposal?: OrderProposal;
  rounds: number;
};

type ToolUseBlock = {
  type: "tool_use";
  id: string;
  name: string;
  input: Record<string, unknown>;
};

type ToolResultBlock = {
  type: "tool_result";
  tool_use_id: string;
  content: string;
};

// `chat()` consumes `LlmMessage[]`; we widen content to the structured block
// arrays Anthropic accepts. The provider passes content through verbatim.
type LoopMessage = Omit<LlmMessage, "content"> & {
  content: string | Array<ToolUseBlock | ToolResultBlock | { type: "text"; text: string }>;
};

function toAssistantToolUseBlocks(text: string, toolCalls: LlmToolCall[]): LoopMessage {
  const blocks: Array<ToolUseBlock | { type: "text"; text: string }> = [];
  if (text.trim()) blocks.push({ type: "text", text });
  for (const call of toolCalls) {
    blocks.push({ type: "tool_use", id: call.id, name: call.name, input: call.input });
  }
  return { role: "assistant", content: blocks };
}

function toToolResultMessage(results: ToolResultBlock[]): LoopMessage {
  return { role: "user", content: results };
}

function proposalFor(call: LlmToolCall): OrderProposal {
  return {
    tool: call.name,
    destructive: true,
    input: call.input,
    summary: summarizeProposal(call.name, call.input),
    toolUseId: call.id,
  };
}

function stringifyToolResult(result: { ok: boolean; data?: unknown; error?: string }): string {
  if (!result.ok) return JSON.stringify({ error: result.error ?? "Tool failed." });
  return JSON.stringify(result.data ?? { ok: true });
}

export async function runAssistantLoop(
  turns: AssistantTurn[],
  system: string,
  token?: string,
): Promise<AssistantLoopResult> {
  const messages: LoopMessage[] = turns.map((turn) => ({ role: turn.role, content: turn.content }));
  const toolEvents: ToolEvent[] = [];
  let model = "unknown";

  for (let round = 1; round <= MAX_ROUNDS; round += 1) {
    const response = await chat({
      messages: messages as unknown as LlmMessage[],
      system,
      tools: toolSchemas(),
    });
    model = response.model;

    const toolCalls = response.toolCalls ?? [];
    if (!toolCalls.length) {
      return { content: response.text, model, toolEvents, rounds: round };
    }

    const destructive = toolCalls.find((call) => isDestructiveTool(call.name));
    if (destructive) {
      return {
        content: response.text,
        model,
        toolEvents,
        proposal: proposalFor(destructive),
        rounds: round,
      };
    }

    messages.push(toAssistantToolUseBlocks(response.text, toolCalls));

    const results: ToolResultBlock[] = [];
    for (const call of toolCalls) {
      const result = await executeTool(call.name, call.input, token);
      toolEvents.push({ name: call.name, input: call.input, ok: result.ok, error: result.error });
      results.push({
        type: "tool_result",
        tool_use_id: call.id,
        content: stringifyToolResult(result),
      });
    }
    messages.push(toToolResultMessage(results));
  }

  return {
    content: "Reached the maximum tool-calling rounds without a final answer.",
    model,
    toolEvents,
    rounds: MAX_ROUNDS,
  };
}
