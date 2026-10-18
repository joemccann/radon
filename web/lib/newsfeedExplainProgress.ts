export type ExplainStage = "queued" | "loading" | "drafting" | "checking";
export const EXPLAIN_BUDGET_MS = 90_000;
export function encodeExplainEvent(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}
