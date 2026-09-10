/**
 * Provider-agnostic LLM layer.
 *
 * One async `chat()` entrypoint targets:
 *   - xAI Grok (OpenAI-compatible; preferred when XAI_API_KEY is set)
 *   - Anthropic (native Messages API; historical default)
 *   - OpenAI-compatible bases (OpenAI / Groq / DeepSeek / Ollama)
 *   - Gemini
 *
 * Request and response are normalized so call sites never see provider-specific
 * shapes. A configurable fallback provider is tried when the primary fails.
 * Honors ASSISTANT_MOCK for offline tests.
 *
 * Note: SuperGrok / grok.com consumer subscriptions are not an API. CMD+J
 * uses the xAI Inference API (console.x.ai) with XAI_API_KEY / GROK_API_KEY.
 */

import { DEFAULT_MODELS } from "./frontier";

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
export type LlmProviderName = "xai" | "grok" | "anthropic" | "openai" | "gemini";

export type LlmChatRequest = {
  messages: LlmMessage[];
  system?: string;
  tools?: LlmTool[];
  model?: string;
  provider?: LlmProviderName;
  maxTokens?: number;
  /** Per-turn abort (client hung up, wall clock); merged with the request timeout. */
  signal?: AbortSignal;
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

const DEFAULT_MAX_TOKENS = 1200;
const DEFAULT_PROVIDER: LlmProviderName = "anthropic";
const KNOWN_PROVIDERS: readonly LlmProviderName[] = [
  "xai",
  "grok",
  "anthropic",
  "openai",
  "gemini",
];

const ANTHROPIC_ENV_KEYS = ["ANTHROPIC_API_KEY", "CLAUDE_CODE_API_KEY", "CLAUDE_API_KEY"];
const XAI_ENV_KEYS = ["XAI_API_KEY", "GROK_API_KEY"];

/**
 * The model each provider is called with when nothing else names one: no
 * per-turn selection from the picker and no `<PROVIDER>_MODEL` env pin. Same
 * ids lib/llm/catalog.ts serves as BUILTIN_FRONTIER; see lib/llm/frontier.ts
 * for why they have to be one constant.
 */
export { DEFAULT_MODELS };

const isMockMode = () =>
  process.env.ASSISTANT_MOCK === "1" ||
  (process.env.NODE_ENV === "test" && process.env.ASSISTANT_MOCK !== "0");

function envValue(key: string): string | undefined {
  const value = process.env[key]?.trim();
  return value ? value : undefined;
}

function resolveAnthropicApiKey(): string | undefined {
  for (const key of ANTHROPIC_ENV_KEYS) {
    const value = envValue(key);
    if (value) return value;
  }
  return undefined;
}

export function resolveXaiApiKey(): string | undefined {
  for (const key of XAI_ENV_KEYS) {
    const value = envValue(key);
    if (value) return value;
  }
  return undefined;
}

function isKnownProvider(value: string | undefined): value is LlmProviderName {
  return Boolean(value && (KNOWN_PROVIDERS as readonly string[]).includes(value));
}

/** Normalize aliases so dispatch only sees canonical names. */
export function normalizeProvider(name: LlmProviderName): Exclude<LlmProviderName, "grok"> {
  return name === "grok" ? "xai" : name;
}

/**
 * Which provider owns a model id. The picker sends a model, not a provider, so
 * a Grok selection has to reach xAI on a host whose default is Anthropic.
 * Prefix match only: xAI uses dots (`grok-4.6`), Anthropic dashes.
 */
export function providerForModel(model: string | undefined): Exclude<LlmProviderName, "grok"> | undefined {
  const id = model?.trim().toLowerCase();
  if (!id) return undefined;
  if (id.startsWith("claude-")) return "anthropic";
  if (id.startsWith("grok-")) return "xai";
  if (id.startsWith("gpt-") || id.startsWith("o1") || id.startsWith("o3")) return "openai";
  if (id.startsWith("gemini-")) return "gemini";
  return undefined;
}

/**
 * Provider resolution order:
 * 1. Explicit request.provider
 * 2. The provider that owns request.model (a per-turn selection outranks host env)
 * 3. LLM_PROVIDER env
 * 4. Auto-prefer xAI when XAI_API_KEY / GROK_API_KEY is set (CMD+J → Grok)
 * 5. Anthropic fallback
 */
export function resolveProvider(
  request: Pick<LlmChatRequest, "provider" | "model"> = {},
): Exclude<LlmProviderName, "grok"> {
  if (isKnownProvider(request.provider)) {
    return normalizeProvider(request.provider);
  }
  const byModel = providerForModel(request.model);
  if (byModel) return byModel;

  const requested = envValue("LLM_PROVIDER") as LlmProviderName | undefined;
  if (isKnownProvider(requested)) {
    return normalizeProvider(requested);
  }
  if (resolveXaiApiKey()) {
    return "xai";
  }
  return DEFAULT_PROVIDER === "grok" ? "xai" : DEFAULT_PROVIDER;
}

function resolveFallbackProvider(
  primary: Exclude<LlmProviderName, "grok">,
): Exclude<LlmProviderName, "grok"> | undefined {
  const configured = envValue("LLM_FALLBACK_PROVIDER") as LlmProviderName | undefined;
  if (!isKnownProvider(configured)) return undefined;
  const normalized = normalizeProvider(configured);
  if (normalized === primary) return undefined;
  return normalized;
}

function maxTokensFor(request: LlmChatRequest): number {
  return request.maxTokens ?? DEFAULT_MAX_TOKENS;
}

function lastUserContent(messages: LlmMessage[]): string {
  const lastUser = [...messages].reverse().find((message) => message.role === "user");
  if (!lastUser) return "";
  if (typeof lastUser.content === "string") return lastUser.content;
  return lastUser.content
    .filter((block): block is Extract<LlmContentBlock, { type: "text" }> => block.type === "text")
    .map((block) => block.text)
    .join("");
}

function mockResponse(request: LlmChatRequest, provider: LlmProviderName): LlmChatResponse {
  const prompt = lastUserContent(request.messages) || "No user message provided.";
  return {
    provider,
    model: "mock",
    text: `Mock ${provider} response: ${prompt}`,
    stopReason: "end_turn",
    usage: { inputTokens: 0, outputTokens: 0 },
  };
}

async function readErrorDetail(response: Response): Promise<string> {
  try {
    return await response.text();
  } catch {
    return "";
  }
}

const LLM_REQUEST_TIMEOUT_MS = 45_000;

function llmRequestSignal(request: Pick<LlmChatRequest, "signal">): AbortSignal {
  const timeout = AbortSignal.timeout(LLM_REQUEST_TIMEOUT_MS);
  return request.signal ? AbortSignal.any([request.signal, timeout]) : timeout;
}

// --- Anthropic (native Messages API) -------------------------------------

type AnthropicContentBlock = {
  type: string;
  text?: string;
  id?: string;
  name?: string;
  input?: Record<string, unknown>;
};

type AnthropicResponse = {
  model?: string;
  content?: AnthropicContentBlock[];
  stop_reason?: string;
  usage?: { input_tokens?: number; output_tokens?: number };
};

async function callAnthropic(request: LlmChatRequest): Promise<LlmChatResponse> {
  const apiKey = resolveAnthropicApiKey();
  if (!apiKey) {
    throw new Error("Missing Anthropic API key. Set ANTHROPIC_API_KEY, CLAUDE_CODE_API_KEY, or CLAUDE_API_KEY.");
  }

  const url = envValue("ANTHROPIC_API_URL") || "https://api.anthropic.com/v1/messages";
  const model = request.model || envValue("ANTHROPIC_MODEL") || DEFAULT_MODELS.anthropic;

  const body: Record<string, unknown> = {
    model,
    max_tokens: maxTokensFor(request),
    messages: request.messages.map((message) => ({ role: message.role, content: message.content })),
  };
  if (request.system) body.system = request.system;
  if (request.tools?.length) body.tools = request.tools;

  const response = await fetch(url, {
    method: "POST",
    headers: {
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
      "content-type": "application/json",
      accept: "application/json",
    },
    body: JSON.stringify(body),
    signal: llmRequestSignal(request),
  });

  if (!response.ok) {
    throw new Error(`Anthropic request failed (${response.status}): ${await readErrorDetail(response)}`);
  }

  const data = (await response.json()) as AnthropicResponse;
  const blocks = data.content ?? [];
  const text = blocks
    .filter((block) => block.type === "text" && typeof block.text === "string")
    .map((block) => block.text)
    .join("");

  const toolCalls = blocks
    .filter((block) => block.type === "tool_use" && block.name)
    .map((block) => ({ id: block.id ?? "", name: block.name as string, input: block.input ?? {} }));

  return {
    provider: "anthropic",
    model: data.model ?? model,
    text,
    toolCalls: toolCalls.length ? toolCalls : undefined,
    stopReason: data.stop_reason,
    usage: normalizeUsage(data.usage?.input_tokens, data.usage?.output_tokens),
  };
}

// --- OpenAI-compatible (xAI / OpenAI / Groq / DeepSeek / Ollama) ----------

type OpenAiToolCall = {
  id?: string;
  type?: string;
  function?: { name?: string; arguments?: string };
};

type OpenAiResponse = {
  model?: string;
  choices?: Array<{
    message?: { content?: string | null; tool_calls?: OpenAiToolCall[] };
    finish_reason?: string;
  }>;
  usage?: { prompt_tokens?: number; completion_tokens?: number };
};

type OpenAiContentPart =
  | { type: "text"; text: string }
  | { type: "image_url"; image_url: { url: string } };

type OpenAiOutboundMessage =
  | { role: string; content: string | OpenAiContentPart[] | null; tool_calls?: OpenAiToolCall[] }
  | { role: "tool"; tool_call_id: string; content: string };

function openAiConfig(provider: Exclude<LlmProviderName, "grok" | "anthropic" | "gemini">) {
  if (provider === "xai") {
    return {
      apiKey: resolveXaiApiKey(),
      baseUrl: envValue("XAI_BASE_URL") || envValue("GROK_BASE_URL") || "https://api.x.ai/v1",
      model: envValue("XAI_MODEL") || envValue("GROK_MODEL") || DEFAULT_MODELS.xai,
      label: "xAI Grok",
    };
  }
  return {
    apiKey: envValue("OPENAI_API_KEY"),
    baseUrl: envValue("OPENAI_BASE_URL") || "https://api.openai.com/v1",
    model: envValue("OPENAI_MODEL") || DEFAULT_MODELS.openai,
    label: "OpenAI",
  };
}

/**
 * Map Anthropic-style structured tool turns onto OpenAI chat/completions
 * message roles so the multi-round assistant loop works on xAI/Grok.
 */
export function toOpenAiMessages(request: LlmChatRequest): OpenAiOutboundMessage[] {
  const messages: OpenAiOutboundMessage[] = [];
  if (request.system) messages.push({ role: "system", content: request.system });

  for (const message of request.messages) {
    if (typeof message.content === "string") {
      messages.push({ role: message.role, content: message.content });
      continue;
    }

    const blocks = message.content;
    const toolResults = blocks.filter(
      (block): block is Extract<LlmContentBlock, { type: "tool_result" }> => block.type === "tool_result",
    );
    if (toolResults.length > 0) {
      for (const result of toolResults) {
        messages.push({
          role: "tool",
          tool_call_id: result.tool_use_id,
          content: result.content,
        });
      }
      continue;
    }

    const text = blocks
      .filter((block): block is Extract<LlmContentBlock, { type: "text" }> => block.type === "text")
      .map((block) => block.text)
      .join("");
    const toolUses = blocks.filter(
      (block): block is Extract<LlmContentBlock, { type: "tool_use" }> => block.type === "tool_use",
    );
    const hasImage = blocks.some((block) => block.type === "image");

    if (toolUses.length > 0) {
      messages.push({
        role: "assistant",
        content: text.length > 0 ? text : null,
        tool_calls: toolUses.map((call) => ({
          id: call.id,
          type: "function",
          function: {
            name: call.name,
            arguments: JSON.stringify(call.input ?? {}),
          },
        })),
      });
      continue;
    }

    if (hasImage) {
      // Images force the array content shape; text-only turns stay plain
      // strings so the provider-side prompt cache keeps hitting.
      messages.push({
        role: message.role,
        content: blocks.flatMap((block): OpenAiContentPart[] => {
          if (block.type === "text") return [{ type: "text", text: block.text }];
          if (block.type === "image") {
            return [{
              type: "image_url",
              image_url: { url: `data:${block.source.media_type};base64,${block.source.data}` },
            }];
          }
          return [];
        }),
      });
      continue;
    }

    messages.push({ role: message.role, content: text });
  }

  return messages;
}

function toOpenAiTools(tools: LlmTool[] | undefined) {
  if (!tools?.length) return undefined;
  return tools.map((tool) => ({
    type: "function",
    function: {
      name: tool.name,
      description: tool.description,
      parameters: tool.input_schema,
    },
  }));
}

async function callOpenAiCompatible(
  request: LlmChatRequest,
  provider: "xai" | "openai",
): Promise<LlmChatResponse> {
  const config = openAiConfig(provider);
  if (!config.apiKey) {
    if (provider === "xai") {
      throw new Error(
        "Missing xAI API key. Set XAI_API_KEY (or GROK_API_KEY) from https://console.x.ai. " +
          "A SuperGrok / grok.com subscription alone is not enough for server-side chat.",
      );
    }
    throw new Error(`Missing ${config.label} API key.`);
  }

  const url = `${config.baseUrl.replace(/\/$/, "")}/chat/completions`;
  const model = request.model || config.model;

  const body: Record<string, unknown> = {
    model,
    max_tokens: maxTokensFor(request),
    messages: toOpenAiMessages(request),
  };
  const tools = toOpenAiTools(request.tools);
  if (tools) body.tools = tools;

  const response = await fetch(url, {
    method: "POST",
    headers: {
      authorization: `Bearer ${config.apiKey}`,
      "content-type": "application/json",
      accept: "application/json",
    },
    body: JSON.stringify(body),
    signal: llmRequestSignal(request),
  });

  if (!response.ok) {
    throw new Error(`${config.label} request failed (${response.status}): ${await readErrorDetail(response)}`);
  }

  const data = (await response.json()) as OpenAiResponse;
  const choice = data.choices?.[0];
  const text = choice?.message?.content ?? "";

  const toolCalls = (choice?.message?.tool_calls ?? [])
    .filter((call) => call.function?.name)
    .map((call) => ({
      id: call.id ?? "",
      name: call.function?.name as string,
      input: parseToolArguments(call.function?.arguments),
    }));

  return {
    provider,
    model: data.model ?? model,
    text,
    toolCalls: toolCalls.length ? toolCalls : undefined,
    stopReason: choice?.finish_reason,
    usage: normalizeUsage(data.usage?.prompt_tokens, data.usage?.completion_tokens),
  };
}

function parseToolArguments(raw: string | undefined): Record<string, unknown> {
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    return typeof parsed === "object" && parsed !== null ? (parsed as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

// --- Gemini ---------------------------------------------------------------

type GeminiResponse = {
  candidates?: Array<{
    content?: { parts?: Array<{ text?: string }> };
    finishReason?: string;
  }>;
  usageMetadata?: { promptTokenCount?: number; candidatesTokenCount?: number };
};

async function callGemini(request: LlmChatRequest): Promise<LlmChatResponse> {
  if (request.tools?.length) {
    // Gemini structured tool-turn parity is not implemented here. Throwing is
    // intentional so the configured tool-capable provider handles the turn.
    throw new Error("Gemini tool requests require a tool-capable fallback provider.");
  }
  const apiKey = envValue("GEMINI_API_KEY");
  if (!apiKey) {
    throw new Error("Missing Gemini API key. Set GEMINI_API_KEY.");
  }

  const base = envValue("GEMINI_BASE_URL") || "https://generativelanguage.googleapis.com/v1beta";
  const model = request.model || envValue("GEMINI_MODEL") || "gemini-2.5-pro";
  const url = `${base.replace(/\/$/, "")}/models/${model}:generateContent?key=${encodeURIComponent(apiKey)}`;

  const body: Record<string, unknown> = {
    contents: request.messages.map((message) => ({
      role: message.role === "assistant" ? "model" : "user",
      parts: [{ text: typeof message.content === "string" ? message.content : lastUserContent([message]) }],
    })),
    generationConfig: { maxOutputTokens: maxTokensFor(request) },
  };
  if (request.system) {
    body.systemInstruction = { parts: [{ text: request.system }] };
  }

  const response = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify(body),
    signal: llmRequestSignal(request),
  });

  if (!response.ok) {
    throw new Error(`Gemini request failed (${response.status}): ${await readErrorDetail(response)}`);
  }

  const data = (await response.json()) as GeminiResponse;
  const candidate = data.candidates?.[0];
  const text = (candidate?.content?.parts ?? [])
    .map((part) => part.text ?? "")
    .join("");

  return {
    provider: "gemini",
    model,
    text,
    stopReason: candidate?.finishReason,
    usage: normalizeUsage(data.usageMetadata?.promptTokenCount, data.usageMetadata?.candidatesTokenCount),
  };
}

function normalizeUsage(input: number | undefined, output: number | undefined): LlmUsage | undefined {
  if (typeof input !== "number" && typeof output !== "number") return undefined;
  return { inputTokens: input ?? 0, outputTokens: output ?? 0 };
}

function dispatch(
  provider: Exclude<LlmProviderName, "grok">,
  request: LlmChatRequest,
): Promise<LlmChatResponse> {
  if (provider === "anthropic") return callAnthropic(request);
  if (provider === "gemini") return callGemini(request);
  if (provider === "xai") return callOpenAiCompatible(request, "xai");
  return callOpenAiCompatible(request, "openai");
}

/**
 * Thin adapter the assistant route (F7) consumes: takes the existing
 * `{ messages, system }` shape and returns just the completion text plus the
 * provider/model/usage envelope the route already serializes. Call sites stay
 * provider-agnostic; switching providers is an env change, not a code change.
 */
export async function assistantChat(
  messages: LlmMessage[],
  system?: string,
  options?: Pick<LlmChatRequest, "tools" | "model" | "provider" | "maxTokens">,
): Promise<LlmChatResponse> {
  return chat({ messages, system, ...options });
}

export async function chat(request: LlmChatRequest): Promise<LlmChatResponse> {
  const provider = resolveProvider(request);

  if (isMockMode()) {
    return mockResponse(request, provider);
  }

  try {
    return await dispatch(provider, request);
  } catch (primaryError) {
    const fallback = resolveFallbackProvider(provider);
    if (!fallback) throw primaryError;

    // The fallback provider gets its OWN default model. A per-turn selection
    // is scoped to the provider that owns it, so handing "grok-4.6" to
    // Anthropic 404s and converts a transient xAI blip into a hard turn
    // failure - the exact outage the fallback exists to absorb. Dropping
    // `provider` too keeps an explicit request.provider from pinning the
    // retry back onto the endpoint that just failed.
    const fallbackResult = await dispatch(fallback, {
      ...request,
      model: undefined,
      provider: undefined,
    });
    return { ...fallbackResult, usedFallback: true };
  }
}
