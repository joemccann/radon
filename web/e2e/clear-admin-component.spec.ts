import { expect, test } from "@playwright/test";
import { build } from "esbuild";
import { fileURLToPath } from "node:url";
import { CLEAR_FIXTURE_TIME } from "./clear-fixtures";

// Exact production component, isolated browser document and fixture transport.
// This does not authenticate /admin or change its operator-only server gate.
// Health/service fixture shapes are shared with e2e/admin-panel.spec.ts.
const HEALTH_OK = {
  status: "ok",
  ib_gateway: {
    auth_state: "authenticated",
    port_listening: true,
    gateway_mode: "docker",
    host: "127.0.0.1",
    port: 4001,
    container_state: "running",
    container_health: "healthy",
    restart_backoff: {
      attempt_count: 0,
      last_attempt_at: 0,
      next_attempt_after: 0,
      next_attempt_in_secs: 0,
      last_outcome: null,
      push_lock: null,
    },
  },
  ib_pool: {
    sync: { connected: true, client_id: 3, managed_accounts: ["U1234"] },
    orders: { connected: true, client_id: 4, managed_accounts: ["U1234"] },
    data: { connected: true, client_id: 5, managed_accounts: ["U1234"] },
  },
};
const SERVICES = {
  supported: true,
  units: [
    {
      unit: "radon-nextjs.service",
      load_state: "loaded",
      active_state: "active",
      sub_state: "running",
      description: "Next.js web app",
      can_control: true,
    },
    {
      unit: "radon-api.service",
      load_state: "loaded",
      active_state: "active",
      sub_state: "running",
      description: "Radon FastAPI",
      can_control: true,
    },
    {
      unit: "radon-ib-gateway.service",
      load_state: "loaded",
      active_state: "active",
      sub_state: "running",
      description: "IB Gateway container",
      can_control: true,
    },
    {
      unit: "radon-monitor.service",
      load_state: "loaded",
      active_state: "inactive",
      sub_state: "dead",
      description: "Monitor daemon",
      can_control: true,
    },
  ],
};
const EDGE_HEALTH = {
  reachable: true,
  service_health: {
    state: "ok",
    rows: [
      {
        service: "portfolio-sync",
        state: "ok",
        updated_at: "2026-06-24T18:00:00Z",
        last_attempt_finished_at: "2026-06-24T18:00:00Z",
        last_error: null,
      },
      {
        service: "cash-flow-sync",
        state: "error",
        updated_at: "2026-06-24T18:00:00Z",
        last_attempt_finished_at: "2026-06-24T18:00:00Z",
        last_error: "provider timeout",
      },
      {
        service: "llm-token-index",
        state: "warning",
        updated_at: "2026-06-24T18:00:00Z",
        last_attempt_finished_at: "2026-06-24T18:00:00Z",
        last_error: null,
      },
    ],
  },
};

let componentBundle = "";
test.beforeAll(async () => {
  const root = fileURLToPath(new URL("..", import.meta.url));
  const result = await build({
    stdin: { contents: 'import { createRoot } from "react-dom/client"; import AdminWorkspace from "./components/admin/AdminWorkspace"; createRoot(document.getElementById("clear-component-root")).render(<AdminWorkspace />);', resolveDir: root, loader: "tsx" },
    bundle: true, write: false, platform: "browser", format: "iife", jsx: "automatic",
    alias: { "@": root }, define: { "process.env.NODE_ENV": '"production"' },
  });
  componentBundle = result.outputFiles[0].text;
});

