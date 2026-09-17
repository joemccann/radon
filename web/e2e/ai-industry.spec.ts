import { test, expect, type Page } from "@playwright/test";
import { installClearFixtures } from "./clear-fixtures";
import { aiFixture } from "../tests/fixtures/aiInfrastructure";
import type { AiPane, AiSnapshot } from "../lib/aiInfrastructure";

// Synthetic transport fixture only: publisher names and measurement coverage,
// never live broker requests or persisted observations.
const publishers = [
  ["openrouter", "OpenRouter"], ["portkey", "Portkey"], ["lambda", "Lambda"],
  ["ramp", "Ramp AI Index"], ["vast", "Vast.ai"], ["runpod", "Runpod"],
  ["artificialanalysis", "Artificial Analysis"], ["liquidcompute", "Liquid Compute"],
  ["sec", "SEC company facts"], ["issuer", "Issuer disclosures"], ["eia", "EIA"],
  ["noaa", "NOAA"], ["epoch", "Epoch AI"], ["designarena", "Design Arena"],
  ["ib", "Interactive Brokers"], ["uw", "Unusual Whales"],
] as const;
const measures: [string, string, AiPane, string, string][] = [
  ["D1", "Public routed activity", "demand", "tokens", "openrouter"],
  ["D2", "Application activity", "demand", "requests", "openrouter"],
  ["D3", "Gateway mix", "demand", "%", "portkey"],
  ["D4", "Independent host activity", "demand", "tokens", "lambda"],
  ["D5", "Ramp business AI spend", "demand", "USD/employee-month", "ramp"],
  ["D6", "Design-task model quality", "demand", "score", "designarena"],
  ["C1", "Matched GPU asking prices", "compute", "USD/GPU-hour", "vast"],
  ["C2", "Rentable GPU supply", "compute", "GPUs", "runpod"],
  ["C3", "Model inference prices", "compute", "USD/million tokens", "openrouter"],
  ["C4", "Cost of useful work", "compute", "USD/task", "artificialanalysis"],
  ["C5", "Liquid Compute GPU index", "compute", "USD/GPU-hour", "liquidcompute"],
  ["H1", "Hardware delivery", "delivery", "USD", "sec"],
  ["H2", "Inventory and collections", "delivery", "days", "sec"],
  ["P1", "Power demand context", "delivery", "MWh", "eia"],
  ["P2", "Power coming online", "delivery", "MW", "issuer"],
  ["F1", "Cash funding coverage", "finance", "ratio", "sec"],
  ["F2", "Future revenue and obligations", "finance", "USD", "issuer"],
  ["M1", "Market pricing context", "finance", "USD", "ib"],
];
const missing = new Set(["portkey", "lambda", "issuer"]);
const snapshot: AiSnapshot = {
  ...aiFixture,
  sources: publishers.map(([id, name]) => ({ ...aiFixture.sources[0], id, name,
    url: `https://example.com/${id}`, lineage_group: id,
    status: missing.has(id) ? "unavailable" : id === "eia" ? "stale" : "available",
    reason: missing.has(id) ? "No observations collected in this fixture." : "Synthetic source coverage.",
    observation_count: missing.has(id) ? 0 : 90,
  })),
  indicators: measures.map(([id, title, pane, unit, source]) => ({ ...aiFixture.indicators[0], id, title, pane,
    source_ids: [source], status: missing.has(source) ? "unavailable" : id === "P1" ? "stale" : "available",
    metrics: missing.has(source) ? [] : [{ ...aiFixture.indicators[0].metrics[0], id: `${id}-series`, label: title, unit, source_id: source }],
    history: missing.has(source) ? [] : Array.from({ length: 90 }, (_, i) => ({
      date: new Date(Date.UTC(2026, 5, i + 1)).toISOString().slice(0, 10),
      value: 30 + i / 10 + (i % 4), unit, source_id: source, series_id: `${id}-series`, label: title,
    })),
  })),
};
async function ready(page: Page, route = "/ai-industry") {
  await page.goto(route);
  await expect(page.getByRole("button", { name: "Refresh snapshot" })).toBeEnabled({ timeout: 45_000 });
}

