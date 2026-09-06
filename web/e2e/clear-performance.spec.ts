import { test, expect, type WebSocketRoute } from "@playwright/test";
import { writeFileSync } from "node:fs";
import { installClearFixtures, CLEAR_FIXTURE_TIME } from "./clear-fixtures";

type Sample = { startTime: number; duration: number; name?: string; value?: number; element?: string };
type BrowserMetrics = { paints: Sample[]; lcp: Sample[]; shifts: Sample[]; longTasks: Sample[]; events: Sample[]; interactions: number[] };
declare global { interface Window { __clearMetrics: BrowserMetrics } }

test.use({
  baseURL: process.env.CLEAR_PERF_BASE_URL ?? "http://localhost:3002",
  extraHTTPHeaders: { "x-radon-authless-test": process.env.RADON_AUTHLESS_TEST_TOKEN ?? "clear-local-verification-20260905" },
  serviceWorkers: "block", colorScheme: "light", reducedMotion: "reduce",
});
test.skip(process.env.CLEAR_PERF !== "1", "Opt-in measurement against a compiled production server, not the development server.");

function clsWindow(shifts: Sample[]) {
  let peak = 0;
  let value = 0;
  let start = 0;
  let previous = -Infinity;
  for (const shift of shifts) {
    if (shift.startTime - previous > 1000 || shift.startTime - start > 5000) { start = shift.startTime; value = 0; }
    value += shift.value ?? 0;
    peak = Math.max(peak, value);
    previous = shift.startTime;
  }
  return peak;
}

const quote = (symbol: string, last: number, delta: number | null = null) => ({ symbol, last, bid: last - 0.01, ask: last + 0.01, close: last - 1, delta, timestamp: CLEAR_FIXTURE_TIME });
const initialQuotes = { AAPL: quote("AAPL", 232.18), MSFT: quote("MSFT", 525.5), MSFT_20270115_530_C: quote("MSFT_20270115_530_C", 9.05, 0.4) };

