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
 * SUBSCRIPTIONS ONLY (operator mandate 2026-09-18): Anthropic, xAI and
 * OpenAI calls authenticate with the operator's Claude Max, SuperGrok and
 * ChatGPT grants (lib/llm/subscriptionAuth.ts). A prepaid console key is
 * never a fallback; RADON_LADDER_ALLOW_PREPAID=1 is the only way one is read,
 * matching scripts/clients/model_ladder.py. Gemini has no HTTP subscription
 * path (the Antigravity grant lacks the generativelanguage scope), so it is
 * prepaid-only and therefore off unless that flag is set.
 */

import { DEFAULT_MODELS } from "./frontier";
import {
  allowPrepaid,
  resolveAnthropicSubscription,
  resolveCodexSubscription,
  resolveXaiSubscription,
} from "./subscriptionAuth";

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
  /** Override the 45s provider HTTP timeout (assistant tool rounds use 90s). */
  timeoutMs?: number;
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
/** Claude Code OAuth grants need this beta and the Claude Code identity on Messages. */
const ANTHROPIC_OAUTH_BETA = "oauth-2025-04-20";
const CLAUDE_CODE_SYSTEM_PREFIX = "You are Claude Code, Anthropic's official CLI for Claude.";
const CLAUDE_CODE_USER_AGENT = "claude-cli/2.1.140";
/** ChatGPT subscription endpoint the codex CLI uses; streaming only. */
const CHATGPT_CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses";

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

/** Prepaid Anthropic console key: read only under RADON_LADDER_ALLOW_PREPAID. */
function resolveAnthropicApiKey(): string | undefined {
  if (!allowPrepaid()) return undefined;
  for (const key of ANTHROPIC_ENV_KEYS) {
    const value = envValue(key);
    if (value) return value;
  }
  return undefined;
}

type AnthropicAuth = { kind: "subscription" | "prepaid"; token: string };

function resolveAnthropicAuth(): AnthropicAuth | undefined {
  const grant = resolveAnthropicSubscription();
  if (grant) return { kind: "subscription", token: grant.token };
  const prepaid = resolveAnthropicApiKey();
  return prepaid ? { kind: "prepaid", token: prepaid } : undefined;
}

/** Presence only: can this deployment serve Anthropic at all? */
export function hasAnthropicAuth(): boolean {
  return Boolean(resolveAnthropicAuth());
}

/** Prepaid xAI console key: read only under RADON_LADDER_ALLOW_PREPAID. */
export function resolveXaiApiKey(): string | undefined {
  if (!allowPrepaid()) return undefined;
  for (const key of XAI_ENV_KEYS) {
    const value = envValue(key);
    if (value) return value;
  }
  return undefined;
}

/** SuperGrok subscription grant: env override first, then ~/.grok/auth.json. */
export function resolveXaiSubscriptionToken(): string | undefined {
  return resolveXaiSubscription()?.token;
}

/** The subscription grant; a prepaid key only under RADON_LADDER_ALLOW_PREPAID. */
export function resolveXaiAuth(): string | undefined {
  return resolveXaiSubscriptionToken() ?? resolveXaiApiKey();
}

/** Prepaid OpenAI console key: read only under RADON_LADDER_ALLOW_PREPAID. */
function resolveOpenAiApiKey(): string | undefined {
  return allowPrepaid() ? envValue("OPENAI_API_KEY") : undefined;
}

/** Presence only: can this deployment serve OpenAI at all? */
export function hasOpenAiAuth(): boolean {
  return Boolean(resolveCodexSubscription() || resolveOpenAiApiKey());
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
 * 4. Auto-prefer xAI when a Grok subscription grant or XAI_API_KEY / GROK_API_KEY is set (CMD+J → Grok)
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
  if (resolveXaiAuth()) {
    return "xai";
  }
  return DEFAULT_PROVIDER === "grok" ? "xai" : DEFAULT_PROVIDER;
}

