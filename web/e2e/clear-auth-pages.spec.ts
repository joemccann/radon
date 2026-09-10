import { expect, test } from "@playwright/test";

// These leaves need Clerk's real anonymous middleware context. The local
// authless header deliberately cannot impersonate an operator or a user.
test.describe("Clear public authentication and operator perimeter", () => {
  test.use({ extraHTTPHeaders: {}, serviceWorkers: "block", colorScheme: "light" });
  for (const viewport of [{ label: "desktop", width: 1440, height: 1000 }, { label: "mobile", width: 390, height: 844 }]) {
    for (const [path, label] of [["/sign-in", "Sign in"], ["/sign-up", "Create your account"]] as const) {
      test(`${viewport.label} ${path} renders the authentication form`, async ({ page }, testInfo) => {
        test.setTimeout(120_000);
        const errors: string[] = [];
        page.on("pageerror", (error) => errors.push(error.message));
        page.on("console", (message) => {
          if (message.type() === "error" && /hydration|did not match|Minified React error/i.test(message.text())) errors.push(message.text());
        });
        await page.setViewportSize(viewport);
        await page.goto(path);
        await expect(page.locator(".cl-rootBox")).toBeVisible({ timeout: 45_000 });
        await expect(page.getByText(label, { exact: false }).filter({ visible: true }).first()).toBeVisible();
        const email = page.getByRole("textbox", { name: "Email address", exact: true });
        await expect(email).toHaveCSS("font-size", "16px");
        await email.fill("clear-browser@example.invalid");
        await expect(email).toHaveValue("clear-browser@example.invalid");
        const submit = page.locator(".cl-formButtonPrimary");
        expect((await submit.boundingBox())?.height).toBeGreaterThanOrEqual(44);
        const signalColor = await page.evaluate(() => {
          const probe = document.createElement("span");
          probe.style.backgroundColor = "var(--signal-core)";
          document.body.append(probe);
          const color = getComputedStyle(probe).backgroundColor;
          probe.remove();
          return color;
        });
        await expect(submit).toHaveCSS("background-color", signalColor);
        await expect(submit).toHaveCSS("background-image", "none");
        await expect(page.locator(".cl-cardBox")).toHaveCSS("box-shadow", "none");
        expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
        expect(errors).toEqual([]);
        await page.screenshot({ path: testInfo.outputPath(`${viewport.label}-${path.slice(1)}.png`), fullPage: true });
      });
    }
    test(`${viewport.label} /admin does not expose the operator console anonymously`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await page.goto("/admin");
      await expect(page).toHaveURL(/\/sign-in/);
      await expect(page.getByTestId("admin-page")).toHaveCount(0);
    });
  }
});
