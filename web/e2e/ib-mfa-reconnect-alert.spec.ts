import { expect, test, type Page, type WebSocketRoute } from "@playwright/test";
import { installClearFixtures } from "./clear-fixtures";

const MFA_MESSAGE = "Interactive Brokers Gateway is reconnecting. Check the push notification from Interactive Brokers on your phone to approve MFA.";

async function setupStatus(page: Page, initialConnected: boolean, initialMfa = false) {
  await installClearFixtures(page);
  const sockets: WebSocketRoute[] = [];
  const frame = (connected: boolean, mfa = false) => JSON.stringify({ type: "status", ib_connected: connected, ib_issue: mfa ? "ibc_mfa_required" : null, ib_status_message: mfa ? MFA_MESSAGE : null, subscriptions: ["SPY", "VIX", "VVIX", "COR1M"] });
  let current = frame(initialConnected, initialMfa);
  await page.route("**/api/admin/health", route => route.fulfill({ json: { ib_gateway: { auth_state: initialMfa ? "awaiting_2fa" : null, service_state: null, upstream_dead: false } } }));
  await page.routeWebSocket(/\/ws(?:\?|$)|localhost:8765|127\.0\.0\.1:18765|localhost:8766/, socket => {
    sockets.push(socket);
    socket.onMessage(() => socket.send(current));
    socket.send(current);
  });
  return (connected: boolean, mfa = false) => { current = frame(connected, mfa); sockets.forEach(socket => socket.send(current)); };
}

test.describe("IB MFA reconnect alert", () => {
  test("initial MFA state is measured in footer telemetry without an upper banner", async ({ page }) => {
    await setupStatus(page, false, true);
    await page.goto("/regime/cri");
    await expect(page.getByRole("status", { name: "System telemetry" })).toContainText("Awaiting 2FA");
    await expect(page.getByTestId("ib-connection-banner")).toHaveCount(0);
  });

  test("initial generic disconnect renders footer Offline without an upper banner", async ({ page }) => {
    await setupStatus(page, false);
    await page.goto("/regime/cri");
    await expect(page.getByRole("status", { name: "System telemetry" })).toContainText("Offline");
    await expect(page.getByTestId("ib-connection-banner")).toHaveCount(0);
  });

  test("actual post-mount disconnect and reconnect emit uplink transitions", async ({ page }) => {
    const publish = await setupStatus(page, true);
    await page.goto("/regime/cri");
    await expect(page.getByRole("status", { name: "System telemetry" })).toContainText("Nominal");
    publish(false);
    await expect(page.getByRole("alert").filter({ hasText: "IB Gateway · uplink lost. Reconnect in progress." })).toBeVisible({ timeout: 10_000 });
    publish(true);
    await expect(page.getByRole("status").filter({ hasText: "IB Gateway · uplink restored" })).toBeVisible();
    await expect(page.getByRole("status", { name: "System telemetry" })).toContainText("Nominal");
    await expect(page.getByTestId("ib-connection-banner")).toHaveCount(0);
  });

  test("actual post-mount MFA disconnect retains phone approval guidance in a toast", async ({ page }) => {
    const publish = await setupStatus(page, true);
    await page.goto("/regime/cri");
    await expect(page.getByRole("status", { name: "System telemetry" })).toContainText("Nominal");
    publish(false, true);
    await expect(page.getByRole("status").filter({ hasText: MFA_MESSAGE })).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("ib-connection-banner")).toHaveCount(0);
  });
});
