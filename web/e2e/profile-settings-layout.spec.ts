import { expect, test, type Locator } from "@playwright/test";
import type { CredentialsPayload } from "../lib/credentials";
import type { PreferenceEntry, PreferencesPayload } from "../lib/preferences";
import { installClearFixtures } from "./clear-fixtures";
import { installClearRealtimeFixtures } from "./clear-realtime-fixtures";

const timestamp = "2026-09-04T18:00:00.000Z";
const definitions = [
  ["RADON_MAX_ORDER_QTY", "Max contracts per order", 2500, "Hard ceiling on contracts per options or combo order. The order is refused at the placement funnel before any IB call."],
  ["RADON_MAX_STOCK_QTY", "Max shares per stock order", 25000, "Hard ceiling on shares per stock order. Shares run larger than contracts, so this is separate from the contract cap."],
  ["RADON_MAX_ORDER_NOTIONAL", "Max order notional", 1000000, "Hard ceiling on dollar notional per order, computed as quantity times limit price times multiplier. The primary fat finger stop."],
  ["RADON_MAX_COMBO_LOSS", "Max combo worst-case loss", 10000000, "Hard ceiling on a combo order's worst-case loss: assignment for unpaired shorts, width for paired ones, net of premium. Separate from notional, which is what the order ticket pays or collects."],
  ["RADON_MAX_ORDERS_PER_MINUTE", "Max orders per minute", 10, "Accepted order placements allowed per rolling minute. The brake on a runaway automation loop."],
] as const;

function preferences(): PreferencesPayload {
  const entries: PreferenceEntry[] = definitions.map(([key, label, value, description]) => ({
    key, label, value, description, group: "Order Limits", value_type: "int", default: value,
    hard_min: 1, hard_max: 100000000, unit: "", applies_immediately: true,
    risk_gated: true, source: "db", db_rejected: false, updated_at: timestamp, updated_by: "sample-operator",
  }));
  entries.push({ ...entries[0], key: "RADON_SAMPLE_ENABLED", label: "Order safety checks", value_type: "bool", value: true, default: true, hard_min: false, hard_max: true, description: "Check every order against configured limits.", risk_gated: false });
  return { preferences: entries, groups: ["Order Limits"], generated_at: timestamp, store: { available: true, error: null, checked_at: timestamp } };
}

function credentials(): CredentialsPayload {
  return {
    groups: ["Market data"], generated_at: timestamp,
    services: [{
      id: "sample_vendor", label: "Sample market data", group: "Market data", validator: true, slow: false,
      note: "Credentials are stored securely. Enter a replacement to update a configured field.",
      fields: [
        { name: "SAMPLE_API_KEY", label: "API key", secret: true, configured: true, hint: "••••abcd", env_fallback: false },
        { name: "SAMPLE_ACCOUNT_EMAIL", label: "Account email", secret: false, configured: false, hint: "", env_fallback: true },
      ].map((field) => ({ ...field, placeholder: "Enter value", version: 1, updated_at: timestamp, updated_by: "sample-operator", exported_only: false })),
    }],
  };
}

async function box(locator: Locator) {
  const rect = await locator.boundingBox();
  expect(rect).not.toBeNull();
  return rect!;
}

