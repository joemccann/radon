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
 * tool_result) so the model sees its own calls and their outputs. Anthropic
 * accepts those blocks natively; OpenAI-compatible providers (xAI Grok) map
 * them in `toOpenAiMessages`. String turns from the UI stay strings.
 */

import { chat, type LlmMessage, type LlmToolCall, type LlmUsage } from "@/lib/llm/provider";
import {
  executeTool,
  isDestructiveTool,
  summarizeProposal,
  toolSchemas,
} from "@/lib/assistant/tools";

const MAX_ROUNDS = 6;

const CAP_FALLBACK_MESSAGE = "Reached the maximum tool-calling rounds without a final answer.";

const CAP_FORCED_FINAL_INSTRUCTION =
  "You have reached the tool-call limit. Do not request any more tools. " +
  "Answer the user's question now with what you have, and state clearly anything you could not determine.";

const REPEATED_CALL_NUDGE =
  "REPEATED CALL: identical to an earlier call this turn. Do not repeat it; " +
  "change the arguments or answer with what you have. Earlier result:\n";

export type AssistantTurn = {
  role: "user" | "assistant";
  content: string;
};

export type ToolEvent = {
  name: string;
  input: Record<string, unknown>;
  ok: boolean;
  error?: string;
  repeated?: boolean;
};

export type AssistantLoopOutcome = "answered" | "proposal" | "cap_forced_final" | "cap_fallback";

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
  usage: LlmUsage;
  outcome: AssistantLoopOutcome;
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

// Key-order-independent serialization so `{a,b}` and `{b,a}` count as the
// same tool call for the repeated-call short-circuit.
function stableStringify(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    const entries = Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableStringify(record[key])}`);
    return `{${entries.join(",")}}`;
  }
  return JSON.stringify(value) ?? "undefined";
}

function callKey(call: LlmToolCall): string {
  return `${call.name} ${stableStringify(call.input)}`;
}

function logRound(round: number, model: string, toolCalls: LlmToolCall[]): void {
  const tools = toolCalls.map((call) => call.name).join(",") || "none";
  console.log(`[assistant] round=${round} model=${model} tools=${tools}`);
}

export async function runAssistantLoop(
  turns: AssistantTurn[],
  system: string,
  token?: string,
): Promise<AssistantLoopResult> {
  const messages: LoopMessage[] = turns.map((turn) => ({ role: turn.role, content: turn.content }));
  const toolEvents: ToolEvent[] = [];
  const priorResults = new Map<string, string>();
  const usage: LlmUsage = { inputTokens: 0, outputTokens: 0 };
  let model = "unknown";

  const accumulateUsage = (roundUsage?: LlmUsage) => {
    if (!roundUsage) return;
    usage.inputTokens += roundUsage.inputTokens;
    usage.outputTokens += roundUsage.outputTokens;
  };

  for (let round = 1; round <= MAX_ROUNDS; round += 1) {
    const response = await chat({
      messages: messages as unknown as LlmMessage[],
      system,
      tools: toolSchemas(),
    });
    model = response.model;
    accumulateUsage(response.usage);

    const toolCalls = response.toolCalls ?? [];
    logRound(round, response.model, toolCalls);
    if (!toolCalls.length) {
      return { content: response.text, model, toolEvents, rounds: round, usage, outcome: "answered" };
    }

    const destructive = toolCalls.find((call) => isDestructiveTool(call.name));
    if (destructive) {
      return {
        content: response.text,
        model,
        toolEvents,
        proposal: proposalFor(destructive),
        rounds: round,
        usage,
        outcome: "proposal",
      };
    }

    messages.push(toAssistantToolUseBlocks(response.text, toolCalls));

    const results: ToolResultBlock[] = [];
    for (const call of toolCalls) {
      const key = callKey(call);
      const prior = priorResults.get(key);
      if (prior !== undefined) {
        toolEvents.push({ name: call.name, input: call.input, ok: true, repeated: true });
        results.push({
          type: "tool_result",
          tool_use_id: call.id,
          content: REPEATED_CALL_NUDGE + prior,
        });
        continue;
      }
      const result = await executeTool(call.name, call.input, token);
      const content = stringifyToolResult(result);
      priorResults.set(key, content);
      toolEvents.push({ name: call.name, input: call.input, ok: result.ok, error: result.error });
      results.push({
        type: "tool_result",
        tool_use_id: call.id,
        content,
      });
    }
    messages.push(toToolResultMessage(results));
  }

  // Cap hit. Round-MAX_ROUNDS tool results are already in `messages`, so one
  // forced tool-less final call lets the model answer with everything it has
  // instead of discarding the turn behind a canned error.
  messages.push({ role: "user", content: CAP_FORCED_FINAL_INSTRUCTION });
  try {
    const finalResponse = await chat({ messages: messages as unknown as LlmMessage[], system });
    accumulateUsage(finalResponse.usage);
    logRound(MAX_ROUNDS + 1, finalResponse.model, []);
    const text = finalResponse.text?.trim();
    if (text) {
      return {
        content: text,
        model: finalResponse.model,
        toolEvents,
        rounds: MAX_ROUNDS + 1,
        usage,
        outcome: "cap_forced_final",
      };
    }
  } catch {
    // Fall through to the canned fallback.
  }

  return {
    content: CAP_FALLBACK_MESSAGE,
    model,
    toolEvents,
    rounds: MAX_ROUNDS,
    usage,
    outcome: "cap_fallback",
  };
}
