import { test, expect, type Page } from "@playwright/test";
import { installClearFixtures } from "./clear-fixtures";

const SERIES = Array.from({ length: 72 }, (_, index) => {
  const date = new Date(Date.UTC(2020, 9 + index, 1));
  const lead = new Date(
    Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 6, 1),
  );
  return {
    date: date.toISOString().slice(0, 7),
    lead_date: lead.toISOString().slice(0, 7),
    taiwan_exports_usd_mn: 40000 + index * 100,
    korea_exports_usd_mn: 50000 + index * 100,
    taiwan_yoy_pct: 10 + Math.sin(index / 4) * 8,
    korea_yoy_pct: 9 + Math.cos(index / 4) * 7,
    mean_yoy_pct: 10 + Math.sin(index / 4) * 6,
    ma3_yoy_pct: index < 2 || index === 40 ? null : 9 + Math.sin(index / 4) * 5,
    ma3_change_pp: index < 3 || index === 40 || index === 41 ? null : 0.5,
  };
});
const PAYLOAD = {
  missing: false,
  scan_time: "2026-10-19T09:40:00Z",
  source_last_modified: null,
  data_date: "2026-09-30",
  count: SERIES.length,
  series: SERIES,
  current: { ...SERIES.at(-1)!, growth: "expanding", momentum: "accelerating" },
  sources: {
    taiwan: {
      name: "Taiwan MOF",
      url: "https://data.gov.tw/en/datasets/8378",
      latest_month: "2026-09",
      last_modified: null,
    },
    korea: {
      name: "South Korea KCS",
      url: "https://www.tradedata.go.kr/cts/index_eng.do",
      latest_month: "2026-09",
      last_modified: null,
    },
  },
  comparison: {
    available: false,
    label: "MSCI US forward 12-month EPS",
    reason: "Licensed EPS series not connected",
  },
};

async function setup(
  page: Page,
  theme: "light" | "dark",
  payload: object = PAYLOAD,
) {
  await installClearFixtures(page);
  await page.clock.setFixedTime(new Date("2026-10-19T08:40:00Z"));
  await page.addInitScript(
    (value) => localStorage.setItem("theme", value),
    theme,
  );
  await page.route("**/api/asia-export-lead", (route) => {
    if (route.request().method() !== "GET") return route.abort();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });
  });
}

for (const theme of ["light", "dark"] as const) {
  for (const viewport of [
    { width: 1440, height: 1000 },
    { width: 393, height: 852 },
  ]) {
    test(`${theme} ${viewport.width}px: exports, calendar controls, brush and no overflow`, async ({
      page,
    }) => {
      await page.setViewportSize(viewport);
      await setup(page, theme);
      await page.goto("/regime/exports");
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      if (viewport.width > 640) {
        await expect(
          page.locator('.regime-rail__item[data-tab="exports"]'),
        ).toHaveClass(/active/);
      } else {
        await expect(
          page.getByRole("tab", { name: "EXPORTS", exact: true }),
        ).toHaveAttribute("aria-selected", "true");
        await expect(
          page.getByTestId("asia-export-lead-mobile-grid"),
        ).toBeVisible();
      }
      const chart = page.getByTestId("asia-export-lead-chart-section");
      await expect(chart).toBeVisible();
      await expect(chart.locator("svg path[stroke]").first()).toBeVisible();
      await expect(page.getByTestId("asia-export-lead-strip-asof")).toHaveText(
        "2026-09-30",
      );
      await expect(
        page.getByTestId("asia-export-lead-freshness-rail"),
      ).toContainText("Next check");
      await expect(
        page.getByTestId("asia-export-lead-freshness-rail-countdown"),
      ).toHaveText("1h 00m");
      await page.getByRole("button", { name: "1Y", exact: true }).click();
      await expect(chart).toHaveAttribute("data-range-start", "2025-10");
      await page
        .getByRole("button", { name: "Shift +6M", exact: true })
        .click();
      await expect(chart).toHaveAttribute("data-alignment", "shifted");
      await expect(chart).toContainText("Mar 2027");
      await expect(page.getByTestId("asia-export-lead-strip-asof")).toHaveText(
        "2026-09-30",
      );
      await page
        .getByRole("button", { name: "Countries", exact: true })
        .click();
      await expect(chart).toHaveAttribute("data-alignment", "observed");
      await expect(
        page.getByRole("button", { name: "Shift +6M", exact: true }),
      ).toBeDisabled();
      await expect(chart).toContainText("Taiwan YoY");
      await expect(chart).toContainText("South Korea YoY");
      const handle = page.getByTestId("asia-export-lead-brush-handle-left");
      await handle.focus();
      await handle.press("ArrowRight");
      await expect(chart).toHaveAttribute("data-range-start", "2025-11");
      await expect(page.getByText("Custom", { exact: true })).toBeVisible();
      await expect(chart).toContainText(
        "MSCI US forward EPS comparison unavailable.",
      );
      await expect(chart).toContainText(
        "Six-month alignment is not an earnings forecast.",
      );
      expect(
        await chart
          .locator("svg path")
          .evaluateAll((paths) =>
            paths.some((path) =>
              /NaN|Infinity/.test(path.getAttribute("d") ?? ""),
            ),
          ),
      ).toBe(false);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      ).toBe(true);
    });
  }
}

test("missing exports retain an honest measurement empty state", async ({
  page,
}) => {
  await setup(page, "light", {
    ...PAYLOAD,
    missing: true,
    current: null,
    count: 0,
    series: [],
  });
  await page.goto("/regime/exports");
  await expect(
    page.getByText("No export data yet", { exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("asia-export-lead-chart-section")).toHaveCount(
    0,
  );
});
