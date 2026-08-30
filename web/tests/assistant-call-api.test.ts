import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createClient, type Client } from "@libsql/client";

/**
 * PR2: list_apis + call_api catalog client.
 *
 * Chat is a second client of the same HTTP APIs the UI uses. Adversarial
 * cases A1-A8, A15, A19 pin SSRF, order/admin refusal, Clerk-scoped
 * watchlist, spawn cap, truncation, and tool-count budget.
 */

const mocks = vi.hoisted(() => ({
  radonFetch: vi.fn(),
}));

vi.mock("@/lib/radonApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/radonApi")>();
  return {
    ...actual,
    radonFetch: mocks.radonFetch,
  };
});

let currentUserId: string | null = "user_test_1";
vi.mock("@clerk/nextjs/server", () => ({
  auth: vi.fn(async () => ({ userId: currentUserId })),
}));

let db: Client;
const mockGetDb = vi.fn(() => db);
vi.mock("@/lib/db", () => ({
  resetDb: () => {},
  getDb: mockGetDb,
}));

const PRINCIPAL = { userId: "user_test_1", token: "jwt-user-1" };

async function seedSchema(client: Client): Promise<void> {
  await client.execute(`CREATE TABLE user_watchlist (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, symbol TEXT NOT NULL,
    sector TEXT, added_at TEXT NOT NULL, UNIQUE(user_id, symbol))`);
}

async function execute(
  name: string,
  input: Record<string, unknown>,
  principal = PRINCIPAL,
  budget?: { spawnAttempts: number },
) {
  const { executeTool } = await import("@/lib/assistant/tools");
  return executeTool(name, input, principal, budget);
}

async function callApi(
  input: Record<string, unknown>,
  principal = PRINCIPAL,
  budget?: { spawnAttempts: number },
) {
  return execute("call_api", input, principal, budget);
}

function watchlistFrom(data: unknown): Array<{ symbol: string }> {
  const payload = data as {
    body?: { watchlist?: Array<{ symbol: string }> };
    watchlist?: Array<{ symbol: string }>;
  };
  return payload.body?.watchlist ?? payload.watchlist ?? [];
}

