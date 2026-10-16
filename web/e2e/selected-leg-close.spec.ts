import { expect, test } from "@playwright/test";
import { captureSelectedLegView, closePutSubset, coverSingleShortCall, installSelectedLegFixtures } from "./selected-leg-close-fixtures";

test.describe.configure({ timeout: 90_000 });

test("selecting only held puts closes the vertical without the financing call", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await closePutSubset(page, testInfo);
});

test("one selected short call is covered with a BUY option order", async ({ page }, testInfo) => {
  await coverSingleShortCall(page, testInfo);
});

test("mixed-basis selected close sums measured legs and scales partial realized P&L", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const fixture = await installSelectedLegFixtures(page, "mixed");
  await page.getByTestId("pos-leg-select-1").check();
  await page.getByTestId("pos-leg-select-2").check();
  await page.getByTestId("pos-trade-selected").click();
  const ticket = page.getByTestId("position-trade-ticket");
  await ticket.getByTestId("position-trade-qty").fill("25");
  await ticket.getByTestId("position-trade-limit").fill("8.10");
  await ticket.getByRole("button", { name: "Review Order", exact: true }).click();
  const summary = ticket.getByTestId("order-confirm-summary");
  // Retired basis = (69,969 - 11,969) * 25/100 = 14,500.
  // Close proceeds = 8.10 * 100 * 25 = 20,250; realized P&L = 5,750.
  await expect(summary).toContainText("Est. Realized P&L:");
  await expect(summary).toContainText("$5,750");
  await expect(ticket.getByTestId("position-trade-remaining-legs")).toContainText("270");
  expect(fixture.placements).toHaveLength(0);
  expect(fixture.pageErrors).toEqual([]);
  await captureSelectedLegView(page, testInfo, "mixed-basis-selected-close-review");
});

test("selection survives sorting and cancellation; deselection never places an order", async ({ page }) => {
  const fixture = await installSelectedLegFixtures(page);
  const shortPut = page.getByTestId("pos-leg-select-1");
  const longPut = page.getByTestId("pos-leg-select-2");
  await shortPut.check();
  await longPut.check();
  await page.locator(".pos-legs-table th").filter({ hasText: "Strike" }).click();
  await expect(shortPut).toBeChecked();
  await expect(longPut).toBeChecked();
  await expect(page.getByTestId("pos-leg-select-0")).not.toBeChecked();
  await page.getByTestId("pos-trade-selected").click();
  await page.getByRole("button", { name: "Cancel trade", exact: true }).click();
  await expect(shortPut).toBeChecked();
  await expect(longPut).toBeChecked();
  await longPut.uncheck();
  await expect(page.getByTestId("pos-trade-selected")).toHaveText("Close Selected Legs (1)");
  await shortPut.uncheck();
  await expect(page.getByTestId("pos-trade-selected")).toBeDisabled();
  const all = page.getByRole("checkbox", { name: "Select all option legs", exact: true });
  await all.check();
  await expect(page.getByTestId("pos-trade-selected")).toHaveText("Close Selected Legs (3)");
  await all.uncheck();
  await expect(page.getByTestId("pos-trade-selected")).toBeDisabled();
  expect(fixture.placements).toHaveLength(0);
  expect(fixture.pageErrors).toEqual([]);
});

test("selected close quantities reject fractional, zero and excess contracts", async ({ page }) => {
  const fixture = await installSelectedLegFixtures(page);
  await page.getByTestId("pos-leg-select-1").check();
  await page.getByTestId("pos-leg-select-2").check();
  await page.getByTestId("pos-trade-selected").click();
  const ticket = page.getByTestId("position-trade-ticket");
  await ticket.getByTestId("position-trade-limit").fill("8.10");
  const review = ticket.getByRole("button", { name: "Review Order", exact: true });
  for (const invalid of ["100.5", "101", "0", "-1", ""]) {
    await ticket.getByTestId("position-trade-qty").fill(invalid);
    await expect(review).toBeDisabled();
  }
  await ticket.getByTestId("position-trade-qty").fill("25");
  await expect(review).toBeEnabled();
  await review.click();
  await expect(ticket.getByTestId("position-trade-selected-legs")).toContainText("25");
  await expect(ticket.getByTestId("position-trade-remaining-legs")).toContainText("75");
  expect(fixture.placements).toHaveLength(0);
  expect(fixture.pageErrors).toEqual([]);
});

test("selecting a call spread leaves its financing put untouched", async ({ page }) => {
  const fixture = await installSelectedLegFixtures(page, "call");
  await page.getByRole("checkbox", { name: "Select SHORT Call $280", exact: true }).check();
  await page.getByRole("checkbox", { name: "Select LONG Call $250", exact: true }).check();
  await expect(page.getByTestId("pos-leg-select-0")).not.toBeChecked();
  await page.getByTestId("pos-trade-selected").click();
  const ticket = page.getByTestId("position-trade-ticket");
  await expect(ticket.getByTestId("position-trade-qty")).toHaveValue("100");
  await expect(ticket.getByTestId("position-trade-remaining-legs")).toContainText("230");
  await ticket.getByTestId("position-trade-limit").fill("8.10");
  await ticket.getByRole("button", { name: "Review Order", exact: true }).click();
  expect(fixture.placements).toHaveLength(0);
  await ticket.getByRole("button", { name: "Confirm Order", exact: true }).click();
  await expect.poll(() => fixture.placements.length).toBe(1);
  expect(fixture.placements[0].body).toEqual({
    type: "combo", symbol: "CRWD", action: "SELL", quantity: 100, limitPrice: 8.1, tif: "DAY",
    legs: [
      { expiry: "20261030", strike: 280, right: "C", action: "SELL", ratio: 1 },
      { expiry: "20261030", strike: 250, right: "C", action: "BUY", ratio: 1 },
    ],
  });
  expect(fixture.pageErrors).toEqual([]);
});
