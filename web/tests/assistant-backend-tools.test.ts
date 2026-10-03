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

  function fencedBody<T>(data: unknown): T {
    const lines = (data as { excerpt: string }).excerpt.split("\n");
    return JSON.parse(lines.slice(1, -1).join("\n")) as T;
  }

  it("get_option_chain keeps compact UW fields when the fallback serves the chain", async () => {
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
    const [path] = mocks.radonFetch.mock.calls[1];
    expect(String(path)).toContain("/options/uw-chain");
    expect(String(path)).toContain("symbol=ADBE");
    expect(String(path)).toContain("expiry=2026-09-18");
    const body = fencedBody<{ source: string; contracts: Array<{ oi: number; volume: number }> }>(result.data);
    expect(body.source).toBe("uw");
    expect(body.contracts[0]).toMatchObject({ oi: 1200, volume: 400 });
  });

  it("get_option_expirations reads the IB secdef list first, same source as the chain UI", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-02T15:00:00Z"));
    mocks.radonFetch.mockResolvedValue({ symbol: "SPCX", expirations: ["20261030", "20261218"] });
    const { executeTool } = await import("@/lib/assistant/tools");

    const result = await executeTool("get_option_expirations", { ticker: "spcx" }, PRINCIPAL);
    vi.useRealTimers();
    expect(result.ok).toBe(true);
    expect(mocks.radonFetch).toHaveBeenCalledTimes(1);
    expect(mocks.radonFetch.mock.calls[0][0]).toBe("/options/expirations?symbol=SPCX");
    const body = fencedBody<{ source: string; expirations: Array<{ expiry: string; dte: number }> }>(
      result.data,
    );
    expect(body.source).toBe("ib");
    expect(body.expirations).toEqual([
      { expiry: "2026-10-30", dte: 28 },
      { expiry: "2026-12-18", dte: 77 },
    ]);
  });

  it("get_option_expirations falls back to UW when the IB secdef read fails", async () => {
    const { RadonApiError } = await import("@/lib/radonApi");
    mocks.radonFetch.mockImplementation(async (path: string) => {
      if (path.startsWith("/options/expirations")) throw new RadonApiError("gateway down", 503);
      return { ticker: "SPCX", source: "uw", expirations: [{ expiry: "2026-10-30", dte: 28 }], contracts: [] };
    });
    const { executeTool } = await import("@/lib/assistant/tools");

    const result = await executeTool("get_option_expirations", { ticker: "SPCX" }, PRINCIPAL);
    expect(result.ok).toBe(true);
    expect(mocks.radonFetch.mock.calls.map(([path]) => path)).toEqual([
      "/options/expirations?symbol=SPCX",
      "/options/uw-chain?symbol=SPCX",
    ]);
    const body = fencedBody<{ source: string; expirations: Array<{ expiry: string }> }>(result.data);
    expect(body.source).toBe("uw");
    expect(body.expirations[0].expiry).toBe("2026-10-30");
  });

  it("get_option_term_structure prices every tenor from IB in one call and reports ATM IV per tenor", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-02T15:00:00Z"));
    const row = (expiry: string, strike: number, iv: number) => ({
      strike, right: "C", expiry, bid: 9, ask: 9.4, mid: 9.2, iv, delta: 0.5, gamma: 0.02, theta: -0.1, vega: 0.2,
    });
    mocks.radonFetch.mockImplementation(async (path: string) => {
      if (path.startsWith("/options/expirations")) {
        return { symbol: "SPCX", expirations: ["20261030", "20261120", "20261218", "20270115"] };
      }
      if (path.startsWith("/options/ib-quotes")) {
        return {
          ticker: "SPCX",
          spot: 158.65,
          source: "ib",
          expirations: {
            "2026-10-30": [row("2026-10-30", 155, 0.46), row("2026-10-30", 160, 0.45)],
            "2026-11-20": [row("2026-11-20", 160, 0.48)],
            "2026-12-18": [],
          },
        };
      }
      return {
        ticker: "SPCX",
        expiry: "2026-12-18",
        spot: 158.6,
        source: "uw",
        contracts: [row("2026-12-18", 160, 0.52)],
      };
    });
    const { executeTool } = await import("@/lib/assistant/tools");

    const result = await executeTool(
      "get_option_term_structure",
      { ticker: "SPCX", right: "C", max_dte: 90 },
      PRINCIPAL,
    );
    vi.useRealTimers();
    expect(result.ok).toBe(true);
    expect(mocks.radonFetch.mock.calls.map(([path]) => String(path))).toEqual([
      "/options/expirations?symbol=SPCX",
      "/options/ib-quotes?symbol=SPCX&expiries=2026-10-30%2C2026-11-20%2C2026-12-18&wings=4&right=C",
      // IB returned no book for December only; UW fills that tenor alone.
      "/options/uw-chain?symbol=SPCX&expiry=2026-12-18&wings=4&right=C",
    ]);
    const body = fencedBody<{
      ticker: string;
      spot: number;
      expirations_source: string;
      pricing_source: string;
      term: Array<{ expiry: string; dte: number; atm_strike: number; atm_iv: number; pricing_source: string; contracts: unknown[] }>;
    }>(result.data);
    expect(body.ticker).toBe("SPCX");
    expect(body.spot).toBe(158.65);
    expect(body.expirations_source).toBe("ib");
    expect(body.pricing_source).toBe("mixed");
    expect(body.term.map((r) => [r.expiry, r.dte, r.atm_strike, r.atm_iv, r.pricing_source])).toEqual([
      ["2026-10-30", 28, 160, 0.45, "ib"],
      ["2026-11-20", 49, 160, 0.48, "ib"],
      ["2026-12-18", 77, 160, 0.52, "uw"],
    ]);
    expect(body.term[0].contracts).toHaveLength(2);
  });

  it("get_option_chain prices the expiry from IB snapshots before UW", async () => {
    mocks.radonFetch.mockResolvedValue({
      ticker: "SPCX",
      spot: 158.65,
      source: "ib",
      expirations: {
        "2026-10-30": [{ strike: 160, right: "C", expiry: "2026-10-30", bid: 7, ask: 7.2, mid: 7.1, iv: 0.448, delta: 0.56 }],
      },
    });
    const { executeTool } = await import("@/lib/assistant/tools");

    const result = await executeTool("get_option_chain", { ticker: "spcx", expiry: "20261030", right: "c" }, PRINCIPAL);
    expect(result.ok).toBe(true);
    expect(mocks.radonFetch).toHaveBeenCalledTimes(1);
    const [path, opts] = mocks.radonFetch.mock.calls[0];
    expect(path).toBe("/options/ib-quotes?symbol=SPCX&expiries=2026-10-30&wings=8&right=C");
    expect((opts as { timeout: number }).timeout).toBeGreaterThan(60_000);
    const body = fencedBody<{ source: string; expiry: string; spot: number; contracts: Array<{ strike: number; iv: number }> }>(
      result.data,
    );
    expect(body).toMatchObject({ source: "ib", expiry: "2026-10-30", spot: 158.65 });
    expect(body.contracts).toEqual([expect.objectContaining({ strike: 160, iv: 0.448 })]);
  });

  it.each([
    ["the IB quote route fails", async () => { const { RadonApiError } = await import("@/lib/radonApi"); throw new RadonApiError("gateway down", 504); }],
    ["IB returns an unpriced book", async () => ({ ticker: "SPCX", spot: null, source: "ib", expirations: { "2026-10-30": [{ strike: 160, right: "C", bid: null, ask: null, iv: null }] } })],
  ])("get_option_chain falls back to UW when %s", async (_label, ibResponse) => {
    mocks.radonFetch.mockImplementation(async (path: string) => {
      if (path.startsWith("/options/ib-quotes")) return ibResponse();
      return { ticker: "SPCX", expiry: "2026-10-30", spot: 158.6, source: "uw", contracts: [{ strike: 160, right: "C", bid: 7, ask: 7.3, iv: 0.45 }] };
    });
    const { executeTool } = await import("@/lib/assistant/tools");

    const result = await executeTool("get_option_chain", { ticker: "SPCX", expiry: "2026-10-30" }, PRINCIPAL);
    expect(result.ok).toBe(true);
    expect(mocks.radonFetch.mock.calls.map(([path]) => path)).toEqual([
      "/options/ib-quotes?symbol=SPCX&expiries=2026-10-30&wings=8",
      "/options/uw-chain?symbol=SPCX&expiry=2026-10-30&wings=8",
    ]);
    expect(fencedBody<{ source: string }>(result.data).source).toBe("uw");
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
    // RC-B7/C09: non-knowledge tool results are fenced; the ONLY copy of the
    // payload is the fenced excerpt's JSON.
    const fenced = result.data as { excerpt: string };
    const lines = fenced.excerpt.split("\n");
    const body = JSON.parse(lines.slice(1, -1).join("\n")) as {
      spreads: Array<{ buyStrike: number; maxPayoutDollars: number }>;
    };
    expect(body.spreads[0].buyStrike).toBe(480);
    expect(body.spreads[0].maxPayoutDollars).toBeCloseTo(13_000, 0);
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
