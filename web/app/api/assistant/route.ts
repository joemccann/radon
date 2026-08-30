import { requireRouteAccess } from "@/lib/routeAccess";

import { NextRequest, NextResponse } from "next/server";

import { runAssistantLoop, type AssistantModelSelection, type AssistantTurn, type ToolEvent } from "@/lib/assistant/loop";
import { recordAssistantTurn, type AssistantTurnToolCall } from "@/lib/assistant/telemetry";
import { enforceDemoAiQuota } from "@/lib/demo/enforceAiQuota";
import { etCalendarDateString } from "@/lib/journal/rangePnl";
import { validateModelId } from "@/lib/llm/catalog";

type ChatRole = "user" | "assistant";

export type ChatImageMediaType = "image/png" | "image/jpeg" | "image/gif" | "image/webp";

export type ChatTextBlock = { type: "text"; text: string };

export type ChatImageBlock = {
  type: "image";
  source: { type: "base64"; media_type: ChatImageMediaType; data: string };
};

export type ChatContentBlock = ChatTextBlock | ChatImageBlock;

export type ChatMessage = {
  role: ChatRole;
  content: string | ChatContentBlock[];
};

export type AssistantPayload = {
  messages: ChatMessage[];
  /** Model id from the picker. Advisory: only a catalogued id is honored. */
  model?: string;
};

export const maxDuration = 300;

export const SYSTEM_PROMPT =
  "You are Grok, running as Radon's trading operations assistant. " +
  "You are an API client of the same HTTP APIs the operator UI uses. " +
  "Use list_apis to find the path, then call_api to invoke it. Do not guess paths. " +
  "Watchlist is GET/POST /api/watchlist and DELETE /api/watchlist/{symbol}. " +
  "You analyze institutional flow, portfolio risk, and trade structure with the same direct style as Grok. " +
  "You can call named tools to pull live flow, scans, gamma exposure, the portfolio, quotes, priced option chains, ranked verticals, the 7-milestone evaluate pipeline, other FastAPI READ surfaces via fetch_backend or call_api, and the trade journal (query_journal for raw fills, get_realized_pnl for realized P&L). " +
  "Destructive actions (placing orders) are never executed automatically: propose them and let the operator confirm. " +
  "Always respond in short, decisive blocks using signal, structure, kelly logic, and final decision. " +
  "If confidence is low, explicitly state uncertainty and recommend the next command or additional data. " +
  "LIVE MARKET: before naming strikes or a debit, call get_quote and either rank_spreads or get_option_chain. Never invent a spot price. " +
  "For exact verticals use rank_spreads (it uses live mids and flags convexity: gain >= 2x loss). " +
  "For a full thesis call run_evaluate. For other backend services (earnings, VCG, short availability, ratings, open orders) call list_apis then call_api. " +
  "Before forming a new thesis, consult search_knowledge and find_prior_evals for prior theses, evals, incidents, and lessons, and cite the doc_keys you relied on in your answer. " +
  "A knowledge miss or timeout is not a reason to skip live market tools. Continue with quote, chain, flow, and evaluate. " +
  "For questions about trade history, fills, or profit and loss, go straight to the journal tools (get_realized_pnl, query_journal); the knowledge base does not carry P&L figures and cannot enumerate fills. " +
  "If a knowledge tool fails or returns no thesis documents, say so plainly in your answer and never fabricate prior theses, lessons, or sizing history. " +
  "JOURNAL CONVENTIONS: The trade journal contains two row families for the same executions: Flex-rehydrate aggregate rows (family flex_agg, composite exec ids, carrying realized_pnl / cost_basis / proceeds / open_basis) and realtime per-fill rows (family fill). " +
  "The same fill can appear in BOTH families; never sum across families without deduping, and rows marked dup:true duplicate an aggregate. " +
  "Closes lot-match against opens by VWAP (per-unit basis = open_basis / quantity), and the matching open may lie OUTSIDE the date window you queried: a close early in a week can realize P&L against an open from a prior week. " +
  "For realized P&L questions prefer get_realized_pnl: it dedupes and lot-matches for you, attributes P&L to the close date, and handles cross-window opens. Use query_journal only to inspect raw rows. " +
  "The operator may attach chart or screenshot images to a message: read every attached image as part of that request.";

const isMockMode = () =>
  process.env.ASSISTANT_MOCK === "1" ||
  (process.env.NODE_ENV === "test" && process.env.ASSISTANT_MOCK !== "0");

function cleanString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

// Trust boundary for pasted images. Anything failing a limit is dropped
// silently — never thrown, never forwarded unvalidated.
const IMAGE_MEDIA_TYPES: readonly string[] = ["image/png", "image/jpeg", "image/gif", "image/webp"];
const MAX_IMAGES_PER_MESSAGE = 4;
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const BASE64_PATTERN = /^[A-Za-z0-9+/]+={0,2}$/;

function decodedBase64Bytes(data: string): number {
  return Math.floor((data.length * 3) / 4);
}