describe("assistant call_api catalog client", () => {
  beforeEach(async () => {
    vi.resetModules();
    mocks.radonFetch.mockReset();
    currentUserId = "user_test_1";
    db = createClient({ url: ":memory:" });
    await seedSchema(db);
    mockGetDb.mockReturnValue(db);
  });

  afterEach(() => {
    db.close();
    vi.restoreAllMocks();
  });

  it("A1 unknown /watchlist/add is refused and names the live watchlist routes", async () => {
    const result = await callApi({ path: "/watchlist/add" });
    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/GET \/api\/watchlist/);
    expect(result.error).toMatch(/POST \/api\/watchlist/);
    expect(result.error).toMatch(/DELETE \/api\/watchlist\/\{symbol\}/);
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });

  it.each([
    "http://169.254.169.254/",
    "//evil",
    "/quote/FOO/../orders/place",
    "/api/../admin",
  ])("A2 SSRF path %s never calls radonFetch", async (path) => {
    const result = await callApi({ method: "GET", path });
    expect(result.ok).toBe(false);
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });

  it.each(["/orders/place", "/api/orders/place"])(
    "A3 POST %s is refused with no fetch",
    async (path) => {
      const result = await callApi({ method: "POST", path });
      expect(result.ok).toBe(false);
      expect(mocks.radonFetch).not.toHaveBeenCalled();
    },
  );

  it("A4 FastAPI reads forward principal.token to radonFetch", async () => {
    mocks.radonFetch.mockResolvedValue({ ticker: "AAPL", last: 190 });
    const result = await callApi({ method: "GET", path: "/quote/AAPL" });
    expect(result.ok).toBe(true);
    expect(mocks.radonFetch).toHaveBeenCalledWith(
      "/quote/AAPL",
      expect.objectContaining({ token: PRINCIPAL.token }),
    );
  });

  it("A5 watchlist POST as user_test_1 is invisible to another Clerk user", async () => {
    const posted = await callApi({
      method: "POST",
      path: "/api/watchlist",
      body: { symbol: "VST" },
    });
    expect(posted.ok).toBe(true);
    expect(mocks.radonFetch).not.toHaveBeenCalled();

    const rows = await db.execute("SELECT user_id, symbol FROM user_watchlist");
    expect(rows.rows).toHaveLength(1);
    expect(rows.rows[0]).toMatchObject({ user_id: "user_test_1", symbol: "VST" });

    currentUserId = "user_other";
    const other = await callApi(
      { method: "GET", path: "/api/watchlist" },
      { userId: "user_other", token: "jwt-other" },
    );
    expect(other.ok).toBe(true);
    expect(watchlistFrom(other.data)).toEqual([]);
  });

  it("A7 oversized FastAPI payload is truncated", async () => {
    mocks.radonFetch.mockResolvedValue({ blob: "A".repeat(30_000) });
    const result = await callApi({ method: "GET", path: "/quote/AAPL" });
    expect(result.ok).toBe(true);
    expect(result.data).toEqual(expect.objectContaining({ truncated: true }));
    const serialized = JSON.stringify(result.data);
    expect(serialized.length).toBeLessThan(30_000);
  });

  it("A8 third read.spawn in one turn is refused", async () => {
    mocks.radonFetch.mockResolvedValue({ ok: true, rows: [] });
    const { createAssistantTurnBudget } = await import("@/lib/assistant/tools");
    const budget = createAssistantTurnBudget();

    const first = await callApi({ method: "POST", path: "/scan" }, PRINCIPAL, budget);
    const second = await callApi({ method: "POST", path: "/discover" }, PRINCIPAL, budget);
    const third = await callApi({ method: "POST", path: "/gex/scan" }, PRINCIPAL, budget);

    expect(first.ok).toBe(true);
    expect(second.ok).toBe(true);
    expect(third.ok).toBe(false);
    expect(third.error).toMatch(/spawn/i);
    expect(mocks.radonFetch).toHaveBeenCalledTimes(2);
  });

  it("A15 POST /pi/exec is refused; run_evaluate remains the named path", async () => {
    const result = await callApi({ method: "POST", path: "/pi/exec" });
    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/pi\/exec|internal|run_evaluate/i);
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });

  it("A19 toolSchemas stays at most 20 after list_apis and call_api", async () => {
    const { toolSchemas, isDestructiveTool } = await import("@/lib/assistant/tools");
    const names = toolSchemas().map((schema) => schema.name);
    expect(names).toEqual(expect.arrayContaining(["list_apis", "call_api"]));
    expect(toolSchemas().length).toBeLessThanOrEqual(20);
    expect(isDestructiveTool("list_apis")).toBe(false);
    expect(isDestructiveTool("call_api")).toBe(false);
    expect(isDestructiveTool("place_order")).toBe(true);
  });

  it("watchlist POST then GET lists VST; DELETE removes it; duplicate POST is ok", async () => {
    const posted = await callApi({
      method: "POST",
      path: "/api/watchlist",
      body: { symbol: "VST" },
    });
    expect(posted.ok).toBe(true);

    const listed = await callApi({ method: "GET", path: "/api/watchlist" });
    expect(listed.ok).toBe(true);
    expect(watchlistFrom(listed.data).map((row) => row.symbol)).toContain("VST");

    const duplicate = await callApi({
      method: "POST",
      path: "/api/watchlist",
      body: { symbol: "VST" },
    });
    expect(duplicate.ok).toBe(true);
    const afterDup = await callApi({ method: "GET", path: "/api/watchlist" });
    expect(watchlistFrom(afterDup.data).filter((row) => row.symbol === "VST")).toHaveLength(1);

    const removed = await callApi({ method: "DELETE", path: "/api/watchlist/VST" });
    expect(removed.ok).toBe(true);
    const empty = await callApi({ method: "GET", path: "/api/watchlist" });
    expect(watchlistFrom(empty.data).map((row) => row.symbol)).not.toContain("VST");
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });
});
