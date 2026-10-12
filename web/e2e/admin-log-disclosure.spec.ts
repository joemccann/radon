/**
 * E2E: an ERROR or STALE writer row discloses the owning unit's journal.
 *
 * Same harness as admin-service-controls.spec.ts: the exact production
 * <AdminWorkspace />, bundled into an isolated document with the app's CSS and
 * fixture transport for every /api route. It does not authenticate /admin.
 * Verifies a healthy row stays inert, nothing hits /api/admin/logs before a
 * row is opened, the exact GET once it is, the raw last_error, and the
 * errors-only default.
 */
import { expect, test } from "@playwright/test";
import { build } from "esbuild";
import { fileURLToPath } from "node:url";
import { CLEAR_FIXTURE_TIME } from "./clear-fixtures";

const now = Date.parse(CLEAR_FIXTURE_TIME);
const ago = (secs: number) => new Date(now - secs * 1000).toISOString();

const ROWS = [
  {
    service: "dropbox-research",
    state: "error",
    updated_at: ago(60),
    last_attempt_started_at: ago(120),
    last_attempt_finished_at: ago(60),
    last_error: "ResearchIngestError: PDF parse failed for Q3-deck.pdf (pdfminer: unexpected EOF)",
  },
  {
    service: "bpi-scan",
    state: "ok",
    updated_at: ago(600),
    last_attempt_finished_at: ago(600),
    last_error: null,
  },
];

const LOGS = {
  service: "dropbox-research",
  unit: "radon-research.service",
  available: true,
  reason: null,
  truncated: false,
  entries: [
    { ts: ago(3600), priority: 6, message: "research worker idle, 0 queued" },
    { ts: ago(110), priority: 6, message: "ingest start: Q3-deck.pdf" },
    { ts: ago(90), priority: 4, message: "pdfminer: retrying page 14 after decode warning" },
    { ts: ago(61), priority: 3, message: "ResearchIngestError: PDF parse failed for Q3-deck.pdf (pdfminer: unexpected EOF)" },
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

test("an errored writer row discloses its unit journal on click", async ({ page, request }) => {
  await page.setViewportSize({ width: 1440, height: 1400 });
  await page.clock.setFixedTime(new Date(CLEAR_FIXTURE_TIME));
  const errors: string[] = [];
  const logRequests: { url: string; method: string }[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/admin/logs") {
      logRequests.push({ url: `${url.pathname}${url.search}`, method: route.request().method() });
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(LOGS) });
    }
    const payloads: Record<string, unknown> = {
      "/api/admin/services": { supported: true, host_role: "app", status_source: "host-control", units: [] },
      "/api/admin/edge-health": { reachable: true, service_health: { state: "error", rows: ROWS } },
      "/api/admin/reliability": { window_ms: 604800000, since: CLEAR_FIXTURE_TIME, baseline: {}, events: [] },
      "/api/admin/host-metrics": { window_ms: 3600000, since: CLEAR_FIXTURE_TIME, rows: [] },
      "/api/admin/slo": { window_ms: 604800000, since: CLEAR_FIXTURE_TIME, rows: [] },
      "/api/admin/trading/status": { halted: false },
    };
    const body = payloads[url.pathname];
    await route.fulfill({ status: body === undefined ? 503 : 200, contentType: "application/json", body: JSON.stringify(body ?? { error: "No fixture" }) });
  });
  const appResponse = await request.get("/kit");
  expect(appResponse.status()).toBe(200);
  const appHtml = await appResponse.text();
  const styles = [...appHtml.matchAll(/<link\b[^>]*rel="stylesheet"[^>]*>/g)].map((match) => match[0]).join("");
  const htmlClass = appHtml.match(/<html[^>]*class="([^"]*)"/)?.[1] ?? "";
  const bodyClass = appHtml.match(/<body[^>]*class="([^"]*)"/)?.[1] ?? "";
  await page.route("**/__admin-log-disclosure", (route) => route.fulfill({
    status: 200, contentType: "text/html",
    body: `<!doctype html><html lang="en" data-theme="dark" class="${htmlClass}"><head><meta name="viewport" content="width=device-width,initial-scale=1">${styles}<style>${componentCss}</style><title>Admin log disclosure</title></head><body class="${bodyClass}"><div id="clear-component-root"></div></body></html>`,
  }));
  await page.goto("/__admin-log-disclosure");
  await page.addScriptTag({ content: componentBundle });
  await expect(page.getByTestId("admin-page")).toBeVisible();

  const table = page.getByTestId("writer-freshness");
  for (const name of ["Services & writers", "Writer freshness"]) {
    const summary = page.locator("summary", { hasText: name });
    if ((await summary.locator("xpath=..").getAttribute("open")) === null) await summary.click();
  }
  await expect(table).toBeVisible();

  const healthy = page.getByTestId("writer-row-bpi-scan");
  await expect(healthy).not.toHaveAttribute("aria-expanded", /.*/);
  await expect(healthy.getByRole("button")).toHaveCount(0);

  const errored = page.getByTestId("writer-row-dropbox-research");
  await expect(errored).toHaveAttribute("aria-expanded", "false");
  expect(logRequests).toHaveLength(0);

  await errored.click();
  await expect(errored).toHaveAttribute("aria-expanded", "true");
  const panel = page.getByTestId("service-log-disclosure");
  await expect(panel).toBeVisible();
  await expect.poll(() => logRequests.length).toBe(1);
  expect(logRequests[0]).toEqual({ url: "/api/admin/logs?service=dropbox-research", method: "GET" });

  await expect(page.getByTestId("service-log-raw-error")).toHaveText(ROWS[0].last_error!);
  await expect(panel).toContainText("radon-research.service");
  await expect(panel).toContainText("pdfminer: retrying page 14");
  await expect(panel).not.toContainText("research worker idle");

  await page.evaluate(() => document.fonts.ready);
  await errored.scrollIntoViewIfNeeded();
  await page.screenshot({ path: test.info().outputPath("admin-log-disclosure.png"), fullPage: true });

  await errored.click();
  await expect(errored).toHaveAttribute("aria-expanded", "false");
  await expect(panel).toHaveCount(0);
  expect(errors).toEqual([]);
});
