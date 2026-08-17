/**
 * REL-036 / R-062 — every Next.js UW fetch must move the shared daily budget.
 *
 * UWClient (Python) records each UW HTTP hit into the flock-shared budget
 * file; the six route-handler call sites fetched UW directly and incremented
 * nothing, so browsing-driven traffic was invisible to /uw/usage and the
 * universe-scan brake. countedUwFetch mirrors one hit per UW response into
 * the shared counter via POST /uw/usage/record (fire-and-forget).
 */
import { readFileSync } from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { afterEach, describe, expect, it, vi } from "vitest";

const { radonFetchMock } = vi.hoisted(() => ({
  radonFetchMock: vi.fn(async () => ({})),
}));

vi.mock("@/lib/radonApi", () => ({ radonFetch: radonFetchMock }));

import { countedUwFetch } from "@/lib/uwCountedFetch";

const WEB_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

afterEach(() => {
  vi.unstubAllGlobals();
  radonFetchMock.mockClear();
});

describe("countedUwFetch", () => {
  it("records one budget hit per resolved UW fetch", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}")));

    await countedUwFetch("https://api.unusualwhales.com/api/stock/AAPL/info");
    await countedUwFetch("https://api.unusualwhales.com/api/stock/AAPL/stock-state");
    await countedUwFetch("https://api.unusualwhales.com/api/news/headlines");

    expect(radonFetchMock).toHaveBeenCalledTimes(3);
    expect(radonFetchMock).toHaveBeenCalledWith(
      "/uw/usage/record",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("records nothing when the UW request never produced a response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network down");
      }),
    );

    await expect(
      countedUwFetch("https://api.unusualwhales.com/api/stock/AAPL/info"),
    ).rejects.toThrow("network down");
    expect(radonFetchMock).not.toHaveBeenCalled();
  });

  it("never lets a failed budget record break the UW data path", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
    radonFetchMock.mockRejectedValueOnce(new Error("fastapi down"));

    const res = await countedUwFetch("https://api.unusualwhales.com/api/stock/AAPL/info");

    expect(res.status).toBe(200);
  });
});

describe("UW route call sites go through the counted path", () => {
  const ROUTES = [
    "app/api/ticker/info/route.ts",
    "app/api/previous-close/route.ts",
    "app/api/ticker/seasonality/route.ts",
    "app/api/ticker/news/route.ts",
  ];

  for (const route of ROUTES) {
    it(`${route} counts every UW fetch`, () => {
      const src = readFileSync(path.join(WEB_DIR, route), "utf-8");
      const uwLiterals = (src.match(/api\.unusualwhales\.com/g) ?? []).length;
      const countedCalls = (src.match(/countedUwFetch\(/g) ?? []).length;
      expect(uwLiterals).toBeGreaterThan(0);
      // One countedUwFetch call per UW URL literal — a bare fetch() against
      // UW is invisible to the daily budget gauge and brake (R-062).
      expect(countedCalls).toBe(uwLiterals);
    });
  }
});
