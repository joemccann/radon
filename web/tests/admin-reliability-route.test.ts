/**
 * @vitest-environment node
 *
 * The reliability history is itself an operational dependency. A stale Turso
 * client must not turn the admin health surface into an unbounded request.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const execute = vi.fn();
const resetDb = vi.fn();

vi.mock("@/lib/db", () => ({
  getDb: () => ({ execute }),
  resetDb,
}));

beforeEach(() => {
  vi.resetModules();
  execute.mockReset();
  resetDb.mockReset();
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
    await vi.advanceTimersByTimeAsync(3_000);

    const response = await responsePromise;
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({
      events: [],
      baseline: {},
      missing: true,
      error: "admin-reliability-events read timed out after 3000ms",
    });
    expect(resetDb).toHaveBeenCalledTimes(1);
  });
});