function safeImageBlock(block: { source?: unknown }): ChatImageBlock | null {
  const source = block.source as { type?: unknown; media_type?: unknown; data?: unknown } | undefined;
  if (!source || source.type !== "base64") return null;

  const mediaType = source.media_type;
  if (typeof mediaType !== "string" || !IMAGE_MEDIA_TYPES.includes(mediaType)) return null;

  const data = source.data;
  if (typeof data !== "string" || !BASE64_PATTERN.test(data)) return null;
  if (decodedBase64Bytes(data) > MAX_IMAGE_BYTES) return null;

  return {
    type: "image",
    source: { type: "base64", media_type: mediaType as ChatImageMediaType, data },
  };
}

function safeContentBlocks(rawContent: unknown[]): ChatContentBlock[] {
  const blocks: ChatContentBlock[] = [];
  let images = 0;
  for (const raw of rawContent) {
    const block = raw as { type?: unknown; text?: unknown; source?: unknown };
    if (block?.type === "text") {
      const text = cleanString(block.text);
      if (text) blocks.push({ type: "text", text });
      continue;
    }
    if (block?.type === "image") {
      if (images >= MAX_IMAGES_PER_MESSAGE) continue;
      const image = safeImageBlock(block);
      if (image) {
        blocks.push(image);
        images += 1;
      }
    }
  }
  return blocks;
}

function safeMessages(rawMessages: unknown): ChatMessage[] {
  if (!Array.isArray(rawMessages)) return [];

  const parsed: ChatMessage[] = [];
  for (const item of rawMessages) {
    const role = (item as { role?: unknown }).role;
    const content = (item as { content?: unknown }).content;
    if (role !== "user" && role !== "assistant") continue;

    if (typeof content === "string") {
      if (content.trim()) parsed.push({ role, content: content.trim() });
      continue;
    }
    if (Array.isArray(content)) {
      const blocks = safeContentBlocks(content);
      if (blocks.length) parsed.push({ role, content: blocks });
    }
  }
  return parsed;
}

function fallbackReply(input: string): string {
  const q = cleanString(input).toLowerCase();
  if (!q) return "Awaiting your instruction. Ask for ticker analysis, a scan review, or risk checks.";
  if (q.includes("help")) {
    return "Try: analyze [TICKER], compare support vs against, action items, watch list, or run scan/portfolio workflows.";
  }
  if (q.includes("brze")) {
    return "BRZE is currently against-flow. It is a near-expiry long call structure with distribution-heavy prints; this is higher urgency than neutral flow mismatches.";
  }
  if (q.includes("analyze rr") || q === "rr") {
    return "RR is against flow and currently shows sustained distribution, so position risk is elevated unless thesis data sharpens.";
  }
  return "I have flow context loaded for the dashboard and can expand on any ticker, structure, or command-style request.";
}

// The provider's offline mock prefixes completions with "Mock <provider>".
// When the loop runs under ASSISTANT_MOCK with the real (unmocked) provider, we
// rewrite that placeholder into the assistant's deterministic fallback so the
// dashboard mock stays useful and stable.
function isProviderMockContent(content: string): boolean {
  return /^Mock (anthropic|openai|gemini|xai|grok) response:/i.test(content);
}

function toTurns(messages: ChatMessage[]): AssistantTurn[] {
  return messages.map((message) => ({ role: message.role, content: message.content }));
}

// Text only: the fallback reply and telemetry both want a readable string, and
// an image block carries nothing readable.
function contentText(content: ChatMessage["content"]): string {
  if (typeof content === "string") return content;
  return content
    .filter((block): block is ChatTextBlock => block.type === "text")
    .map((block) => block.text)
    .join(" ");
}

function lastUserContent(messages: ChatMessage[]): string {
  const message = [...messages].reverse().find((entry) => entry.role === "user");
  return message ? contentText(message.content) : "";
}

// A client-supplied model is never trusted: only an id the catalog knows is
// forwarded, so a caller cannot bill an arbitrary model string. Anything else
// (unknown id, or a catalog that is down) silently falls back to the
// deployment default rather than failing the operator's turn.
async function resolveModelSelection(raw: unknown): Promise<AssistantModelSelection> {
  const requested = cleanString(raw);
  if (!requested) return {};
  try {
    const model = await validateModelId(requested);
    return model ? { model: model.id, provider: model.provider } : {};
  } catch {
    return {};
  }
}

function toTelemetryToolCalls(toolEvents: ToolEvent[]): AssistantTurnToolCall[] {
  return toolEvents.map((event) => ({
    name: event.name,
    ok: event.ok,
    ...(event.repeated ? { repeated: true } : {}),
    ...(event.error ? { error: event.error.slice(0, 200) } : {}),
  }));
}

export const radonCapability = "internal";

/**
 * Gap between `heartbeat` frames while the loop works. Nothing downstream
 * needs the payload; the point is that the connection is never idle long
 * enough for an intermediary to reclaim it.
 */
export const ASSISTANT_HEARTBEAT_MS = 10_000;

