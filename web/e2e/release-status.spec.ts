import { test, expect } from "@playwright/test";
import { installClearFixtures } from "./clear-fixtures";

test.use({ extraHTTPHeaders: { "x-radon-authless-test": process.env.RADON_AUTHLESS_TEST_TOKEN ?? "clear-local-verification-20260905" } });

test("desktop header shows this build and does not offer Reload on a local bundle", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const requests = await installClearFixtures(page);
  await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
  const release = page.getByTestId("release-status").first();
  await expect(release).toBeVisible();
  await expect(release).toHaveAttribute("data-state", "local");
  await expect(release.getByRole("button", { name: /local build/ })).toBeVisible();
  await release.getByRole("button").click();
  await expect(release.getByText("Local build")).toBeVisible();
  await expect(release.getByRole("button", { name: "Reload" })).toHaveCount(0);
  expect(requests.filter((request) => request === "GET /api/version")).toHaveLength(0);
});

test("mobile workspace menu shows the same local release", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installClearFixtures(page);
  await page.goto("/dashboard", { waitUntil: "domcontentloaded" });
  await page.getByTestId("mobile-tab-more").click();
  const release = page.getByTestId("mobile-more-drawer").getByTestId("release-status");
  await expect(release).toHaveAttribute("data-state", "local");
  await expect(release.getByText("Local build")).toBeVisible();
  await expect(release.getByRole("button", { name: "Reload" })).toHaveCount(0);
});