function resolveFallbackProvider(
  primary: Exclude<LlmProviderName, "grok">,
): Exclude<LlmProviderName, "grok"> | undefined {
  const configured = envValue("LLM_FALLBACK_PROVIDER") as LlmProviderName | undefined;
  if (!isKnownProvider(configured)) {
    // xAI is only AUTO-preferred (resolution step 4). When nobody pinned a
    // provider and nobody configured a fallback, losing xAI (2026-09-18: the
    // team ran out of credits, 403 on every call) must degrade to the
    // historical Anthropic default rather than fail every rewrite.
    if (
      primary === "xai" &&
      !envValue("LLM_PROVIDER") &&
      hasAnthropicAuth()
    ) {
      return "anthropic";
    }
    return undefined;
  }
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

function llmRequestSignal(request: Pick<LlmChatRequest, "signal" | "timeoutMs">): AbortSignal {
  const timeout = AbortSignal.timeout(request.timeoutMs ?? LLM_REQUEST_TIMEOUT_MS);
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

/**
 * Headers for the Anthropic Messages API. A Claude Max OAuth grant is sent as
 * a Bearer with the oauth beta; the prepaid path keeps x-api-key.
 */
export function anthropicHeaders(auth: AnthropicAuth): Record<string, string> {
  const headers: Record<string, string> = {
    "anthropic-version": "2023-06-01",
    "content-type": "application/json",
    accept: "application/json",
  };
  if (auth.kind === "subscription") {
    headers.authorization = `Bearer ${auth.token}`;
    headers["anthropic-beta"] = ANTHROPIC_OAUTH_BETA;
    headers["user-agent"] = CLAUDE_CODE_USER_AGENT;
  } else {
    headers["x-api-key"] = auth.token;
  }
  return headers;
}

/**
 * The OAuth grant is only honoured for Claude Code traffic: the Messages API
 * answers a bare 429 unless the system prompt opens with the Claude Code
 * identity block (verified live 2026-09-18). Prepend it; the caller's own
 * system text follows as a second block.
 */
export function anthropicSystem(
  auth: AnthropicAuth,
  system: string | undefined,
): string | Array<{ type: "text"; text: string }> | undefined {
  if (auth.kind !== "subscription") return system;
  const blocks: Array<{ type: "text"; text: string }> = [{ type: "text", text: CLAUDE_CODE_SYSTEM_PREFIX }];
  if (system) blocks.push({ type: "text", text: system });
  return blocks;
}

/** Shared by every direct Messages-API caller (seasonality vision, etc.). */
export function anthropicRequestAuth(): { headers: Record<string, string>; system: (s?: string) => ReturnType<typeof anthropicSystem> } | undefined {
  const auth = resolveAnthropicAuth();
  if (!auth) return undefined;
  return { headers: anthropicHeaders(auth), system: (s?: string) => anthropicSystem(auth, s) };
}

async function callAnthropic(request: LlmChatRequest): Promise<LlmChatResponse> {
  const auth = resolveAnthropicAuth();
  if (!auth) {
    throw new Error(
      "Missing Anthropic subscription. Log Claude Code in (~/.claude/.credentials.json) or set CLAUDE_CODE_OAUTH_TOKEN.",
    );
  }

  const url = envValue("ANTHROPIC_API_URL") || "https://api.anthropic.com/v1/messages";
  const model = request.model || envValue("ANTHROPIC_MODEL") || DEFAULT_MODELS.anthropic;

  const body: Record<string, unknown> = {
    model,
    max_tokens: maxTokensFor(request),
    messages: request.messages.map((message) => ({ role: message.role, content: message.content })),
  };
  const system = anthropicSystem(auth, request.system);
  if (system) body.system = system;
  if (request.tools?.length) {
    body.tools = request.tools;
    if (request.toolChoice === "none") body.tool_choice = { type: "none" };
    else if (request.toolChoice === "required") body.tool_choice = { type: "any" };
    else if (request.toolChoice === "auto") body.tool_choice = { type: "auto" };
  }

  const response = await fetch(url, {
    method: "POST",
    headers: anthropicHeaders(auth),
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
      apiKey: resolveXaiAuth(),
      baseUrl: envValue("XAI_BASE_URL") || envValue("GROK_BASE_URL") || "https://api.x.ai/v1",
      model: envValue("XAI_MODEL") || envValue("GROK_MODEL") || DEFAULT_MODELS.xai,
      label: "xAI Grok",
    };
  }
  return {
    apiKey: resolveOpenAiApiKey(),
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
      ...(tool.description ? { description: tool.description } : {}),
      parameters: tool.input_schema,
    },
  }));
}

