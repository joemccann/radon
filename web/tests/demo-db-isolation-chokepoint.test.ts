/**
 * @vitest-environment node
 *
 * F20260917-C04/C08: the REL-245 demo/prod DB isolation guard was applied on
 * only two routes and keyed on the principal kind, so blotter/RSC reads and
 * the assistant's direct-Turso journal/portfolio tools skipped it. The guard
 * now lives inside the shared `dbExecute` chokepoint, keyed on the DB marker:
 * a demo-scoped caller (demo deployment flag or demo-principal execution
 * scope) executing against a prod-marked TURSO_DB_URL is refused before any
 * row leaves the database, on every caller.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  execute: vi.fn(async () => ({ rows: [], columns: [], rowsAffected: 0, lastInsertRowid: undefined })),
}));

vi.mock("@/lib/db", () => ({
  getDb: () => ({ execute: mocks.execute }),
  resetDb: vi.fn(),
  getPoolStats: () => null,
}));

const PROD_URL = "libsql://radon-joemccann.aws-us-west-2.turso.io";
const DEMO_URL = "libsql://radon-demo-joemccann.aws-us-west-2.turso.io";

describe("F20260917-C04: demo/prod isolation enforced inside dbExecute", () => {
  beforeEach(() => {
    vi.resetModules();
    mocks.execute.mockClear();
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("refuses a demo-principal-scoped execute against the prod DB marker", async () => {
    vi.stubEnv("TURSO_DB_URL", PROD_URL);
    const { runWithDemoDbPrincipal } = await import("@/lib/demo/demoDbIsolation");
    const { dbExecute } = await import("@/lib/dbExecute");
    await expect(
      runWithDemoDbPrincipal(() => dbExecute("SELECT 1", { label: "test" })),
    ).rejects.toThrow(/radon-joemccann/);
    expect(mocks.execute).not.toHaveBeenCalled();
  });

  it("refuses on a demo deployment (NEXT_PUBLIC_RADON_DEMO=1) against the prod marker", async () => {
    vi.stubEnv("TURSO_DB_URL", PROD_URL);
    vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "1");
    const { dbExecute } = await import("@/lib/dbExecute");
    await expect(dbExecute("SELECT 1", { label: "test" })).rejects.toThrow(/prod marker/);
    expect(mocks.execute).not.toHaveBeenCalled();
  });

  it("assistant journal tool inherits the guard for a demo principal", async () => {
    vi.stubEnv("TURSO_DB_URL", PROD_URL);
    vi.stubEnv("ASSISTANT_MOCK", "");
    const { executeTool } = await import("@/lib/assistant/tools");
    const result = await executeTool(
      "query_journal",
      { from: "2026-09-01", to: "2026-09-10" },
      { userId: "demo-user", kind: "demo" },
    );
    expect(result.ok).toBe(false);
    expect(mocks.execute).not.toHaveBeenCalled();
  });

  it("demo-scoped execute against the demo DB still works", async () => {
    vi.stubEnv("TURSO_DB_URL", DEMO_URL);
    const { runWithDemoDbPrincipal } = await import("@/lib/demo/demoDbIsolation");
    const { dbExecute } = await import("@/lib/dbExecute");
    await expect(
      runWithDemoDbPrincipal(() => dbExecute("SELECT 1", { label: "test" })),
    ).resolves.toBeTruthy();
    expect(mocks.execute).toHaveBeenCalledTimes(1);
  });

  it("operator (unscoped) execute against the prod DB still works", async () => {
    vi.stubEnv("TURSO_DB_URL", PROD_URL);
    const { dbExecute } = await import("@/lib/dbExecute");
    await expect(dbExecute("SELECT 1", { label: "test" })).resolves.toBeTruthy();
    expect(mocks.execute).toHaveBeenCalledTimes(1);
  });
});
