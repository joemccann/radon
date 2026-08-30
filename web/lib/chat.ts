import type { Dispatch, SetStateAction } from "react";
import type {
  ApiMessage,
  AssistantOrderProposal,
  AssistantResponse,
  AssistantToolEvent,
  ChatImageAttachment,
  Message,
  PiResponse,
  WorkspaceSection,
} from "./types";
import { PI_COMMAND_ALIASES, PI_COMMAND_SET } from "./data";
import { placeOrderFeedback } from "./orders/placeOrderFeedback";
import {
  createTimestamp,
  formatAssistantPayload,
  formatPiPayload,
  normalizeTextLines,
  sleep,
} from "./utils";

export function isPiCommandInput(raw: string) {
  const normalized = raw.trim().toLowerCase();
  const first = normalized.replace(/^\//, "").split(/\s+/)[0];
  return first ? PI_COMMAND_SET.has(first) : false;
}

export function normalizeCommandInput(raw: string) {
  const trimmed = raw.trim();
  return trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
}

export function routeToPiPrompt(raw: string): string | null {
  const normalized = raw.trim();
  if (!normalized) {
    return null;
  }

  if (isPiCommandInput(normalized)) {
    return normalizeCommandInput(normalized);
  }

  const lower = normalized.toLowerCase();
  const alias = PI_COMMAND_ALIASES[lower];
  if (alias) {
    return alias;
  }

  const analyzeTicker = normalized.match(/^analyze\s+\$?([A-Za-z][A-Za-z0-9.-]{0,9})\s*$/);
  if (analyzeTicker) {
    return `/evaluate ${analyzeTicker[1].toUpperCase()}`;
  }

  if (/\bportfolio\b/.test(lower) || /\bpositions?\b/.test(lower)) {
    return "/portfolio";
  }

  if (/\bdiscover\b/.test(lower)) {
    return "/discover";
  }

  if (/\bjournal\b/.test(lower)) {
    return "/journal";
  }

  // Leap-scan MUST be matched before the generic `\bscan\b` branch — otherwise
  // "run a leap scan for mag7" is swallowed by the flow scanner. The LEAP
  // scanner requires a ticker group, so an unrecognized target falls back to
  // the mag7 megacap preset rather than erroring on a bare `/leap-scan`.
  if (/\bleap\b/.test(lower)) {
    return `/leap-scan --preset ${detectLeapPreset(lower)}`;
  }

  if (/\bscan\b/.test(lower)) {
    return `/scan`;
  }

  return null;
}

/**
 * Map natural-language phrasing to one of the LEAP scanner's whitelisted
 * presets (sectors, mag7, semis, emerging, china — see `executeLeapScan` in
 * `app/api/pi/route.ts`). Defaults to `mag7` (megacaps) when no group is named,
 * so the command always runs against a real ticker set.
 */
const LEAP_PRESET_PATTERNS: ReadonlyArray<readonly [RegExp, string]> = [
  [/\bsemi(?:conductor)?s?\b|\bchips?\b|\bsox\b/, "semis"],
  [/\bsectors?\b/, "sectors"],
  [/\bemerging\b|\bem\b/, "emerging"],
  [/\bchina\b|\bchinese\b/, "china"],
  [/\bmag\s?7\b|\bmagnificent\b/, "mag7"],
];

function detectLeapPreset(lower: string): string {
  for (const [pattern, preset] of LEAP_PRESET_PATTERNS) {
    if (pattern.test(lower)) {
      return preset;
    }
  }
  return "mag7";
}

export function fallbackReply(input: string) {
  const query = input.trim().toLowerCase();

  if (!query) {
    return "I can analyze flow structure, scan alignment, and risk, then map to a decision view.";
  }

  if (query.includes("analyze brze") || query.includes("brze")) {
    return "I could not load the live position context. Retry the authenticated assistant before making a portfolio decision.";
  }

  if (query.includes("analyze rr") || query.includes(" rr")) {
    return "I could not load current flow context. Retry the authenticated assistant before changing exposure.";
  }

  if (query.includes("compare support vs against") || query.includes("support against") || query.includes("support vs against")) {
    return "Live support and against-flow groups are unavailable. Retry when the authenticated assistant is connected.";
  }

  if (query.includes("action") || query.includes("items")) {
    return "Live action items are unavailable. Retry when the authenticated assistant is connected.";
  }

  if (query.includes("watch list") || query.includes("watch closely")) {
    return "Live watchlist context is unavailable. Retry when the authenticated assistant is connected.";
  }

  if (query.includes("portfolio") || query.includes("positions")) {
    return "The live portfolio snapshot is unavailable. Retry the authenticated assistant; no cached account figures are embedded in this client.";
  }

  return "I can review any ticker, compare support/against groups, or walk through risk and Kelly logic for any position.";
}

async function readJsonBody<T>(response: Response): Promise<T | null> {
  const textReader = (response as { text?: () => Promise<string> }).text;
  if (typeof textReader === "function") {
    try {
      const raw = await textReader.call(response);
      if (!raw.trim()) {
        return null;
      }
      return JSON.parse(raw) as T;
    } catch {
      return null;
    }
  }

  try {
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

/**
 * The edge abandons an assistant turn before the route does, and its 504 body
 * is not JSON, so `payload.error` is empty and the status is the only thing
 * carrying the reason. "Assistant service returned an error." told the
 * operator nothing on 2026-08-29 when a pasted-chart turn timed out at the
 * proxy while the turn itself was still running.
 */
const TIMEOUT_STATUSES = new Set([408, 504]);

function assistantErrorMessage(status: number | undefined, error: string | undefined): string {
  if (typeof status === "number" && TIMEOUT_STATUSES.has(status)) {
    return "The turn timed out before the assistant answered. A smaller image or a shorter question may get through.";
  }
  return error ? `Error: ${error}` : "Assistant service returned an error.";
}

/**
 * Text-only assistant turn. Delegates to {@link requestAssistantTurn} so there
 * is ONE reader of the endpoint: the route answers `text/event-stream` now, and
 * a second call site parsing the body as JSON would silently fall back to
 * canned copy on every real turn.
 */
export async function requestAssistantReply(history: ApiMessage[], latestMessage: string): Promise<string> {
  const turn = await requestAssistantTurn(history, latestMessage);
  return turn.content;
}

/**
 * The turn's user message. Text-only turns keep the plain-string content shape
 * the endpoint has always accepted; pasted images promote it to the Anthropic
 * block array, images first so the model reads them before the question.
 */
function buildUserMessage(text: string, attachments: ChatImageAttachment[]): ApiMessage {
  if (!attachments.length) {
    return { role: "user", content: text };
  }
  return {
    role: "user",
    content: [
      ...attachments.map((attachment) => ({
        type: "image" as const,
        source: {
          type: "base64" as const,
          media_type: attachment.mediaType,
          data: attachment.data,
        },
      })),
      // An image-only turn carries no text block: an empty one is not content.
      ...(text ? [{ type: "text" as const, text }] : []),
    ],
  };
}

export type AssistantTurn = {
  content: string;
  proposal: AssistantOrderProposal | null;
  /** Per-tool-call telemetry from the agentic loop; drives <EngineTrace>. */
  toolEvents: AssistantToolEvent[];
  /** Concrete model id the loop ran on; drives the trace's engine chip. */
  model: string | null;
};

/** Live progress from the open turn, before its final payload exists. */
export type AssistantStreamEvent =
  | { type: "start" }
  | { type: "tool"; event: AssistantToolEvent };

/**
 * A stream that ends without a `done` frame — killed upstream, severed
 * connection, a proxy that gave up mid-body. It MUST read as a failure: an
 * empty assistant bubble is worse than the 504 this replaced, because nothing
 * on screen says the turn did not finish.
 */
const TRUNCATED_STREAM_MESSAGE =
  "The connection dropped and the turn did not finish. No order was placed. Ask again.";

function parseFrameData(raw: string): unknown {
  try {
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

/**
 * Reads the `/api/assistant` event stream. Frames are `event:`/`data:` lines
 * terminated by a blank line, and arrive on arbitrary chunk boundaries, so the
 * buffer is split on the terminator rather than per read.
 */
async function readAssistantStream(
  body: ReadableStream<Uint8Array>,
  latestMessage: string,
  onEvent?: (event: AssistantStreamEvent) => void,
): Promise<AssistantTurn> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const streamedTools: AssistantToolEvent[] = [];
  let buffer = "";
  let settled: AssistantTurn | null = null;
  let failure: string | null = null;

  const handle = (event: string, raw: string) => {
    if (event === "start") {
      onEvent?.({ type: "start" });
      return;
    }
    if (event === "tool") {
      const parsed = parseFrameData(raw) as AssistantToolEvent | null;
      if (parsed) {
        streamedTools.push(parsed);
        onEvent?.({ type: "tool", event: parsed });
      }
      return;
    }
    if (event === "error") {
      const parsed = parseFrameData(raw) as { error?: string } | null;
      failure = assistantErrorMessage(undefined, parsed?.error);
      return;
    }
    if (event === "done") {
      const payload = parseFrameData(raw) as AssistantResponse | null;
      settled = {
        content:
          typeof payload?.content === "string" && payload.content.trim()
            ? formatAssistantPayload(payload.content)
            : fallbackReply(latestMessage),
        proposal: payload?.proposal ?? null,
        toolEvents: Array.isArray(payload?.toolEvents) ? payload.toolEvents : streamedTools,
        model: typeof payload?.model === "string" ? payload.model : null,
      };
    }
  };

  const drainFrames = () => {
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const chunk = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      let event = "";
      const dataLines: string[] = [];
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7).trim();
        else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
      }
      if (event) handle(event, dataLines.join("\n"));
      boundary = buffer.indexOf("\n\n");
    }
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (value) {
        buffer += decoder.decode(value, { stream: true });
        drainFrames();
      }
      if (done) break;
    }
  } catch {
    // A read that throws is the same failure as a stream that stops early.
  }

  if (settled) return settled;
  return {
    content: failure ?? TRUNCATED_STREAM_MESSAGE,
    proposal: null,
    toolEvents: streamedTools,
    model: null,
  };
}