/**
 * ChatGPT subscription via the codex grant. chatgpt.com only streams, so the
 * SSE is folded here into the same envelope the other adapters return. Tool
 * turns are refused so the configured tool-capable provider takes them.
 */
async function callChatGptSubscription(
  request: LlmChatRequest,
  grant: { token: string; accountId?: string },
): Promise<LlmChatResponse> {
  if (request.tools?.length) {
    throw new Error("ChatGPT subscription turns do not carry tools; use a tool-capable provider.");
  }
  const model = request.model || envValue("OPENAI_MODEL") || DEFAULT_MODELS.openai;
  const input = request.messages.map((message) => ({
    role: message.role,
    content: [
      {
        type: message.role === "assistant" ? "output_text" : "input_text",
        text: typeof message.content === "string" ? message.content : lastUserContent([message]),
      },
    ],
  }));
  const body: Record<string, unknown> = { model, input, store: false, stream: true };
  if (request.system) body.instructions = request.system;
  if (request.reasoningEffort) body.reasoning = { effort: request.reasoningEffort };

  const headers: Record<string, string> = {
    authorization: `Bearer ${grant.token}`,
    "content-type": "application/json",
    accept: "text/event-stream",
    "OpenAI-Beta": "responses=experimental",
  };
  if (grant.accountId) headers["chatgpt-account-id"] = grant.accountId;

  const response = await fetch(envValue("CHATGPT_CODEX_RESPONSES_URL") || CHATGPT_CODEX_RESPONSES_URL, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal: llmRequestSignal(request),
  });
  if (!response.ok) {
    throw new Error(`ChatGPT subscription request failed (${response.status}): ${await readErrorDetail(response)}`);
  }

  let text = "";
  let stopReason: string | undefined;
  let usage: LlmUsage | undefined;
  for (const line of (await response.text()).split("\n")) {
    if (!line.startsWith("data:")) continue;
    let event: { type?: string; delta?: string; response?: { status?: string; usage?: { input_tokens?: number; output_tokens?: number } } };
    try {
      event = JSON.parse(line.slice(5).trim());
    } catch {
      continue;
    }
    if (event.type === "response.output_text.delta" && typeof event.delta === "string") text += event.delta;
    if (event.type === "response.completed") {
      stopReason = event.response?.status;
      usage = normalizeUsage(event.response?.usage?.input_tokens, event.response?.usage?.output_tokens);
    }
  }
  return { provider: "openai", model, text, stopReason, usage };
}

async function callOpenAiCompatible(
  request: LlmChatRequest,
  provider: "xai" | "openai",
): Promise<LlmChatResponse> {
  if (provider === "openai" && !allowPrepaid()) {
    const grant = resolveCodexSubscription();
    if (!grant) {
      throw new Error("Missing ChatGPT subscription. Log the codex CLI in (~/.codex/auth.json) or set CODEX_OAUTH_TOKEN.");
    }
    return callChatGptSubscription(request, grant);
  }
  const config = openAiConfig(provider);
  if (!config.apiKey) {
    if (provider === "xai") {
      throw new Error(
        "Missing xAI subscription. Log the grok CLI in (~/.grok/auth.json) or set XAI_OAUTH_TOKEN.",
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
  if (tools) {
    body.tools = tools;
    if (request.toolChoice) body.tool_choice = request.toolChoice;
  }
  if (request.reasoningEffort) body.reasoning_effort = request.reasoningEffort;

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
  const apiKey = allowPrepaid() ? envValue("GEMINI_API_KEY") : undefined;
  if (!apiKey) {
    throw new Error(
      "Gemini has no subscription path here; a prepaid GEMINI_API_KEY is read only under RADON_LADDER_ALLOW_PREPAID=1.",
    );
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
  options?: Pick<LlmChatRequest, "tools" | "toolChoice" | "reasoningEffort" | "model" | "provider" | "maxTokens" | "timeoutMs">,
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
    console.warn(
      `[llm] ${provider} failed, falling back to ${fallback}: ${
        primaryError instanceof Error ? primaryError.message.slice(0, 300) : String(primaryError)
      }`,
    );

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
