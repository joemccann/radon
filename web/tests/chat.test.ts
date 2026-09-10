import { test, expect, vi } from "vitest";
import {
  isPiCommandInput,
  normalizeCommandInput,
  routeToPiPrompt,
  fallbackReply,
  placeProposedOrder,
  resolveSectionFromPath,
} from "../lib/chat";

test("isPiCommandInput identifies valid PI commands", () => {
  expect(isPiCommandInput("scan")).toBe(true);
  expect(isPiCommandInput("/scan")).toBe(true);
  expect(isPiCommandInput("scan --top 20")).toBe(true);
  expect(isPiCommandInput("discover")).toBe(true);
  expect(isPiCommandInput("evaluate AAPL")).toBe(true);
  expect(isPiCommandInput("portfolio")).toBe(true);
  expect(isPiCommandInput("journal --limit 5")).toBe(true);
  expect(isPiCommandInput("help")).toBe(true);
  expect(isPiCommandInput("sync")).toBe(true);
  expect(isPiCommandInput("leap-scan")).toBe(true);
});

test("isPiCommandInput rejects non-commands", () => {
  expect(isPiCommandInput("hello world")).toBe(false);
  expect(isPiCommandInput("analyze brze")).toBe(false);
  expect(isPiCommandInput("")).toBe(false);
  expect(isPiCommandInput("   ")).toBe(false);
});

test("normalizeCommandInput adds leading slash", () => {
  expect(normalizeCommandInput("scan")).toBe("/scan");
  expect(normalizeCommandInput("/scan")).toBe("/scan");
  expect(normalizeCommandInput("  scan --top 5  ")).toBe("/scan --top 5");
});

test("routeToPiPrompt routes direct commands", () => {
  expect(routeToPiPrompt("scan")).toBe("/scan");
  expect(routeToPiPrompt("/scan --top 20")).toBe("/scan --top 20");
  expect(routeToPiPrompt("portfolio")).toBe("/portfolio");
  expect(routeToPiPrompt("discover")).toBe("/discover");
});

test("routeToPiPrompt routes aliases", () => {
  expect(routeToPiPrompt("compare support vs against")).toBe("/scan --top 20");
  expect(routeToPiPrompt("action items")).toBe("/journal --limit 25");
});

test("routeToPiPrompt does not map watchlist phrases to /scan", () => {
  expect(routeToPiPrompt("review watch list")).toBe(null);
  expect(routeToPiPrompt("watch list")).toBe(null);
  expect(routeToPiPrompt("watchlist")).toBe(null);
  expect(routeToPiPrompt("/scan")).toBe("/scan");
  expect(routeToPiPrompt("portfolio")).toBe("/portfolio");
});

test("routeToPiPrompt routes analyze to evaluate", () => {
  expect(routeToPiPrompt("analyze AAPL")).toBe("/evaluate AAPL");
  expect(routeToPiPrompt("analyze brze")).toBe("/evaluate BRZE");
});

test("routeToPiPrompt leaves natural language analysis requests for the assistant", () => {
  expect(routeToPiPrompt("Analyze the best bullish risk reversal for MSFT expiring in mid July")).toBe(null);
  expect(routeToPiPrompt("analyze MSFT risk reversal expiring in mid July")).toBe(null);
});

test("routeToPiPrompt routes keyword matches", () => {
  expect(routeToPiPrompt("show me the portfolio")).toBe("/portfolio");
  expect(routeToPiPrompt("check positions")).toBe("/portfolio");
  expect(routeToPiPrompt("run a scan")).toBe("/scan");
  expect(routeToPiPrompt("open journal")).toBe("/journal");
  expect(routeToPiPrompt("let me discover opportunities")).toBe("/discover");
});

test("routeToPiPrompt routes leap-scan requests to /leap-scan, never the flow /scan", () => {
  // The exact bug report: 'leap scan' was being swallowed by the \\bscan\\b
  // branch and run as a flow scan. It must reach the LEAP scanner instead.
  expect(routeToPiPrompt("run a leap scan for mag7")).toBe("/leap-scan --preset mag7");
  expect(routeToPiPrompt("leap scan semis")).toBe("/leap-scan --preset semis");
  expect(routeToPiPrompt("run a leap scan for mag7")).not.toBe("/scan");
});

test("routeToPiPrompt maps natural-language leap preset keywords", () => {
  expect(routeToPiPrompt("leap scan on semiconductors")).toBe("/leap-scan --preset semis");
  expect(routeToPiPrompt("leap scan the sectors")).toBe("/leap-scan --preset sectors");
  expect(routeToPiPrompt("leap scan emerging markets")).toBe("/leap-scan --preset emerging");
  expect(routeToPiPrompt("leap scan chinese names")).toBe("/leap-scan --preset china");
});

