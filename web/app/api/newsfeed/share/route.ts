import { requireRouteAccess } from "@/lib/routeAccess";
import { chat } from "@/lib/llm/provider";
import { NEWSFEED_VOICE_SYSTEM, parseVoiceCopy, voiceInput } from "@/lib/newsfeedVoice";

export const runtime = "nodejs";
export const radonCapability = "internal";

function json(body: unknown, status = 200): Response {
  return Response.json(body, { status, headers: { "Cache-Control": "no-store" } });
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

  const timeout = AbortSignal.timeout(25_000);
  const signal = AbortSignal.any([request.signal, timeout]);
  try {
    signal.throwIfAborted();
    const result = await chat({
      system: NEWSFEED_VOICE_SYSTEM,
      messages: [{ role: "user", content: JSON.stringify(input) }],
      maxTokens: 1600,
      signal,
    });
    signal.throwIfAborted();
    return json(parseVoiceCopy(result.text, input));
  } catch (error) {
    if (request.signal.aborted) return json({ error: "Rewrite cancelled." }, 499);
    if (timeout.aborted || (error instanceof Error && error.name === "TimeoutError")) {
      return json({ error: "Rewrite timed out. Try again." }, 504);
    }
    return json({ error: "Could not generate a verified draft. Try again." }, 502);
  }
}
