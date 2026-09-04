/**
 * R-624 (P2, NF-10): `7f722259` hid the provider error from the user and from
 * every queryable surface at once. The only surviving record of the cause was
 * `console.error("[assistant] turn failed", detail)` — stdout/journald.
 * `recordAssistantTurn` wrote `outcome: "error"` but its INSERT column list
 * had no error column, so the CLASS of failure was persisted nowhere and turn
 * 1 and turn 200 of a sustained outage were indistinguishable.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";

const executed: Array<{ sql: string; args: unknown[] }> = [];

vi.mock("@/lib/dbExecute", () => ({
  dbExecute: (stmt: { sql: string; args: unknown[] }) => {
    executed.push(stmt);
    return Promise.resolve({ rows: [] });
  },
}));

describe("the failure class is persisted with the turn", () => {
  beforeEach(() => {
    executed.length = 0;
  });

  it("writes error_class when the turn failed", async () => {
    const { recordAssistantTurn } = await import("../lib/assistant/telemetry");
    recordAssistantTurn({
      ts: new Date().toISOString(),
      userMsg: "hi",
      rounds: 0,
      toolCalls: [],
      outcome: "error",
      errorClass: "ProviderOverloadedError",
    });
    expect(executed).toHaveLength(1);
    expect(executed[0].sql).toContain("error_class");
    expect(executed[0].args).toContain("ProviderOverloadedError");
  });

  it("writes null for a clean turn", async () => {
    const { recordAssistantTurn } = await import("../lib/assistant/telemetry");
    recordAssistantTurn({
      ts: new Date().toISOString(),
      userMsg: "hi",
      rounds: 1,
      toolCalls: [],
      outcome: "ok",
    });
    const args = executed[0].args as unknown[];
    expect(args[args.length - 1]).toBeNull();
  });

  it("the placeholder count still matches the column count", async () => {
    const { recordAssistantTurn } = await import("../lib/assistant/telemetry");
    recordAssistantTurn({
      ts: "t", userMsg: "m", rounds: 0, toolCalls: [], outcome: "ok",
    });
    const { sql, args } = executed[0];
    const columns = sql.slice(sql.indexOf("(") + 1, sql.indexOf(")")).split(",").length;
    const placeholders = (sql.match(/\?/g) || []).length;
    expect(placeholders).toBe(columns);
    expect(args).toHaveLength(columns);
  });
});

describe("the assistant route records the class it swallowed", () => {
  it("passes errorClass into recordAssistantTurn", async () => {
    const fs = await import("node:fs/promises");
    const src = await fs.readFile(
      new URL("../app/api/assistant/route.ts", import.meta.url),
      "utf8",
    );
    const body = src
      .split("\n")
      .filter((line) => !line.trim().startsWith("//"))
      .join("\n");
    const slice = body.slice(body.indexOf("[assistant] turn failed"));
    expect(slice).toContain("errorClass");
  });
});

describe("the migration adds the column", () => {
  it("ships 0070_assistant_turn_error_class.sql", async () => {
    const fs = await import("node:fs/promises");
    const sql = await fs.readFile(
      new URL("../../scripts/db/migrations/0070_assistant_turn_error_class.sql", import.meta.url),
      "utf8",
    );
    expect(sql).toContain("ADD COLUMN error_class TEXT");
    expect(sql).toContain("VALUES (70,");
  });
});
