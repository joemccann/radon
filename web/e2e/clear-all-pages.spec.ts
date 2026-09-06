import { expect, test } from "@playwright/test";
import { readdirSync, writeFileSync } from "node:fs";
import { resolve, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { CLEAR_ROUTE_CASES } from "./clear-route-inventory";
import { installClearFixtures } from "./clear-fixtures";
import { installClearRealtimeFixtures } from "./clear-realtime-fixtures";

test.use({ serviceWorkers: "block", colorScheme: "light", reducedMotion: "reduce" });

test("Clear inventory includes every App Router page exactly once", () => {
  const root = fileURLToPath(new URL("..", import.meta.url));
  const pages = readdirSync(resolve(root, "app"), { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name === "page.tsx")
    .map((entry) => relative(root, resolve(entry.parentPath, entry.name)).replaceAll("\\", "/"))
    .sort();
  expect(CLEAR_ROUTE_CASES.map((entry) => entry.source).sort()).toEqual(pages);
});

for (const viewport of [{ label: "desktop", width: 1440, height: 1000 }, { label: "mobile", width: 390, height: 844 }]) {
  test.describe(viewport.label, () => {
    test.use({ viewport });
    for (const entry of CLEAR_ROUTE_CASES.filter((route) => !route.guarded)) {
      test(`${entry.path} renders its page without overflow or runtime errors`, async ({ page }, testInfo) => {
        test.setTimeout(120_000);
        const errors: string[] = [];
        page.on("pageerror", (error) => errors.push(error.message));
        page.on("console", (message) => {
          if (message.type() === "error" && /hydration|hydrated|did not match|Minified React error/i.test(message.text())) errors.push(message.text());
        });
        const apiRequests = await installClearFixtures(page);
        const realtimeMessages = await installClearRealtimeFixtures(page);
        const response = await page.goto(entry.path, { waitUntil: "domcontentloaded" });
        expect(response?.status(), entry.source).toBe(200);
        if (entry.destination) await expect(page).toHaveURL(new RegExp(entry.destination.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "$"));
        if (entry.path === "/journal") {
          await page.getByTestId("journal-range-all").click();
          await expect(page.getByTestId("journal-range-all")).toHaveAttribute("aria-pressed", "true");
        }
        if (entry.selector) await expect(page.locator(entry.selector).filter({ visible: true }).first()).toBeVisible({ timeout: 30_000 });
        if (entry.text) await expect(page.getByText(entry.text, { exact: false }).filter({ visible: true }).first()).toBeVisible({ timeout: 30_000 });
        if (entry.path === "/AAPL?tab=book" && realtimeMessages.length > 0) {
          await expect(page.locator(".book-feed-pill")).toHaveText("SMART DEPTH");
          await expect(page.locator(".microstructure-strip")).toContainText("232.18");
          expect(realtimeMessages.some((raw) => JSON.parse(raw).action === "subscribe-depth")).toBe(true);
        }
        if (entry.path === "/profile") {
          await expect(page.locator(".profile-field__input").filter({ visible: true })).toHaveValue("Sample Operator");
        }
        if (entry.path === "/preferences") {
          await expect(page.getByTestId("preference-input-RADON_MAX_ORDER_QTY")).toHaveValue("400");
        }
        if (entry.regimeTab) {
          const tab = viewport.width <= 640
            ? page.locator('[role="tab"][aria-selected="true"]')
            : page.locator(`.regime-rail__item[data-tab="${entry.regimeTab}"]`);
          await expect(tab).toBeVisible({ timeout: 30_000 });
          if (viewport.width > 640) await expect(tab).toHaveClass(/active/);
          await expect(page.locator(".regime-detail-pane")).toBeVisible();
        }
        await page.evaluate(() => document.fonts.ready);
        const undersizedFinancialLabels = await page.locator(
          '.m-metric__label, .chart-panel-kicker, .regime-strip-label, .gex-level-label, [data-testid="cta-model-inputs"], [class*="__statsFootnote"], .watchlist-row__label, .orders-command-strip__value, .journal-range-chip, .m-chip, .m-segment__item, .scanner-mode-tab, .mobile-card__subtitle, .m-cta-section-header__label, .snapshot-card__error, [data-testid="options-workspace"] [role="tab"], .book-colhead > span, .regime-cta-gauge-scale > span, .grg-header-meta > span, .grg-chart-head > span, .grg-asset-grid > span, .grg-asset-grid strong, .grg-card strong, .gex-chart-legend, .gex-status-badge, .m-breadth-divergence-row > span, .act-flat > span, .microstructure-strip, .futures-delayed, .ticker-flow-refresh, .mobile-drawer__signout',
        ).evaluateAll((elements) => elements
          .filter((element) => element.getBoundingClientRect().width > 0)
          .filter((element) => Number.parseFloat(getComputedStyle(element).fontSize) < 12)
          .map((element) => ({ text: element.textContent?.trim().slice(0, 60), className: element.getAttribute("class"), fontSize: getComputedStyle(element).fontSize })));
        expect(undersizedFinancialLabels, "Financial context must meet Clear's 12px minimum").toEqual([]);
        await expect(page.locator("body")).not.toContainText(/Application error|Runtime Error|This page could not be found/);
        const horizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        expect(horizontalOverflow, `${entry.path} must contain wide tables/charts in local scroll regions`).toBeLessThanOrEqual(1);
        const readability = await page.evaluate(() => {
          const smallText = new Map<string, { tag: string; className: string; ancestry: string[]; fontSize: number; text: string }>();
          const clippedControls: Array<{ tag: string; className: string; text: string; left: number; right: number }> = [];
          for (const element of document.querySelectorAll("main *, .trial-expired-page *")) {
            const rect = element.getBoundingClientRect();
            const css = getComputedStyle(element);
            if (rect.width === 0 || rect.height === 0 || css.visibility === "hidden" || css.display === "none" || rect.bottom < 0 || rect.top > innerHeight) continue;
            const ownText = [...element.childNodes].filter((node) => node.nodeType === Node.TEXT_NODE).map((node) => node.textContent ?? "").join(" ").trim();
            const className = element.getAttribute("class") ?? "";
            const fontSize = Number.parseFloat(css.fontSize);
            if (ownText && fontSize < 12 && !element.closest('[aria-hidden="true"]')) {
              const ancestry = [element.parentElement, element.parentElement?.parentElement, element.parentElement?.parentElement?.parentElement]
                .filter((ancestor): ancestor is HTMLElement => Boolean(ancestor))
                .map((ancestor) => `${ancestor.tagName}.${ancestor.getAttribute("class") ?? ""}`);
              smallText.set(`${element.tagName}:${className}:${fontSize}`, { tag: element.tagName, className, ancestry, fontSize, text: ownText.slice(0, 70) });
            }
            if (element.matches("button,input,select,textarea,a[href]") && (rect.left < -1 || rect.right > innerWidth + 1)) {
              const scrollParent = [...function* () { let parent = element.parentElement; while (parent) { yield parent; parent = parent.parentElement; } }()].some((parent) => /auto|scroll/.test(getComputedStyle(parent).overflowX));
              if (!scrollParent) clippedControls.push({ tag: element.tagName, className, text: (element.getAttribute("aria-label") ?? element.textContent ?? "").trim().slice(0, 70), left: rect.left, right: rect.right });
            }
          }
          return { smallText: [...smallText.values()], clippedControls };
        });
        const readabilityPath = testInfo.outputPath("readability.json");
        writeFileSync(readabilityPath, JSON.stringify({ route: entry.path, viewport: viewport.label, ...readability }, null, 2));
        await testInfo.attach("readability.json", { path: readabilityPath, contentType: "application/json" });
        await testInfo.attach("realtime-transport", { body: JSON.stringify(realtimeMessages), contentType: "application/json" });
        expect(errors).toEqual([]);
        expect(apiRequests.filter((request) => /\/api\/orders\/(place|cancel|modify)$/.test(request))).toEqual([]);
        const slug = entry.source.replace(/^app\//, "").replace(/\/page\.tsx$/, "").replace(/[^a-z0-9-]/gi, "-");
        await page.screenshot({ path: testInfo.outputPath(`${viewport.label}-${slug}.png`), fullPage: true, animations: "disabled" });
      });
    }
  });
}
