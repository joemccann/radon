/**
 * @vitest-environment node
 *
 * The demo/prod isolation guard must hold at `getDb()` itself, not only in
 * `dbExecute`: routes that take `const db = getDb(); db.execute(...)` never
 * pass through `dbExecute`. A demo-scoped caller (demo deployment flag or a
 * demo-principal execution scope) must not obtain a client for a prod-marked
 * TURSO_DB_URL, cached or fresh.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const createClientMock = vi.hoisted(() =>
  vi.fn(() => ({ execute: vi.fn(), close: vi.fn() })),
);

vi.mock("@libsql/client", () => ({ createClient: createClientMock }));

const PROD_URL = "libsql://radon-joemccann.aws-us-west-2.turso.io";
const DEMO_URL = "libsql://radon-demo-joemccann.aws-us-west-2.turso.io";

describe("getDb enforces demo/prod DB isolation", () => {
  beforeEach(() => {
    vi.resetModules();
    createClientMock.mockClear();
    vi.stubEnv("TURSO_AUTH_TOKEN", "token");
    vi.stubEnv("RADON_DB_USE_REPLICA", "");
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("refuses a demo-principal scope against the prod marker", async () => {
    vi.stubEnv("TURSO_DB_URL", PROD_URL);
    const { runWithDemoDbPrincipal } = await import("@/lib/demo/demoDbIsolation");
    const { getDb } = await import("@/lib/db");
    expect(() => runWithDemoDbPrincipal(() => getDb())).toThrow(/prod marker/);
    expect(createClientMock).not.toHaveBeenCalled();
  });

  it("refuses a demo-principal scope even after an operator call cached the client", async () => {
    vi.stubEnv("TURSO_DB_URL", PROD_URL);
    const { runWithDemoDbPrincipal } = await import("@/lib/demo/demoDbIsolation");
    const { getDb } = await import("@/lib/db");
    expect(getDb()).toBeTruthy();
    expect(() => runWithDemoDbPrincipal(() => getDb())).toThrow(/prod marker/);
  });

  it("refuses on a demo deployment against the prod marker", async () => {
    vi.stubEnv("TURSO_DB_URL", PROD_URL);
    vi.stubEnv("NEXT_PUBLIC_RADON_DEMO", "1");
    const { getDb } = await import("@/lib/db");
    expect(() => getDb()).toThrow(/prod marker/);
    expect(createClientMock).not.toHaveBeenCalled();
  });

  it("serves a demo-scoped caller from the demo DB", async () => {
    vi.stubEnv("TURSO_DB_URL", DEMO_URL);
    const { runWithDemoDbPrincipal } = await import("@/lib/demo/demoDbIsolation");
    const { getDb } = await import("@/lib/db");
    expect(runWithDemoDbPrincipal(() => getDb())).toBeTruthy();
    expect(createClientMock).toHaveBeenCalledTimes(1);
  });

  it("serves the operator from the prod DB", async () => {
    vi.stubEnv("TURSO_DB_URL", PROD_URL);
    const { getDb } = await import("@/lib/db");
    expect(getDb()).toBeTruthy();
    expect(createClientMock).toHaveBeenCalledTimes(1);
  });
});
