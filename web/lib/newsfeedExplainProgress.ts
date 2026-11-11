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

export function encodeExplainEvent(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}
