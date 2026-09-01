/**
 * E2E: profile Preferences + Credentials tabs (PR #125).
 *
 * Drives the real /profile page (authless harness) with stubbed APIs and
 * asserts the load-bearing behavior at the wire:
 *  - the folded-in tabs render for a non-demo session
 *  - the Credentials tab lists services with masked hints only
 *  - an armed save PUTs the full path + payload; a vendor 422 keeps the
 *    draft and shows the playful retry line
 */

import { test, expect, type Page } from "@playwright/test";

const CREDENTIALS_PAYLOAD = {
  groups: ["Market Data", "AI Providers"],
  services: [
    {
      id: "unusual_whales",
      label: "Unusual Whales",
      group: "Market Data",
      validator: true,
      slow: false,
      note: "",
      fields: [
        {
          name: "UW_TOKEN",
          label: "API token",
          secret: true,
          placeholder: "uw_...",
          configured: true,
          hint: "\u2022\u2022\u2022\u2022cret",
          version: 3,
          updated_at: "2026-09-01T00:00:00Z",
          updated_by: "op-1",
          env_fallback: false,
        },
      ],
    },
    {
      id: "anthropic",
      label: "Anthropic",
      group: "AI Providers",
      validator: true,
      slow: false,
      note: "",
      fields: [
        {
          name: "ANTHROPIC_API_KEY",
          label: "API key",
          secret: true,
          placeholder: "sk-ant-...",
          configured: false,
          hint: "",
          version: 0,
          updated_at: null,
          updated_by: null,
          env_fallback: false,
        },
      ],
    },
  ],
  generated_at: "2026-09-01T00:00:00Z",
};

const PREFERENCES_PAYLOAD = {
  preferences: [
    {
      key: "RADON_MAX_ORDER_QTY",
      label: "Max order quantity",
      group: "Order Limits",
      value_type: "int",
      value: 100,
      default: 100,
      hard_min: 1,
      hard_max: 500,
      unit: "contracts",
      description: "Ceiling on a single order's quantity.",
      applies_immediately: true,
      source: "default",
      db_rejected: false,
      updated_at: null,
      updated_by: null,
    },
  ],
  groups: ["Order Limits"],
  store: { available: true, error: null, checked_at: "2026-09-01T00:00:00Z" },
  generated_at: "2026-09-01T00:00:00Z",
};

async function stubProfileApis(page: Page): Promise<void> {
  await page.route("**/api/profile", (route) =>
    route.fulfill({ json: { username: "operator", avatar_url: null, ui_preferences: null } }),
  );
  await page.route("**/api/bookmarks", (route) => route.fulfill({ json: { bookmarks: [] } }));
  await page.route("**/api/watchlist", (route) => route.fulfill({ json: { watchlist: [] } }));
  await page.route("**/api/preferences", (route) =>
    route.fulfill({ json: PREFERENCES_PAYLOAD }),
  );
}

test.describe("profile operator tabs", () => {
  test("preferences fold-in and credentials masked list render", async ({ page }) => {
    await stubProfileApis(page);
    await page.route("**/api/credentials", (route) =>
      route.fulfill({ json: CREDENTIALS_PAYLOAD }),
    );

    await page.goto("/profile");
    await expect(page.getByRole("tab", { name: "Preferences" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Credentials" })).toBeVisible();

    await page.getByRole("tab", { name: "Preferences" }).click();
    await expect(page.getByTestId("preferences-section")).toBeVisible();
    await expect(page.getByText("Max order quantity")).toBeVisible();

    await page.getByRole("tab", { name: "Credentials" }).click();
    await expect(page.getByTestId("credentials-panel")).toBeVisible();
    await expect(page.getByTestId("credential-service-unusual-whales")).toBeVisible();
    // Masked hint renders; no plaintext anywhere.
    await expect(page.getByTestId("credential-status-UW_TOKEN")).toContainText("cret");
    await page.screenshot({ path: "test-results/profile-credentials-tab.png", fullPage: true });
  });

  test("armed save PUTs the full path and a 422 shows the playful retry line", async ({ page }) => {
    await stubProfileApis(page);
    await page.route("**/api/credentials", (route) =>
      route.fulfill({ json: CREDENTIALS_PAYLOAD }),
    );
    const putBodies: Array<{ url: string; payload: unknown }> = [];
    await page.route("**/api/credentials/anthropic", (route) => {
      putBodies.push({
        url: new URL(route.request().url()).pathname,
        payload: route.request().postDataJSON(),
      });
      return route.fulfill({
        status: 422,
        json: {
          detail: {
            code: "CREDENTIAL_REJECTED",
            service: "anthropic",
            status: "invalid",
            message: "Anthropic rejected the credential (HTTP 401)",
          },
        },
      });
    });

    await page.goto("/profile");
    await page.getByRole("tab", { name: "Credentials" }).click();

    const save = page.getByTestId("credential-save-anthropic");
    await expect(save).toBeDisabled();

    await page.locator("#cred-ANTHROPIC_API_KEY").fill("sk-ant-rejected");
    await expect(save).toBeEnabled();
    await save.click();

    await expect(page.getByTestId("credential-notice-anthropic")).toContainText(
      /absolutely not/i,
    );
    expect(putBodies).toHaveLength(1);
    expect(putBodies[0].url).toBe("/api/credentials/anthropic");
    expect(putBodies[0].payload).toEqual({
      values: { ANTHROPIC_API_KEY: "sk-ant-rejected" },
    });
    // The rejected draft stays for a retry.
    await expect(page.locator("#cred-ANTHROPIC_API_KEY")).toHaveValue("sk-ant-rejected");
    await page.screenshot({ path: "test-results/profile-credentials-rejection.png", fullPage: true });
  });
});
