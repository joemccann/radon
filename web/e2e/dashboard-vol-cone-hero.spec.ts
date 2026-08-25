import { expect, test } from "@playwright/test";

const HIT = {
  ticker: "NKE",
  spot: 72.4,
  expiry: "2026-09-18",
  month: "SEP",
  dte: 24,
  atm_iv: 0.281,
  call_10_iv: 0.302,
  put_10_iv: 0.314,
  call_10_strike: 79.64,
  put_10_strike: 65.16,
  p10: 0.276,
  p90: 0.463,
  atm_percentile: 0.04,
  call_10_percentile: 0.05,
  put_10_percentile: 0.07,
  wing_score: 0.06,
  regime: "CHEAP_WINGS",
  series: [],
};

const VOL_CONE = {
  scan_time: new Date().toISOString(),
  source_as_of: "2026-08-24",
  count: 118,
  hit_count: 3,
  current: HIT,
  names: [HIT],
  hits: [
    HIT,
    { ...HIT, ticker: "KO", spot: 71.2, atm_iv: 0.192, p10: 0.188, p90: 0.297, wing_score: 0.11 },
    {
      ...HIT,
      ticker: "MDLZ",
      spot: 63.8,
      expiry: "2026-10-16",
      month: "OCT",
      dte: 52,
      atm_iv: 0.229,
      p10: 0.201,
      p90: 0.341,
      atm_percentile: 0.12,
      wing_score: 0.38,
      regime: "CHEAP_ATM",
    },
  ],
};

test("dashboard signals panel lists vol cone candidates", async ({ page }) => {
  await page.route("**/api/vol-cone", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(VOL_CONE) }),
  );

  // The operator runs dark; verify the panel in the theme it ships in.
  await page.addInitScript(() => window.localStorage.setItem("theme", "dark"));

  await page.goto("/dashboard");
  const signals = page.getByTestId("dashboard-section-signals");
  await expect(signals).toBeVisible();

  await signals.getByRole("button", { name: "Vol Cone" }).click();

  const table = signals.getByRole("table", { name: "Vol cone candidates" });
  await expect(table).toBeVisible();
  await expect(table).toContainText("NKE");
  await expect(table).toContainText("$72 · SEP 18 · 24D");
  await expect(table).toContainText("28.1");
  await expect(table).toContainText("CHEAP WINGS");
  await expect(table).toContainText("CHEAP ATM");

  // The row opens the listed long strangle, not a bare ticker page.
  await expect(signals.getByLabel("Open NKE long 10% OTM strangle")).toHaveAttribute(
    "href",
    /src=vol-cone/,
  );

  // Nothing overflows the panel border at the dashboard's narrowest column.
  const overflow = await table.evaluate((el) => el.scrollWidth - el.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});
