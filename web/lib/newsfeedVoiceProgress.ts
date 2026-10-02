/**
 * Wire contract and progress model for the streamed newsfeed voice rewrite.
 * Shared by the route (which writes the events) and NewsfeedShare (which
 * reads them), so the stage vocabulary lives in one place.
 */

export type VoiceStage = "queued" | "drafting" | "hedging" | "checking";

/** Route wall clock for one rewrite. Headers stream immediately, so the edge's 30s header guard never applies. */
export const VOICE_REWRITE_BUDGET_MS = 90_000;
/** The browser gives the server its full budget plus time for the final frame. */
export const VOICE_REWRITE_CLIENT_TIMEOUT_MS = VOICE_REWRITE_BUDGET_MS + 10_000;

export const VOICE_STAGE_LABELS: Record<VoiceStage, string> = {
  queued: "Starting rewrite",
  drafting: "Drafting in your voice",
  hedging: "Still drafting. Trying a second model",
  checking: "Checking numbers against the source",
};

const QUEUED_PROGRESS = 0.04;
const DRAFT_FLOOR = 0.08;
const DRAFT_SPAN = 0.82;
const TYPICAL_DRAFT_MS = 12_000;
const CHECKING_PROGRESS = 0.95;

/**
 * Estimated completion in [0, 1). Drafting has no measurable progress, so it
 * eases toward 90% on a typical-duration curve and never claims done.
 */
export function voiceProgress(stage: VoiceStage, elapsedMs: number): number {
  if (stage === "queued") return QUEUED_PROGRESS;
  if (stage === "checking") return CHECKING_PROGRESS;
  return DRAFT_FLOOR + DRAFT_SPAN * (1 - Math.exp(-Math.max(0, elapsedMs) / TYPICAL_DRAFT_MS));
}

export function encodeVoiceEvent(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

type VoiceFrame = { event: string; data: unknown };

function decodeFrame(frame: string): VoiceFrame | null {
  const event = frame.match(/^event: (.+)$/m)?.[1];
  const data = frame.match(/^data: (.+)$/m)?.[1];
  return event && data ? { event, data: JSON.parse(data) } : null;
}

function isStreamed(response: Response): boolean {
  return Boolean(response.headers?.get("content-type")?.includes("text/event-stream"));
}

/** Resolves the result frame's payload; throws on an error frame or a stream that ends without one. */
async function readFrames(body: ReadableStream<Uint8Array>, onStage: (stage: VoiceStage) => void): Promise<unknown> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffered = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) throw new Error("Rewrite stream ended without a draft.");
    buffered += decoder.decode(value, { stream: true });
    const frames = buffered.split("\n\n");
    buffered = frames.pop() ?? "";
    for (const frame of frames.map(decodeFrame)) {
      if (frame?.event === "stage") onStage((frame.data as { stage: VoiceStage }).stage);
      if (frame?.event === "error") throw new Error((frame.data as { error?: string }).error ?? "Rewrite failed.");
      if (frame?.event === "result") { void reader.cancel(); return frame.data; }
    }
  }
}

/** Reads a rewrite response: the event stream, or a plain JSON body (pre-stream rejections, older servers). */
export async function readVoiceRewrite(response: Response, onStage: (stage: VoiceStage) => void): Promise<unknown> {
  if (!isStreamed(response) || !response.body) return response.json();
  return readFrames(response.body, onStage);
}
