import { expect, test, type Page, type APIRequestContext } from "@playwright/test";
import { build } from "esbuild";
import { fileURLToPath } from "node:url";

// Render the real operator workspace with its production CSS and mocked transport.
// The operator-only route gate stays intact and every mutation is intercepted.
const NOW = "2026-09-25T18:00:00.000Z";
const HEALTH = {
  status: "ok",
  ib_gateway: {
    auth_state: "authenticated", port_listening: true, gateway_mode: "docker",
    container_state: "running", container_health: "healthy", host: "127.0.0.1", port: 4001,
    restart_backoff: { attempt_count: 0, last_attempt_at: 0, next_attempt_after: 0, next_attempt_in_secs: 0, last_outcome: null, push_lock: null },
  },
  ib_pool: { sync: { connected: true, client_id: 3, managed_accounts: ["U1234"] } },
};
const unit = (name: string, running = true) => ({
  unit: name, load_state: "loaded", active_state: running ? "active" : "failed",
  sub_state: running ? "running" : "failed", description: name, can_control: true, uptime_secs: running ? 3600 : null,
});
const SERVICES = { supported: true, units: [unit("radon-api.service"), unit("radon-relay.service"), unit("radon-ib-gateway.service")] };
const EDGE = {
  reachable: true,
  service_health: { state: "ok", rows: [{ service: "portfolio-sync", state: "ok", updated_at: NOW, last_attempt_finished_at: NOW }] },
  external_probe: { source: "fixture", ok: 1, latency_ms: 142, checked_at: NOW },
};
let bundle = "";
let componentCss = "";
test.beforeAll(async () => {
  const root = fileURLToPath(new URL("..", import.meta.url));
  const result = await build({
    stdin: { contents: 'import { createRoot } from "react-dom/client"; import AdminWorkspace from "./components/admin/AdminWorkspace"; createRoot(document.getElementById("operator-root")).render(<AdminWorkspace />);', resolveDir: root, loader: "tsx" },
    outfile: "operator.js", bundle: true, write: false, platform: "browser", format: "iife", jsx: "automatic",
    alias: { "@": root }, define: { "process.env.NODE_ENV": '"production"' },
  });
  bundle = result.outputFiles.find((file) => file.path.endsWith(".js"))!.text;
  componentCss = result.outputFiles.filter((file) => file.path.endsWith(".css")).map((file) => file.text).join("\n");
});

type Scenario = "attention" | "healthy" | "unavailable";
async function openOperator(page: Page, request: APIRequestContext, theme: "light" | "dark", scenario: Scenario) {
  await page.clock.setFixedTime(new Date(NOW));
  const mutations: { path: string; body: unknown }[] = [];
  let healthAvailable = scenario !== "unavailable";
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== "GET") {
      mutations.push({ path, body: route.request().postDataJSON() });
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, detail: "accepted", returncode: 0 }) });
    }
    if (path === "/api/admin/health" && !healthAvailable) {
      return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "Health unavailable" }) });
    }
    const payloads: Record<string, unknown> = {
      "/api/admin/health": scenario === "attention" ? { ...HEALTH, ib_gateway: { ...HEALTH.ib_gateway, auth_state: "unreachable", port_listening: false } } : HEALTH,
      "/api/admin/services": scenario === "attention" ? { ...SERVICES, units: SERVICES.units.map((row) => row.unit === "radon-api.service" || row.unit === "radon-ib-gateway.service" ? { ...unit(row.unit, false), active_state: "inactive", sub_state: "dead" } : row) } : SERVICES,
      "/api/admin/edge-health": scenario === "attention" ? { ...EDGE, service_health: { state: "error", rows: [...EDGE.service_health.rows, { service: "knowledge-ingest", state: "error", updated_at: NOW, last_error: "provider timeout" }] } } : EDGE,
      "/api/admin/reliability": { events: [], baseline: {} },
      "/api/admin/host-metrics": { rows: [] },
      "/api/admin/slo": { rows: [] },
      "/api/admin/trading/status": { halted: false },
      "/api/admin/demo-users": { users: [] },
    };
    await route.fulfill({ status: path in payloads ? 200 : 503, contentType: "application/json", body: JSON.stringify(payloads[path] ?? { error: "No fixture" }) });
  });
  const response = await request.get("/kit");
  expect(response.status()).toBe(200);
  const html = await response.text();
  const styles = [...html.matchAll(/<link\b[^>]*rel="stylesheet"[^>]*>/g)].map((match) => match[0]).join("");
  expect(styles).not.toBe("");
  const htmlClass = html.match(/<html[^>]*class="([^"]*)"/)?.[1] ?? "";
  const bodyClass = html.match(/<body[^>]*class="([^"]*)"/)?.[1] ?? "";
  await page.route("**/__operator-action-queue", (route) => route.fulfill({
    status: 200, contentType: "text/html",
    body: `<!doctype html><html lang="en" data-theme="${theme}" class="${htmlClass}"><head><meta name="viewport" content="width=device-width,initial-scale=1">${styles}<style>${componentCss}</style></head><body class="${bodyClass}"><div id="operator-root"></div></body></html>`,
  }));
  await page.goto("/__operator-action-queue");
  await page.addScriptTag({ content: bundle });
  await expect(page.getByTestId("admin-attention-queue")).toBeVisible();
  return { mutations, failHealth: () => { healthAvailable = false; } };
}

