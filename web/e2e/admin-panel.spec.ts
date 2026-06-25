/**
 * E2E: operator admin panel
 *
 * Verifies the happy path:
 *   1. /admin renders with the IB Gateway card + service control panel.
 *   2. Force-2FA Push opens the confirmation modal before firing.
 *   3. Clicking the confirm button POSTs to /api/admin/ib/restart.
 *   4. Push lock state from /health disables the Force button.
 *
 * Uses ``RADON_AUTHLESS_TEST=1`` (set by playwright.config.ts) so Clerk's
 * sign-in wall is bypassed for localhost. FastAPI is stubbed at the
 * Next.js route boundary so the test never reaches port 8321.
 */
import { expect, test } from "@playwright/test";

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

const HEALTH_PUSH_LOCKED = {
  ...HEALTH_OK,
  ib_gateway: {
    ...HEALTH_OK.ib_gateway,
    auth_state: "awaiting_2fa",
    restart_backoff: {
      attempt_count: 1,
      last_attempt_at: 0,
      next_attempt_after: 0,
      next_attempt_in_secs: 30,
      last_outcome: "awaiting_2fa",
      push_lock: {
        holder: "ib_watchdog",
        acquired_at: 0,
        expires_at: 0,
        remaining_secs: 45,
        reason: "watchdog_restart",
      },
    },
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

test.describe("admin panel", () => {
  test("renders status card + services list", async ({ page }) => {
    await page.route("**/api/admin/health", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(HEALTH_OK) }),
    );
    await page.route("**/api/admin/services", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(SERVICES) }),
    );

    await page.goto("/admin");

    await expect(page.getByTestId("admin-page")).toBeVisible();
    await expect(page.getByTestId("ib-auth-state")).toContainText("Authenticated");
    await expect(page.getByTestId("services-card")).toContainText("radon-api.service");
    await expect(page.getByTestId("ib-gateway-card")).toContainText("IB Gateway");
  });

  test("sorts Operator service and writer freshness tables from column headers", async ({ page }) => {
    await page.route("**/api/admin/health", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(HEALTH_OK) }),
    );
    await page.route("**/api/admin/services", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(SERVICES) }),
    );
    await page.route("**/api/admin/edge-health", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(EDGE_HEALTH) }),
    );
    await page.route("**/api/admin/reliability", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ events: [] }) }),
    );
    await page.route("**/api/admin/host-metrics", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ rows: [] }) }),
    );
    await page.route("**/api/admin/slo", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ rows: [] }) }),
    );

    await page.goto("/admin");

    const serviceRows = page.locator("[data-testid^='service-row-']");
    await expect(serviceRows).toHaveCount(3);
    await expect(serviceRows.nth(0)).toContainText("radon-nextjs.service");

    await page.getByRole("columnheader", { name: /unit/i }).click();
    await expect(serviceRows.nth(0)).toContainText("radon-api.service");
    await expect(serviceRows.nth(1)).toContainText("radon-monitor.service");
    await expect(serviceRows.nth(2)).toContainText("radon-nextjs.service");

    const writerRows = page.locator("[data-testid^='writer-row-']");
    await expect(writerRows).toHaveCount(3);
    await expect(writerRows.nth(0)).toContainText("cash-flow-sync");

    await page.getByRole("columnheader", { name: /state/i }).click();
    await expect(writerRows.nth(0)).toContainText("cash-flow-sync");
    await expect(writerRows.nth(1)).toContainText("portfolio-sync");
    await expect(writerRows.nth(2)).toContainText("llm-token-index");
  });

  test("Force 2FA opens confirm modal before firing /api/admin/ib/restart", async ({ page }) => {
    await page.route("**/api/admin/health", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(HEALTH_OK) }),
    );
    await page.route("**/api/admin/services", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(SERVICES) }),
    );

    let restartHits = 0;
    await page.route("**/api/admin/ib/restart", async (route) => {
      restartHits += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ restarted: true, authenticated: true }),
      });
    });

    await page.goto("/admin");
    await expect(page.getByTestId("force-2fa-button")).toBeVisible();

    // First click: opens confirmation, does NOT fire the POST.
    await page.getByTestId("force-2fa-button").click();
    await expect(page.getByTestId("admin-confirm")).toBeVisible();
    expect(restartHits).toBe(0);

    // Confirm: now the POST fires.
    await page.getByTestId("admin-confirm-action").click();
    await expect.poll(() => restartHits).toBe(1);
  });

  test("Stop-gateway confirm dialog copies the unit token to the clipboard", async ({
    page,
    context,
  }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.route("**/api/admin/health", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(HEALTH_OK) }),
    );
    await page.route("**/api/admin/services", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(SERVICES) }),
    );

    await page.goto("/admin");

    // Open the destructive Stop dialog for the gateway unit (gated by type-to-confirm).
    await page.getByTestId("gateway-power-button").click();
    await expect(page.getByTestId("admin-confirm")).toBeVisible();

    const copy = page.getByTestId("admin-confirm-copy");
    await expect(copy).toHaveText("Copy");
    await expect(copy).toHaveAttribute(
      "aria-label",
      "Copy radon-ib-gateway.service to clipboard",
    );

    await copy.click();
    await expect(copy).toHaveText("Copied");

    const clip = await page.evaluate(() => navigator.clipboard.readText());
    expect(clip).toBe("radon-ib-gateway.service");
  });

  test("Force 2FA button is disabled when push lock is held", async ({ page }) => {
    await page.route("**/api/admin/health", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(HEALTH_PUSH_LOCKED) }),
    );
    await page.route("**/api/admin/services", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(SERVICES) }),
    );

    await page.goto("/admin");

    const button = page.getByTestId("force-2fa-button");
    await expect(button).toBeDisabled();
    await expect(page.getByTestId("force-2fa-disabled-reason")).toContainText("ib_watchdog");
  });
});
