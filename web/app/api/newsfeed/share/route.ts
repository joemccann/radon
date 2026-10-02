import { requireRouteAccess } from "@/lib/routeAccess";
import { voiceInput, type NewsfeedVoiceInput } from "@/lib/newsfeedVoice";
import { encodeVoiceEvent, VOICE_REWRITE_BUDGET_MS, type VoiceStage } from "@/lib/newsfeedVoiceProgress";
import { rewriteInVoice } from "@/lib/newsfeedVoiceRewrite";

export const runtime = "nodejs";
export const radonCapability = "internal";

const STREAM_HEADERS = {
  "Content-Type": "text/event-stream; charset=utf-8",
  "Cache-Control": "no-store",
  "X-Accel-Buffering": "no",
};

function json(body: unknown, status = 200): Response {
  return Response.json(body, { status, headers: { "Cache-Control": "no-store" } });
}

function failureEvent(error: unknown, timeout: AbortSignal): { error: string; status: number } {
  if (timeout.aborted || (error instanceof Error && error.name === "TimeoutError")) {
    return { error: "Rewrite timed out. Try again.", status: 504 };
  }
  console.error(
    `[newsfeed/share] voice rewrite failed: ${
      error instanceof Error ? `${error.name}: ${error.message.slice(0, 300)}` : String(error)
    }`,
  );
  return { error: "Could not generate a verified draft. Try again.", status: 502 };
}

/**
 * The rewrite can outlast the edge's 30s response-header guard, so the header
 * and a first stage frame go out before any model call and the draft arrives
 * as the final `result` (or `error`) frame.
 */
function streamRewrite(request: Request, input: NewsfeedVoiceInput): Response {
  const timeout = AbortSignal.timeout(VOICE_REWRITE_BUDGET_MS);
  const disconnect = new AbortController();
  const signal = AbortSignal.any([request.signal, disconnect.signal, timeout]);
  const encoder = new TextEncoder();
  const clientGone = () => request.signal.aborted || disconnect.signal.aborted;
  const body = new ReadableStream<Uint8Array>({
    async start(controller) {
      const send = (event: string, data: unknown) => {
        if (!clientGone()) controller.enqueue(encoder.encode(encodeVoiceEvent(event, data)));
      };
      send("stage", { stage: "queued", budgetMs: VOICE_REWRITE_BUDGET_MS });
      try {
        const copy = await rewriteInVoice(input, { signal, onStage: (stage: VoiceStage) => send("stage", { stage }) });
        send("result", copy);
      } catch (error) {
        if (!clientGone()) send("error", failureEvent(error, timeout));
      } finally {
        if (!disconnect.signal.aborted) controller.close();
      }
    },
    cancel() { disconnect.abort(); },
  });
  return new Response(body, { headers: STREAM_HEADERS });
}

export async function POST(request: Request): Promise<Response> {
  const access = await requireRouteAccess(request, {
    operatorOnly: true,
    rate: { key: "newsfeed-share", limit: 10, windowMs: 60_000 },
    durableRateTier: "D",
  });
  if (!access.ok) return access.response;
  let input;
  try {
    const raw = await request.text();
    if (raw.length > 80_000) return json({ error: "News item is too long." }, 400);
    input = voiceInput(JSON.parse(raw));
  } catch { return json({ error: "Provide a valid news title and content." }, 400); }
  if (!input) return json({ error: "Provide a news title and content within the length limits." }, 400);
  return streamRewrite(request, input);
}
