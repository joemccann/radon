/**
 * Wire contract and progress model for the streamed newsfeed explanation.
 * Shared by the route (which writes the events) and NewsfeedExplain (which
 * reads them), so the stage vocabulary lives in one place.
 */

export type ExplainStage = "queued" | "loading" | "drafting" | "checking";

/** Route wall clock for one explanation. Headers stream immediately. */
export const EXPLAIN_BUDGET_MS = 90_000;
/** The browser gives the server its full budget plus time for the final frame. */
export const EXPLAIN_CLIENT_TIMEOUT_MS = EXPLAIN_BUDGET_MS + 10_000;

export const EXPLAIN_STAGE_LABELS: Record<ExplainStage, string> = {
  queued: "Starting explanation",
  loading: "Loading the post",
  drafting: "Drafting a plain-English explanation",
  checking: "Checking the draft against the source",
};

export function encodeExplainEvent(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

type ExplainFrame = { event: string; data: unknown };

function decodeFrame(frame: string): ExplainFrame | null {
  const event = frame.match(/^event: (.+)$/m)?.[1];
  const data = frame.match(/^data: (.+)$/m)?.[1];
  return event && data ? { event, data: JSON.parse(data) } : null;
}

function isStreamed(response: Response): boolean {
  return Boolean(response.headers?.get("content-type")?.includes("text/event-stream"));
}

/** Resolves the result frame's payload; throws on an error frame or a stream that ends without one. */
async function readFrames(body: ReadableStream<Uint8Array>, onStage: (stage: ExplainStage) => void): Promise<unknown> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffered = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) throw new Error("Explanation stream ended without a result.");
    buffered += decoder.decode(value, { stream: true });
    const frames = buffered.split("\n\n");
    buffered = frames.pop() ?? "";
    for (const frame of frames.map(decodeFrame)) {
      if (frame?.event === "stage") onStage((frame.data as { stage: ExplainStage }).stage);
      if (frame?.event === "error") throw new Error((frame.data as { error?: string }).error ?? "Explanation failed.");
      if (frame?.event === "result") { void reader.cancel(); return frame.data; }
    }
  }
}

/** Reads an explain response: the event stream, or a plain JSON body (pre-stream rejections). */
export async function readExplainResult(response: Response, onStage: (stage: ExplainStage) => void): Promise<unknown> {
  if (!isStreamed(response) || !response.body) return response.json();
  return readFrames(response.body, onStage);
}