for (const theme of ["light", "dark"] as const) {
  for (const viewport of [{ label: "desktop", width: 1440, height: 900 }, { label: "mobile", width: 390, height: 752 }, { label: "small-mobile", width: 320, height: 752 }]) {
    test(`${theme} ${viewport.label}: priority action and trading entry precede diagnostics`, async ({ page, request }, testInfo) => {
      await page.setViewportSize(viewport);
      const { mutations } = await openOperator(page, request, theme, "attention");
      const queue = page.getByTestId("admin-attention-queue");
      await expect(queue).toContainText("radon-api.service");
      const primary = page.getByTestId("admin-primary-recovery");
      await expect(primary).toBeVisible();
      const action = primary;
      await expect(action).toBeEnabled();
      const box = await action.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height);
      expect(box!.height).toBeGreaterThanOrEqual(44);
      const trading = page.getByTestId("trading-controls-button");
      await expect(trading).toBeInViewport();
      for (const section of ["services", "writers", "reliability", "gateway", "host", "access"]) {
        await expect(page.getByTestId(`admin-disclosure-${section}`)).not.toHaveAttribute("open", "");
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({ path: testInfo.outputPath(`${theme}-${viewport.label}-attention-admin-component.png`), fullPage: true });
      expect(mutations).toEqual([]);
    });
  }
}

test("queue recovery preserves confirmation and exact gateway mutation; inventories remain sortable", async ({ page, request }) => {
  const { mutations } = await openOperator(page, request, "light", "attention");
  const action = page.getByTestId("admin-primary-recovery");
  await action.click();
  await expect(page.getByTestId("admin-confirm")).toBeVisible();
  expect(mutations).toEqual([]);
  await page.getByTestId("admin-confirm-action").click();
  await expect.poll(() => mutations.length).toBe(1);
  expect(mutations[0].path).toBe("/api/admin/services/radon-ib-gateway.service/start");
  await page.getByTestId("admin-disclosure-services").locator(":scope > summary").click();
  await expect(page.getByTestId("services-card")).toBeVisible();
  await page.getByTestId("services-card").getByRole("columnheader", { name: /unit/i }).click();
  await expect(page.locator("[data-testid^='service-row-']").first()).toContainText("radon-api.service");
  await page.getByTestId("admin-disclosure-writers").locator(":scope > summary").click();
  await expect(page.getByTestId("writer-row-knowledge-ingest")).toBeVisible();
  await expect(page.getByTestId("writer-row-portfolio-sync")).toBeVisible();
});

test("trading controls preserve typed kill confirmation and focus return", async ({ page, request }) => {
  const { mutations } = await openOperator(page, request, "dark", "healthy");
  const entry = page.getByTestId("trading-controls-button");
  await entry.click();
  const dialog = page.getByTestId("trading-controls-dialog");
  await expect(dialog).toBeVisible();
  await page.getByTestId("trading-kill-button").click();
  await expect(page.getByTestId("admin-confirm")).toContainText("including exit orders");
  await expect(page.getByTestId("admin-confirm-action")).toBeDisabled();
  await page.getByTestId("admin-confirm-typed-input").fill("KILL");
  await expect(page.getByTestId("admin-confirm-action")).toBeEnabled();
  expect(mutations).toEqual([]);
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("admin-confirm")).toHaveCount(0);
  // Closing the nested confirmation leaves the containing controls usable.
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(entry).toBeFocused();
  expect(mutations).toEqual([]);
});

test("unknown health remains visible despite successful services and edge polls", async ({ page, request }) => {
  await openOperator(page, request, "light", "unavailable");
  await expect(page.getByTestId("admin-attention-queue")).toContainText(/health|broker/i);
  await expect(page.getByTestId("admin-attention-queue")).toContainText(/unknown|unavailable|observation/i);
  await expect(page.getByTestId("admin-attention-queue")).not.toContainText("No action needed");
});

test("pending primary recovery prevents a duplicate mutation", async ({ page, request }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 752 });
  await openOperator(page, request, "light", "attention");
  let calls = 0;
  let finish!: () => void;
  const pending = new Promise<void>((resolve) => { finish = resolve; });
  await page.route("**/api/admin/services/radon-ib-gateway.service/start", async (route) => {
    calls += 1;
    await pending;
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, detail: "accepted", returncode: 0 }) });
  });
  try {
    await page.getByTestId("admin-primary-recovery").click();
    await page.getByTestId("admin-confirm-action").click();
    await expect.poll(() => calls).toBe(1);
    await expect(page.getByTestId("admin-confirm-action")).toBeDisabled();
    await expect(page.getByTestId("admin-primary-recovery")).toBeDisabled();
    await expect(page.getByTestId("admin-confirm-action")).toHaveText("Working...");
    await page.screenshot({ path: testInfo.outputPath("light-mobile-pending-admin-component.png"), fullPage: true });
    expect(calls).toBe(1);
  } finally {
    finish();
  }
  await expect(page.getByTestId("admin-confirm")).toHaveCount(0);
});

for (const theme of ["light", "dark"] as const) {
  test(`${theme}: healthy state keeps diagnostics accessible and source uncertainty distinct`, async ({ page, request }, testInfo) => {
    await page.setViewportSize({ width: 1280, height: 830 });
    const scenario = await openOperator(page, request, theme, "healthy");
    await expect(page.getByTestId("admin-attention-empty")).toContainText("No action needed");
    await expect(page.getByTestId("overview-broker")).toContainText("Authenticated");
    await page.screenshot({ path: testInfo.outputPath(`${theme}-desktop-healthy-admin-component.png`), fullPage: true });
    scenario.failHealth();
    await page.getByTestId("admin-status-summary").getByRole("button", { name: "Refresh status" }).click();
    await expect(page.getByTestId("overview-broker")).toContainText("Last known");
    await expect(page.getByTestId("overview-services")).toContainText("3 / 3 OK");
    await expect(page.getByTestId("admin-attention-source:health")).toContainText("Showing the last received values");
    await page.screenshot({ path: testInfo.outputPath(`${theme}-desktop-unavailable-admin-component.png`), fullPage: true });
  });
}
