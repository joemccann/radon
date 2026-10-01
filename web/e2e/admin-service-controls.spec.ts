/**
 * E2E: the Service controls modal on the app host, through the host control
 * socket (radon-control.service).
 *
 * Same harness as clear-admin-component.spec.ts: the exact production
 * <AdminWorkspace />, bundled into an isolated document with the app's CSS and
 * fixture transport for every /api route. It does not authenticate /admin.
 * Verifies real unit descriptions, uptimes and one status-source line, armed
 * controls, Stop disarmed on the panel-hosting radon-api, and that a confirmed
 * Restart puts exactly one POST on the full unit path.
 */
import { expect, test } from "@playwright/test";
import { build } from "esbuild";
import { fileURLToPath } from "node:url";
import { CLEAR_FIXTURE_TIME } from "./clear-fixtures";

const HEALTH_OK = {
  status: "ok",
  host_role: "app",
  ib_gateway: {
    auth_state: "authenticated",
    port_listening: true,
    gateway_mode: "cloud",
    host: "10.0.0.4",
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
  },
};

const HOUR = 3600;
const lastRun = new Date(Date.parse(CLEAR_FIXTURE_TIME) - 25 * 60 * 1000).toISOString();

const SERVICES = {
  supported: true,
  host_role: "app",
  status_source: "host-control",
  units: [
    {
      unit: "radon-ib-gateway.service",
      load_state: "remote",
      active_state: "active",
      sub_state: "running",
      description: "IB Gateway on broker",
      can_control: true,
    },
    {
      unit: "radon-api.service",
      load_state: "loaded",
      active_state: "active",
      sub_state: "running",
      description: "Radon FastAPI server",
      can_control: true,
      uptime_secs: 3 * HOUR + 12 * 60,
      allowed_actions: ["restart"],
    },
    {
      unit: "radon-nextjs.service",
      load_state: "loaded",
      active_state: "active",
      sub_state: "running",
      description: "Radon Next.js web app",
      can_control: true,
      uptime_secs: 3 * HOUR + 11 * 60,
      allowed_actions: ["restart"],
    },
    {
      unit: "radon-relay.service",
      load_state: "loaded",
      active_state: "active",
      sub_state: "running",
      description: "Radon IB realtime relay",
      can_control: true,
      uptime_secs: 26 * HOUR,
      allowed_actions: ["start", "stop", "restart"],
    },
    {
      unit: "radon-health.service",
      load_state: "loaded",
      active_state: "active",
      sub_state: "running",
      description: "Radon standalone health daemon (isolated from the trading stack)",
      can_control: true,
      uptime_secs: 5 * 24 * HOUR,
      allowed_actions: ["start", "stop", "restart"],
    },
    {
      unit: "radon-cor.timer",
      load_state: "loaded",
      active_state: "active",
      sub_state: "waiting",
      description: "COR1M daily refresh",
      can_control: true,
      last_active_at: lastRun,
      allowed_actions: ["start", "stop", "restart"],
    },
  ],
};

let componentBundle = "";
let componentCss = "";
test.beforeAll(async () => {
  const root = fileURLToPath(new URL("..", import.meta.url));
  const result = await build({
    stdin: { contents: 'import { createRoot } from "react-dom/client"; import AdminWorkspace from "./components/admin/AdminWorkspace"; createRoot(document.getElementById("clear-component-root")).render(<AdminWorkspace />);', resolveDir: root, loader: "tsx" },
    outfile: "admin-component.js", bundle: true, write: false, platform: "browser", format: "iife", jsx: "automatic",
    alias: { "@": root }, define: { "process.env.NODE_ENV": '"production"' },
  });
  componentBundle = result.outputFiles.find((file) => file.path.endsWith(".js"))!.text;
  componentCss = result.outputFiles.filter((file) => file.path.endsWith(".css")).map((file) => file.text).join("\n");
});

