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
  groups: ["Market Data", "AI Providers", "LLM Regime Sources"],
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
      id: "openrouter",
      label: "OpenRouter",
      group: "LLM Regime Sources",
      validator: true,
      slow: false,
      note: "",
      fields: [
        {
          name: "OPENROUTER_API_KEY",
          label: "Data API key",
          secret: true,
          placeholder: "sk-or-v1-...",
          configured: false,
          hint: "",
          version: 0,
          updated_at: null,
          updated_by: null,
          env_fallback: false,
          exported_only: false,
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
    {
      id: "equibles",
      label: "Equibles",
      group: "Market Data",
      validator: true,
      slow: false,
      note: "",
      fields: [
        {
          name: "EQUIBLES_API_KEY",
          label: "API key",
          secret: true,
          placeholder: "",
          configured: false,
          hint: "",
          version: 0,
          updated_at: null,
          updated_by: null,
          env_fallback: false,
        },
      ],
    },
    {
      id: "ib_flex",
      label: "IB Flex",
      group: "Market Data",
      validator: false,
      slow: false,
      note: "",
      fields: [
        {
          name: "IB_FLEX_TOKEN",
          label: "Flex token",
          secret: true,
          placeholder: "",
          configured: false,
          hint: "",
          version: 0,
          updated_at: null,
          updated_by: null,
          env_fallback: false,
        },
        {
          name: "IB_FLEX_QUERY_ID",
          label: "Blotter query id",
          secret: false,
          placeholder: "",
          configured: false,
          hint: "",
          version: 0,
          updated_at: null,
          updated_by: null,
          env_fallback: false,
        },
      ],
    },
    {
      id: "menthorq",
      label: "MenthorQ",
      group: "Market Data",
      validator: true,
      slow: true,
      note: "Checked with a real browser login. Expect up to a minute.",
      fields: [
        {
          name: "MENTHORQ_USER",
          label: "Email / username",
          secret: false,
          placeholder: "",
          configured: false,
          hint: "",
          version: 0,
          updated_at: null,
          updated_by: null,
          env_fallback: false,
        },
        {
          name: "MENTHORQ_PASS",
          label: "Password",
          secret: true,
          placeholder: "",
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
  test.describe.configure({ timeout: 90_000 });

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
    await expect(page.getByText("LLM Regime Sources")).toBeVisible();
    const openRouter = page.getByTestId("credential-service-openrouter");
    await expect(openRouter).toBeVisible();
    // Masked hint renders; no plaintext anywhere.
    await expect(page.getByTestId("credential-status-UW_TOKEN")).toContainText("cret");
    await openRouter.scrollIntoViewIfNeeded();
    await page.screenshot({ path: "test-results/profile-credentials-llm-desktop.png" });
    await page.setViewportSize({ width: 393, height: 852 });
    await openRouter.scrollIntoViewIfNeeded();
    const mobileInput = await page.locator("#cred-OPENROUTER_API_KEY").boundingBox();
    const mobileSave = await page.getByTestId("credential-save-openrouter").boundingBox();
    expect(mobileInput).not.toBeNull();
    expect(mobileSave).not.toBeNull();
    expect(mobileSave!.y).toBeGreaterThan(mobileInput!.y + mobileInput!.height);
    await expect
      .poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
      .toBe(true);
    await page.screenshot({ path: "test-results/profile-credentials-llm-mobile.png" });
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

  test("mobile Keys scrolls MenthorQ above the tab bar and BOOKMARKS is unclipped", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await stubProfileApis(page);
    await page.route("**/api/credentials", (route) =>
      route.fulfill({ json: CREDENTIALS_PAYLOAD }),
    );

    await page.goto("/profile?tab=credentials");
    await expect(page.locator(".profile-surface--mobile")).toBeVisible();
    await expect(page.getByTestId("profile-panel-scroll")).toBeVisible();
    await expect(page.getByTestId("credentials-panel")).toBeVisible();

    const bookmarks = page.locator(".profile-surface--mobile").getByRole("tab", { name: /bookmarks/i });
    await expect(bookmarks).toBeVisible();
    const tabMetrics = await bookmarks.evaluate((el) => {
      const rect = el.getBoundingClientRect();
      return {
        text: (el.textContent ?? "").replace(/\s+/g, " ").trim(),
        scrollWidth: el.scrollWidth,
        clientWidth: el.clientWidth,
        left: rect.left,
        textOverflow: getComputedStyle(el).textOverflow,
      };
    });
    expect(tabMetrics.text.startsWith("Bookmarks")).toBe(true);
    expect(tabMetrics.scrollWidth).toBeLessThanOrEqual(tabMetrics.clientWidth + 1);
    expect(tabMetrics.left).toBeGreaterThanOrEqual(0);
    expect(tabMetrics.textOverflow).not.toBe("ellipsis");
    await page.screenshot({ path: "test-results/profile-mobile-tabs-bookmarks.png" });

    const menthorq = page.getByTestId("credential-service-menthorq");
    await menthorq.evaluate((el) => el.scrollIntoView({ block: "end", inline: "nearest" }));
    await expect(menthorq).toBeVisible();
    await expect(page.locator("#cred-MENTHORQ_USER")).toBeVisible();
    await expect(page.locator("#cred-MENTHORQ_PASS")).toBeVisible();

    const mqBox = await menthorq.boundingBox();
    const tabBar = page.getByTestId("mobile-tab-bar");
    await expect(tabBar).toBeVisible();
    const barBox = await tabBar.boundingBox();
    expect(mqBox).not.toBeNull();
    expect(barBox).not.toBeNull();
    expect(mqBox!.y + mqBox!.height).toBeLessThanOrEqual(barBox!.y);
    await page.screenshot({ path: "test-results/profile-mobile-keys-menthorq.png" });
  });
});
