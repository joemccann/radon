import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

const PRINCIPAL = { userId: "user_test", token: "jwt" };

describe("assistant backend tools", () => {
  beforeEach(() => {
    vi.resetModules();
    mocks.radonFetch.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("registers live market, evaluate, and fetch_backend as READ tools", async () => {
    const { ASSISTANT_TOOLS, isDestructiveTool, toolSchemas } = await import(
      "@/lib/assistant/tools"
    );

    const names = ASSISTANT_TOOLS.map((tool) => tool.name);
    expect(names).toEqual(
      expect.arrayContaining([
        "get_quote",
        "get_option_expirations",
        "get_option_chain",
        "rank_spreads",
        "run_evaluate",
        "fetch_backend",
      ]),
    );

    for (const name of [
      "get_quote",
      "get_option_expirations",
      "get_option_chain",
      "rank_spreads",
      "run_evaluate",
      "fetch_backend",
    ]) {
      expect(isDestructiveTool(name)).toBe(false);
    }

    const schemaNames = toolSchemas().map((schema) => schema.name);
    expect(schemaNames).toEqual(expect.arrayContaining(names));
  });

  it("get_quote hits FastAPI /quote/{ticker}", async () => {
    mocks.radonFetch.mockResolvedValue({ ticker: "ADBE", last: 481.2, source: "uw" });
    const { executeTool } = await import("@/lib/assistant/tools");

    const result = await executeTool("get_quote", { ticker: "adbe" }, PRINCIPAL);
    expect(result.ok).toBe(true);
    expect(mocks.radonFetch).toHaveBeenCalledWith(
      "/quote/ADBE",
      expect.objectContaining({ token: "jwt" }),
    );
  });

  it("get_option_chain hits the priced UW chain and keeps compact fields", async () => {
    mocks.radonFetch.mockResolvedValue({
      ticker: "ADBE",
      expiry: "2026-09-18",
      spot: 481.2,
      contracts: [
        { strike: 480, right: "C", bid: 10, ask: 10.4, mid: 10.2, iv: 0.28, oi: 1200, volume: 400 },
      ],
    });
    const { executeTool } = await import("@/lib/assistant/tools");

    const result = await executeTool("get_option_chain", {
      ticker: "ADBE",
      expiry: "2026-09-18",
      right: "C",
    }, PRINCIPAL);
    expect(result.ok).toBe(true);
    const [path] = mocks.radonFetch.mock.calls[0];
    expect(String(path)).toContain("/options/uw-chain");
    expect(String(path)).toContain("symbol=ADBE");
    expect(String(path)).toContain("expiry=2026-09-18");
  });

  it("rank_spreads fetches a priced chain and returns ranked bull call payouts", async () => {
    mocks.radonFetch.mockImplementation(async (path: string) => {
      if (String(path).startsWith("/quote/")) {
        return { ticker: "ADBE", last: 480, source: "uw" };
      }
      return {
        ticker: "ADBE",
        expiry: "2026-09-18",
        spot: 480,
        contracts: [
          { strike: 480, right: "C", bid: 10, ask: 10.4, mid: 10.2 },
          { strike: 500, right: "C", bid: 3, ask: 3.4, mid: 3.2 },
        ],
      };
    });
    const { executeTool } = await import("@/lib/assistant/tools");

    const result = await executeTool("rank_spreads", {
      ticker: "ADBE",
      expiry: "2026-09-18",
      kind: "bull_call",
      quantity: 10,
    }, PRINCIPAL);
    expect(result.ok).toBe(true);
    const data = result.data as { spreads: Array<{ buyStrike: number; maxPayoutDollars: number }> };
    expect(data.spreads[0].buyStrike).toBe(480);
    expect(data.spreads[0].maxPayoutDollars).toBeCloseTo(13_000, 0);
  });

  it("run_evaluate posts evaluate.py to /pi/exec without mutating", async () => {
    mocks.radonFetch.mockResolvedValue({
      ok: true,
      stdout: "M4 EDGE PASS\nTRADE",
      stderr: "",
      exit_code: 0,
      timed_out: false,
    });
    const { executeTool } = await import("@/lib/assistant/tools");

    const result = await executeTool("run_evaluate", { ticker: "ADBE" }, PRINCIPAL);
    expect(result.ok).toBe(true);
    const [path, opts] = mocks.radonFetch.mock.calls[0];
    expect(path).toBe("/pi/exec");
    const body = JSON.parse((opts as { body: string }).body);
    expect(body.script).toBe("evaluate.py");
    expect(body.args[0]).toBe("ADBE");
    expect(body.allow_mutating).toBeUndefined();
  });

  it("fetch_backend allows listed READ paths and refuses mutations", async () => {
    mocks.radonFetch.mockResolvedValue({ ticker: "ADBE", shortable: true });
    const { executeTool } = await import("@/lib/assistant/tools");

    const allowed = await executeTool("fetch_backend", {
      method: "GET",
      path: "/short-availability/ADBE",
    }, PRINCIPAL);
    expect(allowed.ok).toBe(true);
    expect(mocks.radonFetch).toHaveBeenCalledWith(
      "/short-availability/ADBE",
      expect.objectContaining({ method: "GET" }),
    );

    const denied = await executeTool("fetch_backend", {
      method: "POST",
      path: "/orders/place",
    }, PRINCIPAL);
    expect(denied.ok).toBe(false);
    expect(denied.error).toMatch(/not allowed/i);
    expect(mocks.radonFetch).toHaveBeenCalledTimes(1);
  });
});