test("app host service controls run through the host control socket", async ({ page, request }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.clock.setFixedTime(new Date(CLEAR_FIXTURE_TIME));
  const errors: string[] = [];
  const posts: { url: string; method: string }[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== "GET") {
      posts.push({ url: path, method: route.request().method() });
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ unit: "radon-relay.service", action: "restart", ok: true, detail: "restart completed", returncode: 0 }),
      });
    }
    const payloads: Record<string, unknown> = {
      "/api/admin/health": HEALTH_OK,
      "/api/admin/services": SERVICES,
      "/api/admin/edge-health": { reachable: true, service_health: { state: "ok", rows: [] } },
      "/api/admin/reliability": { window_ms: 604800000, since: CLEAR_FIXTURE_TIME, baseline: {}, events: [] },
      "/api/admin/host-metrics": { window_ms: 3600000, since: CLEAR_FIXTURE_TIME, rows: [] },
      "/api/admin/slo": { window_ms: 604800000, since: CLEAR_FIXTURE_TIME, rows: [] },
      "/api/admin/trading/status": { halted: false },
    };
    const body = payloads[path];
    await route.fulfill({ status: body === undefined ? 503 : 200, contentType: "application/json", body: JSON.stringify(body ?? { error: "No fixture" }) });
  });
  const appResponse = await request.get("/kit");
  expect(appResponse.status()).toBe(200);
  const appHtml = await appResponse.text();
  const styles = [...appHtml.matchAll(/<link\b[^>]*rel="stylesheet"[^>]*>/g)].map((match) => match[0]).join("");
  const htmlClass = appHtml.match(/<html[^>]*class="([^"]*)"/)?.[1] ?? "";
  const bodyClass = appHtml.match(/<body[^>]*class="([^"]*)"/)?.[1] ?? "";
  await page.route("**/__admin-service-controls", (route) => route.fulfill({
    status: 200, contentType: "text/html",
    body: `<!doctype html><html lang="en" data-theme="light" class="${htmlClass}"><head><meta name="viewport" content="width=device-width,initial-scale=1">${styles}<style>${componentCss}</style><title>Admin service controls</title></head><body class="${bodyClass}"><div id="clear-component-root"></div></body></html>`,
  }));
  await page.goto("/__admin-service-controls");
  await page.addScriptTag({ content: componentBundle });
  await expect(page.getByTestId("admin-page")).toBeVisible();
  await page.getByTestId("service-controls-button").click();

  const card = page.getByTestId("services-card");
  await expect(card).toContainText("Radon FastAPI server");
  await expect(card).toContainText("Radon IB realtime relay");
  await expect(page.getByTestId("services-status-source")).toHaveText(
    "Status and controls via the host control socket.",
  );
  await expect(page.getByTestId("service-activity-radon-api.service")).toHaveText("running 3h 12m");
  await expect(page.getByTestId("service-activity-radon-relay.service")).toHaveText("running 1d 2h");
  await expect(page.getByTestId("service-activity-radon-cor.timer")).toHaveText("last ran 25m ago");
  await expect(card).not.toContainText("never run");
  await expect(card).not.toContainText("host health daemon");

  await expect(page.getByTestId("service-restart-radon-relay.service")).toBeEnabled();
  await expect(page.getByTestId("service-stop-radon-relay.service")).toBeEnabled();
  await expect(page.getByTestId("service-restart-radon-api.service")).toBeEnabled();
  await expect(page.getByTestId("service-stop-radon-api.service")).toBeDisabled();

  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: test.info().outputPath("admin-service-controls.png") });

  expect(posts).toHaveLength(0);
  await page.getByTestId("service-restart-radon-relay.service").click();
  await expect(page.getByTestId("admin-confirm")).toBeVisible();
  expect(posts).toHaveLength(0);
  await page.getByTestId("admin-confirm-action").click();
  await expect.poll(() => posts.length).toBe(1);
  expect(posts[0]).toEqual({ url: "/api/admin/services/radon-relay.service/restart", method: "POST" });
  expect(errors).toEqual([]);
});