for (const viewport of [{ label: "desktop", width: 1440, height: 1000 }, { label: "mobile", width: 390, height: 844 }]) {
  test(`Clear admin actual component ${viewport.label}: loaded tables and safe confirmation`, async ({ page, request }, testInfo) => {
    await page.setViewportSize(viewport);
    await page.clock.setFixedTime(new Date(CLEAR_FIXTURE_TIME));
    const errors: string[] = [];
    const mutations: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (route.request().method() !== "GET") mutations.push(path);
      const payloads: Record<string, unknown> = {
        "/api/admin/health": HEALTH_OK,
        "/api/admin/services": SERVICES,
        "/api/admin/edge-health": { ...EDGE_HEALTH, service_health: { state: "ok", rows: EDGE_HEALTH.service_health.rows.map((row) => ({ ...row, updated_at: CLEAR_FIXTURE_TIME, last_attempt_finished_at: CLEAR_FIXTURE_TIME })) }, external_probe: { source: "sample", ok: 1, latency_ms: 142, checked_at: CLEAR_FIXTURE_TIME } },
        "/api/admin/reliability": { window_ms: 604800000, since: "2026-08-28T18:00:00.000Z", baseline: { "portfolio-sync": "ok" }, events: [{ service: "portfolio-sync", state: "ok", created_at: CLEAR_FIXTURE_TIME }] },
        "/api/admin/host-metrics": { window_ms: 3600000, since: "2026-09-04T17:00:00.000Z", rows: Array.from({ length: 6 }, (_, i) => ({ taken_at: new Date(Date.parse(CLEAR_FIXTURE_TIME) - (5 - i) * 60000).toISOString(), cpu_pct: 12 + i, mem_used_mb: 2600, mem_avail_mb: 5200, load1: 0.3, swap_used_mb: 0, loop_lag_ms: 3 })) },
        "/api/admin/slo": { window_ms: 604800000, since: "2026-08-28T18:00:00.000Z", rows: [{ run_at: CLEAR_FIXTURE_TIME, edge_ok: 1, user_path_ok: 1, freshness_ok: 1, tick_fresh: 1, scan_fresh: 1, latency_ms: 142 }] },
        "/api/admin/trading/status": { halted: false },
        "/api/admin/demo-users": { users: [] },
      };
      const body = payloads[path];
      await route.fulfill({ status: body === undefined ? 503 : 200, contentType: "application/json", body: JSON.stringify(body ?? { error: "No isolated component fixture" }) });
    });
    // Reuse the actual app's compiled CSS and generated self-hosted font class.
    const appResponse = await request.get("/kit");
    expect(appResponse.status()).toBe(200);
    const appHtml = await appResponse.text();
    const styles = [...appHtml.matchAll(/<link\b[^>]*rel="stylesheet"[^>]*>/g)].map((match) => match[0]).join("");
    expect(styles).not.toBe("");
    const htmlClass = appHtml.match(/<html[^>]*class="([^"]*)"/)?.[1] ?? "";
    const bodyClass = appHtml.match(/<body[^>]*class="([^"]*)"/)?.[1] ?? "";
    expect(bodyClass).toContain("radon-clear");
    await page.route("**/__clear-admin-component", (route) => route.fulfill({
      status: 200, contentType: "text/html",
      body: `<!doctype html><html lang="en" data-theme="light" class="${htmlClass}"><head><meta name="viewport" content="width=device-width,initial-scale=1">${styles}<title>Clear isolated admin component</title></head><body class="${bodyClass}"><div id="clear-component-root"></div></body></html>`,
    }));
    await page.goto("/__clear-admin-component");
    await page.addScriptTag({ content: componentBundle });
    await expect(page.getByTestId("admin-page")).toBeVisible();
    await expect(page.getByTestId("ib-auth-state")).toContainText("Authenticated");
    await expect(page.getByTestId("services-card")).toContainText("radon-api.service");
    await expect(page.getByTestId("writer-row-portfolio-sync")).toBeVisible();
    await expect(page.getByTestId("trading-halt-state")).toHaveText("Active");
    await page.evaluate(() => document.fonts.ready);
    const smallLabels = await page.locator(".admin-tile-label, .admin-tile-sub, .admin-kv dt").evaluateAll((labels) => labels
      .filter((label) => label.getBoundingClientRect().width > 0)
      .filter((label) => Number.parseFloat(getComputedStyle(label).fontSize) < 12)
      .map((label) => ({ text: label.textContent, className: label.className, fontSize: getComputedStyle(label).fontSize })));
    expect(smallLabels, "Operator status metadata must meet the 12px minimum").toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    await page.screenshot({ path: testInfo.outputPath(`${viewport.label}-admin-component.png`), fullPage: true });
    await page.getByTestId("gateway-power-button").click();
    await expect(page.getByTestId("admin-confirm")).toBeVisible();
    await expect(page.getByTestId("admin-confirm-action")).toBeDisabled();
    await page.getByTestId("admin-confirm-typed-input").fill("radon-ib-gateway.service");
    await expect(page.getByTestId("admin-confirm-action")).toBeEnabled();
    expect(mutations).toEqual([]);
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(page.getByTestId("admin-confirm")).toHaveCount(0);
    expect(errors).toEqual([]);
    expect(mutations).toEqual([]);
  });
}