test.describe("AI industry value chain", () => {
  test.setTimeout(90_000);
  test.beforeEach(async ({ page }) => {
    await installClearFixtures(page);
    await page.route("**/api/ai-cycle", route => route.fulfill({ json: snapshot }));
    await page.route("**/api/llm-token-index**", route => route.fulfill({ json: { rows: [], count: 0, days: 180 } }));
  });
  test("four stages, separate capability and all 18 plain-language measure explanations", async ({ page }) => {
    await ready(page);
    await expect(page.getByRole("heading", { name: "AI industry", exact: true })).toBeVisible();
    await expect(page.getByText("18 measures", { exact: true })).toBeVisible();
    await expect(page.getByText("16 sources", { exact: true })).toBeVisible();
    const tabs = page.getByRole("tablist", { name: "AI industry value chain" });
    await expect(tabs.getByRole("tab")).toHaveCount(4);
    for (const stage of ["Adoption", "Compute", "Buildout", "Funding"]) {
      const tab = tabs.getByRole("tab", { name: new RegExp(stage) });
      await tab.click();
      await expect(tab).toHaveAttribute("aria-selected", "true");
      await expect(page.getByTestId("ai-industry-history-chart")).toBeVisible();
    }
    await page.getByRole("button", { name: "Model capability", exact: true }).click();
    await expect(page.getByRole("region", { name: "Model capability", exact: true })).toBeVisible();
    for (const [id] of measures) {
      const explanation = page.getByTestId(`ai-indicator-${id}`);
      await explanation.locator("summary").click();
      await expect(explanation.getByRole("heading", { name: "Why it matters", exact: true })).toBeVisible();
      await expect(explanation.getByRole("heading", { name: "What it cannot prove", exact: true })).toBeVisible();
      await explanation.locator("summary").click();
    }
  });
  test("legacy deep link preserves pane; shared Regime chart supports ranges and keyboard brush", async ({ page }) => {
    await ready(page, "/regime/llm?pane=compute");
    await expect(page).toHaveURL(/\/ai-industry\?pane=compute$/);
    await expect(page.getByRole("tab", { name: /Compute/ })).toHaveAttribute("aria-selected", "true");
    const chart = page.getByTestId("ai-industry-history-chart");
    const ranges = chart.getByRole("navigation", { name: "AI observation history range" });
    await ranges.getByRole("button", { name: "1M", exact: true }).click();
    await expect(ranges.getByRole("button", { name: "1M", exact: true })).toHaveAttribute("aria-pressed", "true");
    await ranges.getByRole("button", { name: "All", exact: true }).click();
    const start = chart.getByRole("slider", { name: "Start of visible range", exact: true });
    await expect(start).toHaveAttribute("aria-valuenow", "0");
    await start.focus(); await page.keyboard.press("ArrowRight");
    await expect(start).toHaveAttribute("aria-valuenow", "1");
    await expect(chart.getByText("89 of 90 observations", { exact: true })).toBeVisible();
    const plot = chart.getByRole("slider", { name: "Inspect Matched GPU asking prices history", exact: true });
    await plot.focus(); await page.keyboard.press("Home");
    await expect(plot).toHaveAttribute("aria-valuenow", "0");
    const opener = page.getByRole("article", { name: "GPU rental prices evidence" }).getByRole("button", { name: "Sources and method: Matched GPU asking prices" });
    await opener.click(); await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByText("coverage_numerator", { exact: false })).toBeVisible();
    await page.keyboard.press("Escape"); await expect(page.getByRole("dialog")).toHaveCount(0); await expect(opener).toBeFocused();
  });
  test("coverage search, missing observations, stale evidence and failed refresh are explicit", async ({ page }) => {
    await ready(page);
    const coverage = page.getByTestId("ai-source-coverage");
    await coverage.getByRole("searchbox", { name: "Find a source" }).fill("Portkey");
    await expect(coverage.getByTestId("ai-coverage-portkey")).toContainText("No collected observations");
    await expect(coverage.getByTestId("ai-coverage-ramp")).toHaveCount(0);
    await coverage.getByRole("searchbox").fill("not-a-publisher");
    await expect(coverage).toContainText("No sources match your search.");
    await page.getByRole("combobox", { name: "Measure", exact: true }).selectOption("D3");
    await expect(page.getByRole("article", { name: "Gateway mix evidence" })).toContainText("No comparable history is available. Missing observations are not zero.");
    await page.getByRole("tab", { name: /Buildout/ }).click();
    await page.getByRole("combobox", { name: "Measure", exact: true }).selectOption("P1");
    await expect(page.getByRole("article", { name: "Power demand context evidence" }).getByText("stale", { exact: true })).toBeVisible();
    await page.route("**/api/ai-cycle", route => route.fulfill({ status: 503, json: { detail: "Unavailable" } }));
    await page.getByRole("button", { name: "Refresh snapshot" }).click();
    await expect(page.locator(".toast-container").getByRole("alert").filter({ hasText: "This service is temporarily unavailable" })).toContainText("temporarily unavailable");
    await expect(page.locator(".toast-container").getByRole("alert").filter({ hasText: "This service is temporarily unavailable" })).toContainText("Showing the last available data");
  });
  test("initial loading and first-request failure do not imply zero-valued evidence", async ({ page }) => {
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    await page.route("**/api/ai-cycle", async route => { await pending; await route.fulfill({ status: 503, json: { detail: "Unavailable" } }); });
    try {
      await page.goto("/ai-industry");
      await expect(page.getByRole("button", { name: "Loading…", exact: true })).toBeDisabled();
      await expect(page.getByText("Retrieving source observations…", { exact: true })).toBeVisible();
    } finally { release(); }
    await expect(page.locator(".toast-container").getByRole("alert").filter({ hasText: "This service is temporarily unavailable" })).toContainText("temporarily unavailable");
    await expect(page.getByTestId("ai-infrastructure-panel").getByRole("alert")).toHaveCount(0);
    await expect(page.getByTestId("ai-industry-history-chart")).toHaveCount(0);
  });
  test("dashboard and ticker handoffs use the top-level category without order mutations", async ({ page }) => {
    const mutations: string[] = [];
    page.on("request", request => { if (/\/api\/orders\/(place|cancel|modify)/.test(request.url())) mutations.push(request.url()); });
    await page.goto("/dashboard");
    const dashboardLink = page.getByTestId("clear-overview").getByRole("link", { name: "AI industry evidence" });
    await expect(dashboardLink).toHaveAttribute("href", "/ai-industry");
    await dashboardLink.click();
    await expect(page).toHaveURL(/\/ai-industry$/);
    await page.goto("/MSFT?deck=i");
    const handoff = page.getByRole("complementary", { name: "AI industry research" });
    await expect(handoff).toContainText("Cloud monetization");
    const link = handoff.getByRole("link", { name: "Review industry evidence" });
    await expect(link).toHaveAttribute("href", "/ai-industry?pane=finance");
    await link.click();
    await expect(page.getByRole("tab", { name: /Funding/ })).toHaveAttribute("aria-selected", "true");
    expect(mutations).toEqual([]);
  });
  for (const theme of ["light", "dark"]) for (const mobile of [false, true]) {
    test(`${theme} ${mobile ? "mobile" : "desktop"} visual evidence and layout`, async ({ page }, testInfo) => {
      await page.setViewportSize(mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 });
      await page.addInitScript(value => localStorage.setItem("theme", value), theme);
      await ready(page, "/ai-industry?pane=compute");
      await page.evaluate(value => document.documentElement.setAttribute("data-theme", value), theme);
      await expect(page.getByTestId("ai-industry-history-chart")).toBeVisible();
      await page.getByTestId("ai-indicator-C1").locator("summary").click();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      const path = testInfo.outputPath(`ai-industry-${theme}-${mobile ? "mobile" : "desktop"}.png`);
      await page.screenshot({ path, fullPage: false });
      const chartPath = testInfo.outputPath(`ai-industry-chart-${theme}-${mobile ? "mobile" : "desktop"}.png`);
      await page.getByTestId("ai-industry-history-chart").screenshot({ path: chartPath });
      await testInfo.attach("Shared Regime chart", { path: chartPath, contentType: "image/png" });
      await testInfo.attach("AI industry visual evidence", { path, contentType: "image/png" });
      await page.getByRole("article", { name: "GPU rental prices evidence" }).getByRole("button", { name: "Sources and method: Matched GPU asking prices" }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      expect(await page.getByRole("dialog").evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    });
  }
});
