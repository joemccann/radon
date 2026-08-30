// Per-turn assistant telemetry — one Turso `assistant_turns` row per
// /api/assistant turn (migration 0030). Fire-and-forget by contract: the
// route never awaits this and it never throws, so a Turso outage can never
// fail or delay an assistant reply. Write failures surface through the
// dbExecute chokepoint's own `[dbExecute:assistant-turns]` warn line.

import { dbExecute } from "@/lib/dbExecute";

const USER_MSG_MAX_CHARS = 200;
const WRITE_TIMEOUT_MS = 2_500;

export type AssistantTurnToolCall = {
  name: string;
  ok: boolean;
  repeated?: boolean;
  error?: string;
};

export type AssistantTurnRecord = {
  ts: string;
  userMsg: string;
  rounds: number;
  toolCalls: AssistantTurnToolCall[];
  usage?: { inputTokens: number; outputTokens: number };
  outcome: string;
  /** Image blocks forwarded to the provider this turn (R-454, migration 0063). */
  imageCount?: number;
  /** Provider / model that actually answered, not the one requested. */
  provider?: string | null;
  model?: string | null;
};

export function recordAssistantTurn(record: AssistantTurnRecord): void {
  try {
    void dbExecute(
      {
        sql: "INSERT INTO assistant_turns (ts, user_msg, rounds, tool_calls, usage, outcome, image_count, provider, model) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        args: [
          record.ts,
          record.userMsg.slice(0, USER_MSG_MAX_CHARS),
          record.rounds,
          JSON.stringify(record.toolCalls),
          record.usage ? JSON.stringify(record.usage) : null,
          record.outcome,
          record.imageCount ?? 0,
          record.provider ?? null,
          record.model ?? null,
        ],
      },
      { timeoutMs: WRITE_TIMEOUT_MS, label: "assistant-turns" },
    ).catch(() => {});
  } catch {
    // Even a synchronous throw (unlikely) must not reach the route.
  }
}
