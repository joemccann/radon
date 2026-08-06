/**
 * Pool-destroy observability (2026-08-06 incident).
 *
 * Intermittent UND_ERR_DESTROYED bursts aborted user requests (503s on
 * /orders) with NO logged trigger: destroyPoolAgent, the reset-arming
 * attempt, and the selfHealing stall ceiling were all silent, so journald
 * showed only collateral. Every teardown-path event must log enough to
 * attribute the trigger: the dbExecute label, operation id, and pool
 * generations involved.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { dbExecute } from "@/lib/dbExecute";
import { __resetDbForTests } from "@/lib/db";

vi.mock("@libsql/client", () => {
  const state = { impl: async (_stmt: unknown): Promise<unknown> => ({ rows: [] }) };
  return {
    createClient: () => ({
      execute: (stmt: unknown) => state.impl(stmt),
      batch: (stmts: unknown) => state.impl(stmts),
    }),
    __state: state,
  };
});

async function libsqlState() {
  const mod = (await import("@libsql/client")) as unknown as {
    __state: { impl: (stmt: unknown) => Promise<unknown> };
  };
  return mod.__state;
}

describe("pool teardown observability", () => {
  let warnSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    process.env.TURSO_DB_URL = "libsql://observability-test.turso.io";
    process.env.TURSO_AUTH_TOKEN = "test-token";
    __resetDbForTests();
    warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    warnSpy.mockRestore();
    __resetDbForTests();
  });

  it("a failure that arms or fires the destroy path logs the trigger label", async () => {
    const state = await libsqlState();
    state.impl = async () => {
      throw new Error("boom");
    };

    await expect(
      dbExecute("SELECT 1", { timeoutMs: 50, label: "watchlist" }),
    ).rejects.toThrow();

    const teardownLines = warnSpy.mock.calls
      .map((call) => call.map(String).join(" "))
      .filter((line) => line.includes("[db] pool"));
    expect(teardownLines.length).toBeGreaterThan(0);
    expect(teardownLines.join(" ")).toContain("watchlist");
  });

  it("an actual destroy logs with generation detail", async () => {
    const state = await libsqlState();
    state.impl = async () => {
      throw new Error("boom");
    };

    // Two distinct failed operations inside the cluster window arm then fire.
    await expect(
      dbExecute("SELECT 1", { timeoutMs: 50, label: "profile" }),
    ).rejects.toThrow();
    await expect(
      dbExecute("SELECT 1", { timeoutMs: 50, label: "service_health" }),
    ).rejects.toThrow();

    const destroyLines = warnSpy.mock.calls
      .map((call) => call.map(String).join(" "))
      .filter((line) => line.includes("[db] pool destroy"));
    expect(destroyLines.length).toBeGreaterThan(0);
    expect(destroyLines.join(" ")).toContain("service_health");
  });
});
