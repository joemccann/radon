/**
 * Provider-agnostic LLM layer.
 *
 * One async `chat()` entrypoint targets Anthropic natively (default), any
 * OpenAI-compatible base URL (OpenAI / Groq / DeepSeek / Ollama), or Gemini,
 * selected by env. Request and response are normalized so call sites never see
 * provider-specific shapes. A configurable fallback provider is tried when the
 * primary fails. Honors ASSISTANT_MOCK for offline tests.
 */

export type LlmRole = "user" | "assistant";

export type LlmMessage = {
  role: LlmRole;
  content: string;
};

export type LlmTool = {
  name: string;
  description?: string;
  input_schema: Record<string, unknown>;
};

export type LlmProviderName = "anthropic" | "openai" | "gemini";

export type LlmChatRequest = {
  messages: LlmMessage[];
  system?: string;
  tools?: LlmTool[];
  model?: string;
  provider?: LlmProviderName;
  maxTokens?: number;
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

const ANTHROPIC_ENV_KEYS = ["ANTHROPIC_API_KEY", "CLAUDE_CODE_API_KEY", "CLAUDE_API_KEY"];

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

function resolveProvider(request: LlmChatRequest): LlmProviderName {
  const requested = request.provider ?? (envValue("LLM_PROVIDER") as LlmProviderName | undefined);
  if (requested === "anthropic" || requested === "openai" || requested === "gemini") {
    return requested;
  }
  return DEFAULT_PROVIDER;
}

function resolveFallbackProvider(primary: LlmProviderName): LlmProviderName | undefined {
  const configured = envValue("LLM_FALLBACK_PROVIDER") as LlmProviderName | undefined;
  if (configured !== "anthropic" && configured !== "openai" && configured !== "gemini") {
    return undefined;
  }
  if (configured === primary) return undefined;
  return configured;
}

function maxTokensFor(request: LlmChatRequest): number {
  return request.maxTokens ?? DEFAULT_MAX_TOKENS;
}

function lastUserContent(messages: LlmMessage[]): string {
  const lastUser = [...messages].reverse().find((message) => message.role === "user");
  return lastUser?.content ?? "";
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
  const model = request.model || envValue("ANTHROPIC_MODEL") || "claude-sonnet-4-5-20250929";

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

// --- OpenAI-compatible (OpenAI / Groq / DeepSeek / Ollama) ----------------

type OpenAiToolCall = {
  id?: string;
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

function openAiConfig(provider: LlmProviderName) {
  if (provider === "openai") {
    return {
      apiKey: envValue("OPENAI_API_KEY"),
      baseUrl: envValue("OPENAI_BASE_URL") || "https://api.openai.com/v1",
      model: envValue("OPENAI_MODEL") || "gpt-4o",
      label: "OpenAI",
    };
  }
  // Groq exposes the same OpenAI-compatible surface; kept selectable via
  // LLM_PROVIDER=openai + OPENAI_BASE_URL for DeepSeek/Ollama/etc.
  return {
    apiKey: envValue("GROQ_API_KEY"),
    baseUrl: envValue("GROQ_BASE_URL") || "https://api.groq.com/openai/v1",
    model: envValue("GROQ_MODEL") || "llama-3.3-70b-versatile",
    label: "Groq",
  };
}

function toOpenAiMessages(request: LlmChatRequest) {
  const messages: Array<{ role: string; content: string }> = [];
  if (request.system) messages.push({ role: "system", content: request.system });
  for (const message of request.messages) {
    messages.push({ role: message.role, content: message.content });
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

async function callOpenAiCompatible(request: LlmChatRequest, provider: LlmProviderName): Promise<LlmChatResponse> {
  const config = openAiConfig(provider);
  if (!config.apiKey) {
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
      parts: [{ text: message.content }],
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

function dispatch(provider: LlmProviderName, request: LlmChatRequest): Promise<LlmChatResponse> {
  if (provider === "anthropic") return callAnthropic(request);
  if (provider === "gemini") return callGemini(request);
  return callOpenAiCompatible(request, provider);
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

    const fallbackResult = await dispatch(fallback, request);
    return { ...fallbackResult, usedFallback: true };
  }
}
