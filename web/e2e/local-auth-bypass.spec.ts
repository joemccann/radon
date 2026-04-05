import { test, expect } from "@playwright/test";

test("local dev protected pages stay on localhost when web auth bypass is active", async ({ page }) => {
  await page.goto("http://127.0.0.1:3000/kit");
  await page.waitForLoadState("domcontentloaded");

  await expect(page).toHaveURL(/\/kit$/);
  await expect(page.locator("text=Radon Contributor Kit / Component Spec")).toBeVisible();
});

test("local sign-in route redirects back into the app when web auth bypass is active", async ({ page }) => {
  await page.goto("http://127.0.0.1:3000/sign-in");
  await page.waitForURL("http://127.0.0.1:3000/");

  expect(page.url()).toBe("http://127.0.0.1:3000/");
});