type SseEvent = "start" | "heartbeat" | "tool" | "done" | "error";

function sseFrame(event: SseEvent, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

export async function POST(request: NextRequest): Promise<Response> {
  const access = await requireRouteAccess(request, {
    rate: { key: "assistant:route", limit: 10, windowMs: 60_000 },
    durableRateTier: "D",
  });
  if (!access.ok) return access.response;
  const mock = isMockMode();

  let body: AssistantPayload | null = null;
  try {
    body = (await request.json()) as AssistantPayload;
  } catch {
    if (mock) return NextResponse.json({ error: "No messages supplied." }, { status: 400 });
    return NextResponse.json({ error: "Invalid JSON payload." }, { status: 400 });
  }

  const messages = safeMessages(body?.messages);
  if (!messages.length) {
    return NextResponse.json({ error: "No messages supplied." }, { status: 400 });
  }

  const lastMessage = messages[messages.length - 1];
  if (!mock && lastMessage.role !== "user") {
    return NextResponse.json({ error: "The last message must be from user." }, { status: 400 });
  }

  // Demo AI quota (Phase 4) — no-op for non-demo users; 429 once a trial user
  // exhausts the daily assistant budget, BEFORE the expensive LLM loop runs.
  const quota = await enforceDemoAiQuota("assistant");
  if (quota) return quota;

  // ── Everything above this line still carries a real HTTP status. ──────
  //
  // Below it the response header is already on the wire, so a failure can
  // only be an `error` FRAME on a 200. Nothing that needs 400/401/429 may
  // move down here. R-262.

  const t0 = Date.now();
  const encoder = new TextEncoder();
  let heartbeat: ReturnType<typeof setInterval> | null = null;

  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      // Enqueue is best-effort from here on: the operator can close the tab
      // mid-turn, and a dead controller must not take the loop down with it.
      const send = (event: SseEvent, data: unknown) => {
        try {
          controller.enqueue(encoder.encode(sseFrame(event, data)));
        } catch {
          /* client hung up */
        }
      };

      // THE FIX. 2026-08-29: the route ran the whole multi-round loop and
      // wrote nothing until it finished, so Caddy's header guard abandoned a
      // still-running turn and the operator got a 504. This frame — written
      // synchronously, before anything is awaited — is what makes the header
      // exist no matter how long the turn takes.
      send("start", { ts: new Date().toISOString() });
      heartbeat = setInterval(
        () => send("heartbeat", { ms: Date.now() - t0 }),
        ASSISTANT_HEARTBEAT_MS,
      );

      const finish = () => {
        if (heartbeat) clearInterval(heartbeat);
        heartbeat = null;
        try {
          controller.close();
        } catch {
          /* already closed */
        }
      };

      // Deliberately NOT awaited: `start` has to return so the header flushes.
      void (async () => {
        try {
          const system = `${SYSTEM_PROMPT} Today is ${etCalendarDateString(new Date())} (America/New_York).`;
          const selection = await resolveModelSelection(body?.model);
          const result = await runAssistantLoop(
            toTurns(messages),
            system,
            access.principal,
            selection,
            (event) => send("tool", event),
          );

          const content =
            mock && !result.proposal && isProviderMockContent(result.content)
              ? `Mock Grok response: ${fallbackReply(lastUserContent(messages))}`
              : result.content;

          const outcome = result.proposal ? "proposal" : result.outcome;
          console.log(
            `[assistant] done rounds=${result.rounds} outcome=${outcome} toolCalls=${result.toolEvents.length} usageIn=${result.usage.inputTokens} usageOut=${result.usage.outputTokens} ms=${Date.now() - t0}`,
          );
          recordAssistantTurn({
            ts: new Date().toISOString(),
            userMsg: lastUserContent(messages),
            rounds: result.rounds,
            toolCalls: toTelemetryToolCalls(result.toolEvents),
            usage: result.usage,
            outcome,
          });

          send("done", {
            content,
            model: result.model,
            toolEvents: result.toolEvents,
            proposal: result.proposal ?? null,
            rounds: result.rounds,
          });
        } catch (error) {
          const message = error instanceof Error ? error.message : "Unknown assistant error.";
          console.log(
            `[assistant] done rounds=0 outcome=error toolCalls=0 usageIn=0 usageOut=0 ms=${Date.now() - t0}`,
          );
          recordAssistantTurn({
            ts: new Date().toISOString(),
            userMsg: lastUserContent(messages),
            rounds: 0,
            toolCalls: [],
            outcome: "error",
          });
          send("error", { error: message });
        } finally {
          finish();
        }
      })();
    },
    cancel() {
      if (heartbeat) clearInterval(heartbeat);
      heartbeat = null;
    },
  });

  return new Response(stream, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      // `no-transform` keeps an intermediary from rewriting or compressing
      // the body, which is another way of saying buffering it.
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      // nginx-family proxies honour this; Caddy uses flush_interval instead
      // (cloud/caddy/Caddyfile), and neither costs anything to state.
      "X-Accel-Buffering": "no",
    },
  });
}
