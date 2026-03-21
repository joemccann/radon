import { test, expect } from "vitest";
import {
  isPiCommandInput,
  normalizeCommandInput,
  routeToPiPrompt,
  fallbackReply,
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
  expect(routeToPiPrompt("watch list")).toBe("/scan --top 12");
  expect(routeToPiPrompt("watchlist")).toBe("/scan --top 12");
});

test("routeToPiPrompt routes analyze to evaluate", () => {
  expect(routeToPiPrompt("analyze AAPL")).toBe("/evaluate AAPL");
  expect(routeToPiPrompt("analyze brze")).toBe("/evaluate BRZE");
});

test("routeToPiPrompt routes keyword matches", () => {
  expect(routeToPiPrompt("show me the portfolio")).toBe("/portfolio");
  expect(routeToPiPrompt("check positions")).toBe("/portfolio");
  expect(routeToPiPrompt("run a scan")).toBe("/scan");
  expect(routeToPiPrompt("open journal")).toBe("/journal");
  expect(routeToPiPrompt("let me discover opportunities")).toBe("/discover");
});

test("routeToPiPrompt returns null for unrecognized input", () => {
  expect(routeToPiPrompt("hello world")).toBe(null);
  expect(routeToPiPrompt("what is the weather")).toBe(null);
  expect(routeToPiPrompt("")).toBe(null);
  expect(routeToPiPrompt("   ")).toBe(null);
});

test("fallbackReply returns contextual replies", () => {
  expect(fallbackReply("").length > 0).toBeTruthy();
  expect(fallbackReply("brze").includes("BRZE")).toBeTruthy();
  expect(fallbackReply("analyze rr").includes("RR")).toBeTruthy();
  expect(fallbackReply("portfolio").includes("19 positions")).toBeTruthy();
  expect(fallbackReply("compare support vs against").includes("6 positions")).toBeTruthy();
});

test("resolveSectionFromPath maps URL paths to sections", () => {
  expect(resolveSectionFromPath("/", "dashboard")).toBe("dashboard");
  expect(resolveSectionFromPath("/dashboard", "dashboard")).toBe("dashboard");
  expect(resolveSectionFromPath("/flow-analysis", "dashboard")).toBe("flow-analysis");
  expect(resolveSectionFromPath("/portfolio", "dashboard")).toBe("portfolio");
  expect(resolveSectionFromPath("/performance", "dashboard")).toBe("performance");
  expect(resolveSectionFromPath("/scanner", "dashboard")).toBe("scanner");
  expect(resolveSectionFromPath("/discover", "dashboard")).toBe("discover");
  expect(resolveSectionFromPath("/journal", "dashboard")).toBe("journal");
  expect(resolveSectionFromPath("/internals", "dashboard")).toBe("internals");
  expect(resolveSectionFromPath("/unknown", "dashboard")).toBe("dashboard");
  expect(resolveSectionFromPath(null, "dashboard")).toBe("dashboard");
});
