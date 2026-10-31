/** Server-side adapter to the standalone model gateway. Provider credentials,
 * model defaults, capability selection and fallback belong to that service.
 * No provider request or authentication material is resolved in Next.js.
 */
import { radonFetch } from "@/lib/radonApi";

export type LlmRole = "user" | "assistant";

/** Structured content blocks used by the multi-round tool loop. */
export type LlmContentBlock =
  | { type: "text"; text: string }
  | { type: "image"; source: { type: "base64"; media_type: string; data: string } }
  | { type: "tool_use"; id: string; name: string; input: Record<string, unknown> }
  | { type: "tool_result"; tool_use_id: string; content: string };

export type LlmMessage = {
  role: LlmRole;
  content: string | LlmContentBlock[];
};

export type LlmTool = {
  name: string;
  description?: string;
  input_schema: Record<string, unknown>;
};

/** `grok` is accepted as an alias of `xai`. */
export type LlmProviderName = "xai" | "grok" | "anthropic" | "openai" | "antigravity" | "cursor" | "nvidia" | "cerebras" | "slm-tagger";

export type LlmToolChoice = "auto" | "none" | "required";
export type LlmReasoningEffort = "low" | "medium" | "high";

export type LlmChatRequest = {
  messages: LlmMessage[];
  system?: string;
  tools?: LlmTool[];
  /** OpenAI/xAI string; Anthropic is mapped to `{ type }`. Omit to leave provider default. */
  toolChoice?: LlmToolChoice;
  /** xAI grok-4.6 defaults to high; the assistant loop must pass low for tool rounds. */
  reasoningEffort?: LlmReasoningEffort;
  model?: string;
  provider?: LlmProviderName;
  maxTokens?: number;
  /** Total service request budget, including provider fallback. Defaults to 45s. */
  timeoutMs?: number;
  /** Per-turn abort (client hung up, wall clock); merged with the request timeout. */
  signal?: AbortSignal;
  /** false: request no provider fallback; the gateway still enforces capabilities. */
  fallback?: boolean;
};

export type LlmToolCall = {
  id: string;
  name: string;
  input: Record<string, unknown>;
};

export type LlmUsage = {
  inputTokens: number;
  outputTokens: number;
};

export type LlmChatResponse = {
  provider: LlmProviderName;
  model: string;
  text: string;
  toolCalls?: LlmToolCall[];
  usage?: LlmUsage;
  stopReason?: string;
  usedFallback?: boolean;
};


const PROVIDERS: readonly LlmProviderName[] = ["anthropic", "xai", "grok", "openai", "antigravity", "cursor", "nvidia", "cerebras", "slm-tagger"];
export function normalizeProvider(name: LlmProviderName): Exclude<LlmProviderName, "grok"> {
  return name === "grok" ? "xai" : name;
}

/** Pure ownership hint for explicit selections; routing remains service-owned. */
export function providerForModel(model: string | undefined): Exclude<LlmProviderName, "grok"> | undefined {
  const id = model?.trim().toLowerCase();
  if (!id) return undefined;
  if (id.startsWith("claude-")) return "anthropic";
  if (id.startsWith("grok-")) return "xai";
  if (id.startsWith("gpt-") || id.startsWith("o1") || id.startsWith("o3")) return "openai";
  if (id.startsWith("antigravity-")) return "antigravity";
  return undefined;
}

function isResponse(value: unknown): value is LlmChatResponse {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const r = value as Record<string, unknown>;
  if (!PROVIDERS.includes(r.provider as LlmProviderName) || typeof r.model !== "string" || typeof r.text !== "string") return false;
  if (r.stopReason !== undefined && (typeof r.stopReason !== "string" || ["failed", "incomplete", "error"].includes(r.stopReason))) return false;
  if (r.usedFallback !== undefined && typeof r.usedFallback !== "boolean") return false;
  if (r.toolCalls !== undefined && (!Array.isArray(r.toolCalls) || r.toolCalls.some((call) =>
    !call || typeof call !== "object" || typeof call.id !== "string" || !call.id ||
    typeof call.name !== "string" || !call.name || !call.input || typeof call.input !== "object" || Array.isArray(call.input)))) return false;
  if (r.usage !== undefined) {
    const usage = r.usage as Record<string, unknown>;
    if (!usage || typeof usage !== "object" || ![usage.inputTokens, usage.outputTokens].every(v => typeof v === "number" && Number.isFinite(v) && v >= 0)) return false;
  }
  return true;
}

export async function chat(request: LlmChatRequest): Promise<LlmChatResponse> {
  request.signal?.throwIfAborted();
  if (process.env.ASSISTANT_MOCK === "1" || (process.env.NODE_ENV === "test" && process.env.ASSISTANT_MOCK !== "0")) {
    const last = [...request.messages].reverse().find(message => message.role === "user");
    const prompt = typeof last?.content === "string" ? last.content : last?.content.filter(block => block.type === "text").map(block => block.type === "text" ? block.text : "").join("") || "No user message provided.";
    return { provider: request.provider ? normalizeProvider(request.provider) : providerForModel(request.model) ?? "antigravity", model: "mock", text: `Mock ${request.provider ?? "antigravity"} response: ${prompt}`, stopReason: "end_turn", usage: { inputTokens: 0, outputTokens: 0 } };
  }
  const { signal, ...payload } = request;
  const result = await radonFetch<unknown>("/model-gateway/chat", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload), timeout: request.timeoutMs ?? 45_000, signal,
  });
  if (!isResponse(result)) throw new Error("Model gateway response is malformed or incomplete");
  return result;
}

export async function assistantChat(
  messages: LlmMessage[], system?: string,
  options?: Pick<LlmChatRequest, "tools" | "toolChoice" | "reasoningEffort" | "model" | "provider" | "maxTokens" | "timeoutMs">,
): Promise<LlmChatResponse> {
  return chat({ messages, system, ...options });
}