for (const viewport of [
  { label: "desktop", width: 1440, height: 1000 },
  { label: "tablet", width: 834, height: 1112 },
  { label: "mobile", width: 390, height: 844 },
]) {
  for (const theme of ["light", "dark"] as const) {
    test.describe(`${viewport.label} ${theme} profile settings`, () => {
      test.use({ viewport, colorScheme: theme, reducedMotion: "reduce", serviceWorkers: "block" });

      test("keeps preference copy and credential controls readable and ordered", async ({ page }, testInfo) => {
        test.setTimeout(90000);
        const errors: string[] = [];
        page.on("pageerror", (error) => errors.push(error.message));
        const requests = await installClearFixtures(page);
        await installClearRealtimeFixtures(page);
        await page.addInitScript((value) => localStorage.setItem("theme", value), theme);
        const payload = preferences();
        const credentialPayload = credentials();
        const mutations: unknown[] = [];
        await page.route("**/api/preferences**", async (route) => {
          await route.fulfill({ json: payload });
        });
        await page.route("**/api/credentials**", async (route) => {
          if (route.request().method() === "PUT") {
            mutations.push(route.request().postDataJSON());
            await route.fulfill({ json: { service: credentialPayload.services[0], validation: { status: "valid", message: "Sample verification succeeded" } } });
          } else {
            await route.fulfill({ json: credentialPayload });
          }
        });

        await page.goto("/profile?tab=preferences");
        await expect(page.getByTestId("preference-input-RADON_MAX_ORDER_QTY")).toHaveValue("2500");
        await page.evaluate(() => document.fonts.ready);
        await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
        await page.screenshot({ path: testInfo.outputPath(`profile-settings-${viewport.label}-${theme}-preferences.png`), fullPage: true, animations: "disabled" });
        for (const [key, label] of definitions) {
          const row = page.getByTestId(`preference-row-${key}`);
          const labelBox = await box(row.locator(".preferences-row__label"));
          const descriptionBox = await box(row.locator(".preferences-row__description"));
          const inputBox = await box(page.getByTestId(`preference-input-${key}`));
          const saveBox = await box(page.getByTestId(`preference-save-${key}`));
          expect(descriptionBox.width, `${label}: description must not collapse to a skinny column`).toBeGreaterThan(viewport.width > 900 ? 300 : 240);
          expect(descriptionBox.y, `${label}: label belongs above description`).toBeGreaterThanOrEqual(labelBox.y + labelBox.height - 1);
          expect((await box(row)).height, `${label}: closed metadata keeps rows compact`).toBeLessThan(viewport.width > 900 ? 230 : 360);
          expect(inputBox.height).toBeGreaterThanOrEqual(40);
          expect(saveBox.height).toBeGreaterThanOrEqual(viewport.width <= 640 ? 44 : 40);
          expect(saveBox.y).toBeGreaterThanOrEqual(inputBox.y + inputBox.height - 1);
          if (viewport.width > 900) {
            expect(inputBox.x).toBeGreaterThanOrEqual(descriptionBox.x + descriptionBox.width);
            expect(labelBox.y).toBeLessThanOrEqual(inputBox.y + inputBox.height);
          } else {
            expect(inputBox.y).toBeGreaterThanOrEqual(descriptionBox.y + descriptionBox.height);
          }
          await expect(page.getByTestId(`preference-input-${key}`)).toHaveAccessibleName(label);
        }
        await expect(page.getByTestId("preference-input-RADON_SAMPLE_ENABLED")).toBeChecked();
        const firstRow = page.getByTestId("preference-row-RADON_MAX_ORDER_QTY");
        await firstRow.getByText("Limits and source", { exact: true }).click();
        await expect(page.getByTestId("preference-default-RADON_MAX_ORDER_QTY")).toBeVisible();
        await expect(page.getByTestId("preference-range-RADON_MAX_ORDER_QTY")).toBeVisible();
        await firstRow.getByText("Limits and source", { exact: true }).click();
        expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);

        await page.getByRole("tab", { name: viewport.width <= 640 ? "Keys" : "Credentials", exact: true }).click();
        const service = page.getByTestId("credential-service-sample-vendor");
        await expect(service).toBeVisible();
        const input = page.getByLabel("API key", { exact: true });
        await expect(input).toHaveValue("");
        await expect(input).toHaveAttribute("type", "password");
        await expect(page.getByTestId("credential-status-SAMPLE_API_KEY")).toContainText("abcd");
        for (const field of await service.locator(".credentials-field").all()) {
          const meta = await box(field.locator(".credentials-field__meta"));
          const control = await box(field.locator("input"));
          expect(control.width, "credential entry must retain a usable input width").toBeGreaterThanOrEqual(viewport.width > 900 ? 260 : 180);
          expect(control.height).toBeGreaterThanOrEqual(44);
          if (viewport.width > 900) expect(control.x).toBeGreaterThanOrEqual(meta.x + meta.width);
          else expect(control.y).toBeGreaterThanOrEqual(meta.y + meta.height - 1);
        }
        const save = page.getByTestId("credential-save-sample-vendor");
        await expect(save).toBeDisabled();
        await input.fill("synthetic-browser-key");
        await page.getByRole("button", { name: "Show API key", exact: true }).click();
        await expect(input).toHaveAttribute("type", "text");
        await page.getByRole("button", { name: "Hide API key", exact: true }).click();
        await expect(input).toHaveAttribute("type", "password");
        await expect(save).toBeEnabled();
        await save.click();
        await expect(input).toHaveValue("");
        await expect(save).toBeDisabled();
        expect(mutations).toEqual([{ values: { SAMPLE_API_KEY: "synthetic-browser-key" } }]);
        expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
        expect(requests.filter((request) => /\/api\/orders\/(place|cancel|modify)/.test(request))).toEqual([]);
        expect(errors).toEqual([]);
        await page.screenshot({ path: testInfo.outputPath(`profile-settings-${viewport.label}-${theme}-credentials.png`), fullPage: true, animations: "disabled" });
      });
    });
  }
}