for (const viewport of [{ label: "desktop", width: 1440, height: 1000 }, { label: "mobile", width: 390, height: 844 }]) {
  for (let run = 1; run <= Number(process.env.CLEAR_PERF_RUNS ?? 1); run += 1) {
    test(`Clear production ${viewport.label} cold load ${run}`, async ({ page, context }, testInfo) => {
      test.setTimeout(30_000);
      await page.setViewportSize(viewport);
      const apiRequests = await installClearFixtures(page);
      const sockets: WebSocketRoute[] = [];
      const socketMessages: string[] = [];
      await page.routeWebSocket(/ws:\/\/127\.0\.0\.1:18765|ws:\/\/localhost:8765|\/ws(?:\?|$)/, (socket) => {
        sockets.push(socket);
        socket.onMessage((raw) => {
          const message = JSON.parse(raw.toString());
          socketMessages.push(JSON.stringify(message));
          if (message.action === "subscribe") {
            socket.send(JSON.stringify({ type: "status", ib_connected: true, ib_issue: null, ib_status_message: null, subscriptions: message.symbols ?? [] }));
            socket.send(JSON.stringify({ type: "batch", updates: initialQuotes }));
          }
        });
      });
      await page.addInitScript(() => {
        window.__clearMetrics = { paints: [], lcp: [], shifts: [], longTasks: [], events: [], interactions: [] };
        new PerformanceObserver((list) => {
          window.__clearMetrics.paints.push(...list.getEntries().map((entry) => ({ name: entry.name, startTime: entry.startTime, duration: entry.duration })));
        }).observe({ type: "paint", buffered: true });
        new PerformanceObserver((list) => {
          for (const raw of list.getEntries()) {
            const entry = raw as PerformanceEntry & { element?: Element };
            window.__clearMetrics.lcp.push({ startTime: entry.startTime, duration: entry.duration, element: entry.element?.tagName + "." + entry.element?.getAttribute("class") });
          }
        }).observe({ type: "largest-contentful-paint", buffered: true });
        new PerformanceObserver((list) => {
          for (const raw of list.getEntries()) {
            const entry = raw as PerformanceEntry & { hadRecentInput: boolean; value: number; sources?: { node?: Element }[] };
            if (!entry.hadRecentInput) window.__clearMetrics.shifts.push({ startTime: entry.startTime, duration: entry.duration, value: entry.value, element: entry.sources?.map((source) => source.node?.tagName + "." + source.node?.getAttribute("class")).join(", ") });
          }
        }).observe({ type: "layout-shift", buffered: true });
        new PerformanceObserver((list) => {
          window.__clearMetrics.longTasks.push(...list.getEntries().map((entry) => ({ startTime: entry.startTime, duration: entry.duration })));
        }).observe({ type: "longtask", buffered: true });
        new PerformanceObserver((list) => {
          window.__clearMetrics.events.push(...list.getEntries().map((entry) => ({ name: entry.name, startTime: entry.startTime, duration: entry.duration })));
        }).observe({ type: "event", buffered: true, durationThreshold: 16 } as PerformanceObserverInit);
        document.addEventListener("click", (event) => {
          const target = event.target instanceof Element ? event.target.closest("button") : null;
          if (!target?.closest('[aria-label="Account history period"]')) return;
          const start = performance.now();
          requestAnimationFrame(() => requestAnimationFrame(() => window.__clearMetrics.interactions.push(performance.now() - start)));
        }, true);
      });
      const cdp = await context.newCDPSession(page);
      await cdp.send("Performance.enable");
      await cdp.send("Network.enable");
      await cdp.send("Network.setCacheDisabled", { cacheDisabled: true });
      await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 150, downloadThroughput: 1_600_000 / 8, uploadThroughput: 750_000 / 8, connectionType: "cellular4g" });
      await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
      const network = new Map<string, { type: string; url: string; bytes: number; start: number; end: number }>();
      cdp.on("Network.requestWillBeSent", (event) => network.set(event.requestId, { type: event.type ?? "Unknown", url: new URL(event.request.url).pathname, bytes: 0, start: event.timestamp, end: 0 }));
      cdp.on("Network.loadingFinished", (event) => { const item = network.get(event.requestId); if (item) { item.bytes = event.encodedDataLength; item.end = event.timestamp; } });
      await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
      console.log(`Performance checkpoint ${viewport.label}: document parsed`);
      const chart = page.getByRole("slider", { name: "Inspect account value history" });
      await expect(chart).toBeVisible({ timeout: 20_000 });
      const readiness = await cdp.send("Performance.getMetrics");
      const metric = (name: string) => readiness.metrics.find((entry) => entry.name === name)?.value ?? 0;
      const accountChartReadyMs = (metric("Timestamp") - metric("NavigationStart")) * 1000;
      console.log(`Performance checkpoint ${viewport.label}: account chart ready`);
      await page.evaluate(() => document.fonts.ready);
      // Polling and realtime transport deliberately continue after readiness.
      // Use a fixed, disclosed observation window rather than network-idle.
      await page.waitForTimeout(2000);
      // The fixture's clock substitutes getEntriesByName/getEntriesByType;
      // native PerformanceObserver and CDP retain actual browser timings.
      const cold = await page.evaluate(() => ({ ...window.__clearMetrics, fcp: window.__clearMetrics.paints.find((entry) => entry.name === "first-contentful-paint")?.startTime ?? null }));
      const coldNetwork = [...network.values()];
      console.log(JSON.stringify({ checkpoint: "cold-ready", viewport: viewport.label, fcpMs: cold.fcp, lcpMs: cold.lcp.at(-1)?.startTime, accountChartReadyMs, cls: clsWindow(cold.shifts), resources: coldNetwork.length }));
      const performanceReadsBefore = apiRequests.filter((request) => request === "GET /api/performance").length;
      const period = page.getByRole("button", { name: "1W", exact: true });
      await period.click();
      await expect(period).toHaveAttribute("aria-pressed", "true");
      await page.getByRole("button", { name: "All", exact: true }).click();
      await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
      const interactionMetrics = await page.evaluate(() => ({ interactions: window.__clearMetrics.interactions, events: window.__clearMetrics.events }));
      const socketCountBeforeNavigation = sockets.length;
      const routeStart = await page.evaluate(() => performance.now());
      await page.getByRole("region", { name: "Account value history" }).getByRole("link", { name: "Performance", exact: true }).click();
      await expect(page).toHaveURL(/\/performance$/);
      await expect(page.locator(".performance-chart-shell")).toBeVisible({ timeout: 45_000 });
      const routeDuration = await page.evaluate((start) => performance.now() - start, routeStart);
      const home = viewport.width <= 640 ? page.getByRole("navigation", { name: "Primary mobile navigation" }) : page.getByRole("navigation", { name: "Primary navigation", exact: true });
      await home.getByRole("link", { name: "Portfolio", exact: true }).click();
      await expect(chart).toBeVisible({ timeout: 45_000 });
      await expect(page.getByTestId("clear-account-value")).toContainText("1,246,820");
      const snapshot = {
        checkpoint: process.env.CLEAR_PERF_LABEL ?? "baseline", viewport: viewport.label, run,
        profile: { latencyMs: 150, downloadMbps: 1.6, uploadMbps: 0.75, cpuSlowdown: 4, cache: "cold", data: "browser-isolated representative fixtures", environment: "local production build; lab, not field" },
        fcpMs: cold.fcp, lcpMs: cold.lcp.at(-1)?.startTime ?? null, lcpElement: cold.lcp.at(-1)?.element, accountChartReadyMs,
        cls: clsWindow(cold.shifts), layoutShifts: cold.shifts,
        initialLongTaskCount: cold.longTasks.length, initialLongTaskMaxMs: Math.max(0, ...cold.longTasks.map((task) => task.duration)), initialBlockingTimeMs: cold.longTasks.reduce((sum, task) => sum + Math.max(0, task.duration - 50), 0),
        initialJsBytes: coldNetwork.filter((request) => request.type === "Script").reduce((sum, request) => sum + request.bytes, 0),
        initialCssBytes: coldNetwork.filter((request) => request.type === "Stylesheet").reduce((sum, request) => sum + request.bytes, 0),
        initialFontBytes: coldNetwork.filter((request) => request.type === "Font").reduce((sum, request) => sum + request.bytes, 0),
        initialRequests: coldNetwork.length, chartPeriodPaintMs: interactionMetrics.interactions,
        observedInteractionEventMaxMs: Math.max(0, ...interactionMetrics.events.map((entry) => entry.duration)),
        performanceNavigationMs: routeDuration, performanceReadsBefore, performanceReadsAfter: apiRequests.filter((request) => request === "GET /api/performance").length,
        socketCountBeforeNavigation, socketCountAfterNavigation: sockets.length, socketMessages,
        network: coldNetwork,
      };
      const path = testInfo.outputPath("performance.json");
      writeFileSync(path, JSON.stringify(snapshot, null, 2));
      await testInfo.attach("performance.json", { path, contentType: "application/json" });
      console.log(JSON.stringify({ ...snapshot, network: undefined, socketMessages: undefined, layoutShifts: undefined }));
      expect(performanceReadsBefore).toBe(1);
      expect(snapshot.performanceReadsAfter).toBe(3);
      expect(sockets.length).toBe(socketCountBeforeNavigation);
      expect(apiRequests.some((request) => /POST \/api\/orders\/(place|cancel|modify)/.test(request))).toBe(false);
    });
  }
}