test("routeToPiPrompt defaults an unrecognized leap target (ndx 100) to a real preset, not /scan", () => {
  const routed = routeToPiPrompt("run a leap scan against the ndx 100");
  expect(routed).toBe("/leap-scan --preset mag7");
  expect(routed).not.toBe("/scan");
});

test("routeToPiPrompt leaves an explicit /leap-scan slash command intact", () => {
  expect(routeToPiPrompt("/leap-scan --preset china")).toBe("/leap-scan --preset china");
  expect(routeToPiPrompt("leap-scan --preset semis")).toBe("/leap-scan --preset semis");
});

test("routeToPiPrompt returns null for unrecognized input", () => {
  expect(routeToPiPrompt("hello world")).toBe(null);
  expect(routeToPiPrompt("what is the weather")).toBe(null);
  expect(routeToPiPrompt("")).toBe(null);
  expect(routeToPiPrompt("   ")).toBe(null);
});

test("fallbackReply returns contextual replies", () => {
  expect(fallbackReply("").length > 0).toBeTruthy();
  expect(fallbackReply("brze")).toContain("live position context");
  expect(fallbackReply("analyze rr")).toContain("current flow context");
  expect(fallbackReply("portfolio")).toContain("live portfolio snapshot");
  expect(fallbackReply("compare support vs against")).toContain("unavailable");
  expect(fallbackReply("portfolio")).not.toMatch(/\$[\d,]+|\d+ positions/);
});

test("resolveSectionFromPath maps URL paths to sections", () => {
  expect(resolveSectionFromPath("/", "dashboard")).toBe("dashboard");
  expect(resolveSectionFromPath("/dashboard", "dashboard")).toBe("dashboard");
  expect(resolveSectionFromPath("/flow-analysis", "dashboard")).toBe("flow-analysis");
  expect(resolveSectionFromPath("/portfolio", "dashboard")).toBe("portfolio");
  expect(resolveSectionFromPath("/performance", "dashboard")).toBe("performance");
  expect(resolveSectionFromPath("/scanner", "dashboard")).toBe("scanner");
  expect(resolveSectionFromPath("/discover", "dashboard")).toBe("discover");
  expect(resolveSectionFromPath("/watchlist", "dashboard")).toBe("watchlist");
  expect(resolveSectionFromPath("/journal", "dashboard")).toBe("journal");
  expect(resolveSectionFromPath("/unknown", "dashboard")).toBe("dashboard");
  expect(resolveSectionFromPath(null, "dashboard")).toBe("dashboard");
});

test("validated option proposal submits option or is rejected never stock", async () => {
  const calls: unknown[] = [];
  global.fetch = (async (_url: string, init?: RequestInit) => {
    calls.push(JSON.parse(String(init?.body)));
    return { ok: true, json: async () => ({ message: "ok" }) } as Response;
  }) as typeof fetch;
  await placeProposedOrder({
    tool: "place_order", destructive: true, summary: "BUY option", toolUseId: "1",
    input: { type: "option", ticker: "SPY", action: "BUY", quantity: 1, limit_price: 2, expiry: "20260918", strike: 600, right: "C", conId: 44, exchange: "SMART" },
  });
  expect(calls[0]).toMatchObject({ type: "option", conId: 44, strike: 600 });
});

test("placeProposedOrder maps combo legs instead of forcing a stock order", async () => {
  const fetchMock = vi.fn(async () => ({
    ok: true,
    json: async () => ({ message: "submitted" }),
  }));
  vi.stubGlobal("fetch", fetchMock as unknown as typeof fetch);

  const result = await placeProposedOrder({
    tool: "place_order",
    destructive: true,
    summary: "BUY 10 ADBE bull call spread 480/500 2026-09-18 @ 7",
    toolUseId: "tu-1",
    input: {
      ticker: "ADBE",
      action: "BUY",
      quantity: 10,
      limit_price: 7,
      structure: "bull call spread",
      type: "combo",
      legs: [
        { expiry: "20260918", strike: 480, right: "C", action: "BUY", ratio: 1 },
        { expiry: "20260918", strike: 500, right: "C", action: "SELL", ratio: 1 },
      ],
    },
  });

  expect(result.ok).toBe(true);
  const sent = JSON.parse(fetchMock.mock.calls[0][1].body as string);
  expect(sent.type).toBe("combo");
  expect(sent.symbol).toBe("ADBE");
  expect(sent.quantity).toBe(10);
  expect(sent.limitPrice).toBe(7);
  expect(sent.legs).toHaveLength(2);
  expect(sent.legs[0]).toMatchObject({ strike: 480, action: "BUY", right: "C" });
  vi.unstubAllGlobals();
});
