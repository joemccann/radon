import { scrubSecrets } from "@/lib/apiContracts";
import { requireRouteAccess } from "@/lib/routeAccess";
import { enforceDemoAiQuota } from "@/lib/demo/enforceAiQuota";
import { explainInput, type NewsfeedExplainInput } from "@/lib/newsfeedExplain";
import { encodeExplainEvent, EXPLAIN_BUDGET_MS, type ExplainStage } from "@/lib/newsfeedExplainProgress";
import { explainNewsfeedPost } from "@/lib/newsfeedExplainGenerate";

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
    return { error: "Explanation timed out. Try again.", status: 504 };
  }
  const detail = error instanceof Error ? `${error.name}: ${error.message.slice(0, 300)}` : String(error);
  console.error(scrubSecrets(`[newsfeed/explain] failed: ${detail}`));
  return { error: "Could not explain this post. Try again.", status: 502 };
}

function streamExplain(
  request: Request,
  input: NewsfeedExplainInput,
  token?: string,
): Response {
  const timeout = AbortSignal.timeout(EXPLAIN_BUDGET_MS);
  const disconnect = new AbortController();
  const signal = AbortSignal.any([request.signal, disconnect.signal, timeout]);
  const encoder = new TextEncoder();
  const clientGone = () => request.signal.aborted || disconnect.signal.aborted;
  const body = new ReadableStream<Uint8Array>({
    async start(controller) {
      const send = (event: string, data: unknown) => {
        if (!clientGone()) controller.enqueue(encoder.encode(encodeExplainEvent(event, data)));
      };
      send("stage", { stage: "queued", budgetMs: EXPLAIN_BUDGET_MS });
      try {
        const result = await explainNewsfeedPost(input, {
          signal,
          token,
          onStage: (stage: ExplainStage) => send("stage", { stage }),
        });
        send("result", result);
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
    operatorOnly: false,
    rate: { key: "newsfeed-explain", limit: 8, windowMs: 60_000 },
    durableRateTier: "D",
  });
  if (!access.ok) return access.response;
  let input;
  try {
    const raw = await request.text();
    if (raw.length > 80_000) return json({ error: "News item is too long." }, 400);
    input = explainInput(JSON.parse(raw));
  } catch { return json({ error: "Provide a valid news title and content." }, 400); }
  if (!input) return json({ error: "Provide a news title and content within the length limits." }, 400);
  const quota = await enforceDemoAiQuota("newsfeed/explain");
  if (quota) return quota;
  return streamExplain(request, input, access.principal.token);
}
