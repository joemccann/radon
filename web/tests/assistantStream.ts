/**
 * Test-side reader for the `/api/assistant` event stream.
 *
 * The route answers `text/event-stream` so the response header is flushed
 * before `runAssistantLoop` is awaited (R-262). Tests that used to call
 * `res.json()` read the terminal `done` frame through here instead.
 */

export type AssistantSseFrame = { event: string; data: unknown };

export function parseSseFrames(raw: string): AssistantSseFrame[] {
  const frames: AssistantSseFrame[] = [];
  for (const chunk of raw.split("\n\n")) {
    if (!chunk.trim()) continue;
    let event = "";
    const dataLines: string[] = [];
    for (const line of chunk.split("\n")) {
      if (line.startsWith("event: ")) event = line.slice(7).trim();
      else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
    }
    if (!event) continue;
    const data = dataLines.join("\n");
    let parsed: unknown = null;
    try {
      parsed = data ? JSON.parse(data) : null;
    } catch {
      parsed = data;
    }
    frames.push({ event, data: parsed });
  }
  return frames;
}

/** Every frame the turn wrote. Waits for the stream to close. */
export async function drainAssistantStream(res: Response): Promise<AssistantSseFrame[]> {
  return parseSseFrames(await res.text());
}

/**
 * The turn's terminal payload — the same object shape the route used to return
 * as JSON. Throws when the stream ended without one, because a turn that
 * silently produced nothing is the failure mode this contract exists to catch.
 */
export async function assistantDonePayload<T = Record<string, unknown>>(
  res: Response,
): Promise<T> {
  const frames = await drainAssistantStream(res);
  const done = frames.find((frame) => frame.event === "done");
  if (!done) {
    throw new Error(
      `assistant stream ended without a done frame: ${JSON.stringify(frames)}`,
    );
  }
  return done.data as T;
}
