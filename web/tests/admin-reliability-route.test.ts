/**
 * @vitest-environment node
 *
 * The reliability history is itself an operational dependency. A stale Turso
 * client must not turn the admin health surface into an unbounded request.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const execute = vi.fn();
const resetDb = vi.fn();

vi.mock("@clerk/nextjs/server", () => ({
  auth: vi.fn(async () => ({ userId: "user_test" })),
}));

vi.mock("@/lib/db", () => ({
  getDb: () => ({ execute }),
  resetDb,
}));

beforeEach(() => {
  vi.resetModules();
  execute.mockReset();
  resetDb.mockReset();
  process.env.ALLOWED_USER_IDS = "user_test";
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("GET /api/admin/reliability", () => {
  it("bounds a hung history read and resets the DB client", async () => {
    vi.useFakeTimers();
    execute.mockImplementation(() => new Promise(() => {}));

    const { GET } = await import("../app/api/admin/reliability/route");
    const responsePromise = GET();
    // Handler-local auth resolves before the bounded DB timer is scheduled.
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(3_000);

    const response = await responsePromise;
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({
      events: [],
      baseline: {},
      missing: true,
      error: "Reliability history unavailable",
    });
    expect(resetDb).toHaveBeenCalledTimes(1);
  });
});
