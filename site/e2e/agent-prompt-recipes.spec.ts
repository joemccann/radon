import { expect, test } from "../../web/node_modules/@playwright/test";

test.describe("Copy agent prompt and developer recipes", () => {
  test("shows Copy agent prompt on a dossier and seven recipe cards", async ({
    page,
  }) => {
    await page.goto("/crash-risk-index");
    const dossierCopy = page.getByRole("button", { name: "Copy agent prompt" });
    await expect(dossierCopy.first()).toBeVisible();
    await dossierCopy.first().click();
    await expect(page.getByRole("status")).toHaveText(/Copied|Copy failed/);

    await page.getByRole("button", { name: "View prompt" }).first().click();
    await expect(page.getByRole("dialog", { name: "Agent prompt" })).toBeVisible();
    await expect(page.getByRole("dialog")).toContainText("# Radon Terminal - Crash Risk Index");
    await page.getByRole("button", { name: "Close" }).click();

    await page.goto("/developers");
    await expect(page.getByRole("heading", { name: "Agent prompt payload" })).toBeVisible();
    await expect(page.getByText("Field order is stable")).toBeVisible();

    await page.goto("/developers/recipes");
    await expect(
      page.getByRole("heading", { name: "Radon Terminal developer recipes" }),
    ).toBeVisible();
    await expect(page.getByRole("heading", { name: "Score flow for TICKER" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Evaluate gates for a structure" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Read CRI regime" })).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Read GEX walls and magnets for TICKER" }),
    ).toBeVisible();
    await expect(page.getByRole("heading", { name: "List convex structures" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Size with fractional Kelly" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "What is Radon / when to use" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Copy agent prompt" })).toHaveCount(7);
  });
});