/**
 * Like {@link requestAssistantReply} but preserves the structured order
 * proposal (F7). The agentic loop returns a `place_order` proposal instead of
 * executing it; ChatPanel renders the proposal as a confirm card. The order is
 * NEVER executed here.
 */
export async function requestAssistantTurn(
  history: ApiMessage[],
  latestMessage: string,
  attachments: ChatImageAttachment[] = [],
  /** Catalog model id from the composer's picker. "" leaves the choice to the server. */
  model = "",
  /** Live progress while the turn is still open — flips the panel to alive. */
  onEvent?: (event: AssistantStreamEvent) => void,
): Promise<AssistantTurn> {
  const response = await fetch("/api/assistant", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({
      messages: [...history, buildUserMessage(latestMessage, attachments)],
      // Omitted rather than sent empty: the route treats an absent model as
      // "unchanged behavior", and validates any id it does receive.
      ...(model ? { model } : {}),
    }),
  });

  // Every rejection that carries a status is written before the stream opens,
  // so a non-2xx is still a JSON body.
  if (!response.ok) {
    const failed = await readJsonBody<AssistantResponse>(response);
    const message = assistantErrorMessage(response.status, failed?.error);
    return { content: message, proposal: null, toolEvents: [], model: null };
  }

  if (response.headers?.get?.("content-type")?.includes("text/event-stream") && response.body) {
    return readAssistantStream(response.body, latestMessage, onEvent);
  }

  const payload = await readJsonBody<AssistantResponse>(response);

  const content =
    typeof payload?.content === "string" && payload.content.trim()
      ? formatAssistantPayload(payload.content)
      : fallbackReply(latestMessage);

  return {
    content,
    proposal: payload?.proposal ?? null,
    toolEvents: Array.isArray(payload?.toolEvents) ? payload.toolEvents : [],
    model: typeof payload?.model === "string" ? payload.model : null,
  };
}

