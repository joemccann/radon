import { test } from "@playwright/test";
import { closePutSubset, coverSingleShortCall } from "./selected-leg-close-fixtures";

test.describe.configure({ timeout: 90_000 });

test("mobile held-leg checkboxes close only the selected put spread", async ({ page }, testInfo) => {
  await closePutSubset(page, testInfo);
});

test("mobile selected short call uses buy to close with the puts retained", async ({ page }, testInfo) => {
  await coverSingleShortCall(page, testInfo);
});