/**
 * Executes a confirmed order proposal through the live placement path. Maps the
 * proposal's `place_order` input to the `/api/orders/place` body shape. Called
 * ONLY from the ChatPanel confirm card's Confirm action — never automatically.
 */
export async function placeProposedOrder(
  proposal: AssistantOrderProposal,
): Promise<{ ok: boolean; message: string }> {
  const input = proposal.input;
  const body: Record<string, unknown> = {
    type: input.type,
    symbol: input.ticker,
    action: input.type === "combo" ? "BUY" : input.action,
    quantity: input.quantity,
    tif: "DAY",
    limitPrice: input.limit_price,
    idempotencyKey: crypto.randomUUID(),
  };
  if (input.type === "option") {
    Object.assign(body, {
      expiry: input.expiry.replace(/-/g, ""),
      strike: input.strike,
      right: input.right,
      conId: input.conId,
      exchange: input.exchange,
    });
  }
  if (input.type === "combo") {
    body.legs = input.legs.map((leg) => ({
      expiry: leg.expiry.replace(/-/g, ""),
      strike: leg.strike,
      right: leg.right,
      action: leg.action,
      ratio: leg.ratio,
    }));
  }

  const response = await fetch("/api/orders/place", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const json = await response.json().catch(() => ({}));
  if (!response.ok) {
    return { ok: false, message: (json as { error?: string }).error || "Order placement failed." };
  }
  const feedback = placeOrderFeedback(
    json,
    (json as { message?: string }).message || `Order placed: ${proposal.summary}`,
  );
  return { ok: true, message: feedback.message };
}

export async function requestPiReply(command: string): Promise<string> {
  const response = await fetch("/api/pi", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ input: command }),
  });

  const payload = await readJsonBody<PiResponse>(response);
  const normalized = normalizeTextLines(payload?.output || "");
  const canonicalCommand = command.trim().replace(/^\//, "").split(/\s+/)[0] ?? "";

  if (!response.ok) {
    if (payload?.error) {
      return `Error: ${payload.error}`;
    }
    return "PI command request failed.";
  }

  if (!payload) {
    return "No output returned from PI command.";
  }

  if (payload.status === "error") {
    const details = payload.stderr ? `\n\nDetails:\n${payload.stderr}` : "";
    return `Command '${payload.command}' failed: ${normalized}${details}`;
  }

  if (!normalized) {
    return "No output returned from PI command.";
  }

  return formatPiPayload(canonicalCommand, normalized);
}

/**
 * Chunks a reply is allowed to type out. At 8ms a chunk this is the ceiling on
 * how long a turn can hold the panel: a 400 KB reply is ~3,300 chunks and ~27
 * seconds of forced typing with no way to stop it. Past the cap the remainder
 * lands in ONE write, so the full text always renders — only the animation is
 * bounded. R-312.
 */
export const MAX_STREAM_CHUNKS = 240;

export async function streamMessage(
  messageId: string,
  fullText: string,
  setMessages: Dispatch<SetStateAction<Message[]>>,
  options: { signal?: AbortSignal } = {},
) {
  const { signal } = options;
  const chunk = 120;
  let rendered = "";
  const source = fullText.length ? fullText : "No output returned from PI command.";
  const parts = source.match(new RegExp(`.{1,${chunk}}`, "gs"));

  const write = (content: string) =>
    setMessages((current) =>
      current.map((message) => (message.id === messageId ? { ...message, content } : message)),
    );

  if (!parts) {
    write(source);
    return;
  }

  // Everything past the cap is written in one go rather than dropped.
  const animated = parts.slice(0, MAX_STREAM_CHUNKS);
  const remainder = parts.slice(MAX_STREAM_CHUNKS).join("");

  for (const piece of animated) {
    // An unmounted panel must stop calling setMessages, not run to completion.
    if (signal?.aborted) return;
    rendered += piece;
    write(rendered);
    await sleep(8);
  }

  if (signal?.aborted) return;
  if (remainder) write(rendered + remainder);
}

export function resolveSectionFromPath(pathname: string | null, fallback: WorkspaceSection): WorkspaceSection {
  if (!pathname) {
    return fallback;
  }

  if (pathname === "/" || pathname === "/dashboard") {
    return "dashboard";
  }

  if (pathname.startsWith("/flow-analysis")) {
    return "flow-analysis";
  }

  if (pathname.startsWith("/options")) {
    return "options";
  }

  if (pathname.startsWith("/portfolio")) {
    return "portfolio";
  }

  if (pathname.startsWith("/performance")) {
    return "performance";
  }

  if (pathname.startsWith("/orders")) {
    return "orders";
  }

  if (pathname.startsWith("/scanner")) {
    return "scanner";
  }

  if (pathname.startsWith("/discover")) {
    return "discover";
  }

  if (pathname.startsWith("/watchlist")) {
    return "watchlist";
  }

  if (pathname.startsWith("/journal")) {
    return "journal";
  }

  if (pathname.startsWith("/regime")) {
    return "regime";
  }

  if (pathname.startsWith("/alerts")) {
    return "alerts";
  }

  if (pathname.startsWith("/workflow")) {
    return "workflow";
  }

  if (pathname.startsWith("/admin")) {
    return "admin";
  }

  if (pathname.startsWith("/preferences")) {
    return "preferences";
  }

  if (pathname.startsWith("/profile")) {
    return "profile";
  }

  // Dynamic ticker route: /AAPL, /GOOG, etc. (1-5 alpha chars)
  if (/^\/[A-Za-z]{1,5}$/.test(pathname)) {
    return "ticker-detail";
  }

  return fallback;
}
