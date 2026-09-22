/**
 * Mobile toasts must sit above the tab bar, not over hero metrics.
 * Loads the toast rules from globals.css into a 393×852 page so placement
 * is proven without depending on the Next shell.
 */
import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const css = readFileSync(join(import.meta.dirname, "../app/globals.css"), "utf8");

function ruleBlock(selector: string): string {
  const start = css.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`missing ${selector}`);
  const end = css.indexOf("}", start);
  return css.slice(start, end + 1);
}

const toastCss = [
  ruleBlock(".toast-container"),
  ruleBlock(".toast"),
  ruleBlock(".toast-success"),
  ruleBlock(".toast-error"),
  ruleBlock(".toast-warning"),
  ruleBlock(".toast-message"),
  ruleBlock(".toast-close"),
  ruleBlock('body[data-mobile="true"] .toast-container'),
  ruleBlock('body[data-mobile="true"] .toast'),
  ruleBlock('body[data-mobile="true"] .toast button'),
].join("\n");

type Box = { x: number; y: number; width: number; height: number };

function boxesOverlap(a: Box, b: Box): boolean {
  return a.x < b.x + b.width && a.x + a.width > b.x && a.y < b.y + b.height && a.y + a.height > b.y;
}

test.describe("Mobile toast placement", () => {
  test.use({ viewport: { width: 393, height: 852 } });

  test("sits above the tab bar and leaves Total account readable", async ({ page }, testInfo) => {
    await page.setContent(`<!DOCTYPE html>
<html data-theme="dark">
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {
  --font-mono: "IBM Plex Mono", ui-monospace, SFMono-Regular, monospace;
  --border-dim: #2a3a34;
  --bg-canvas: #0c1210;
  --bg-panel: #101714;
  --text-primary: #edf4ef;
  --text-muted: #8a9a92;
  --positive: #05AD98;
  --negative: #e85d4c;
  --warning: #d4a017;
  --ease-out: cubic-bezier(0.25, 1, 0.5, 1);
  --safe-top: 0px;
  --safe-bottom: 0px;
  --safe-left: 0px;
  --safe-right: 0px;
  --mobile-app-bar-height: 56px;
  --mobile-tab-bar-height: 64px;
}
html, body { margin: 0; background: var(--bg-canvas); color: var(--text-primary); font-family: Inter, system-ui, sans-serif; }
body { min-height: 100dvh; }
.hero { padding: 76px 20px 0; }
.hero-label { font-size: 14px; color: var(--text-muted); }
.hero-value { font-size: 44px; font-weight: 600; letter-spacing: -0.03em; line-height: 1.1; margin-top: 8px; }
.tab-bar {
  position: fixed; left: 0; right: 0; bottom: 0;
  height: calc(var(--mobile-tab-bar-height) + var(--safe-bottom));
  background: var(--bg-panel);
  border-top: 1px solid var(--border-dim);
}
${toastCss}
</style>
</head>
<body data-mobile="true">
  <div class="hero">
    <div class="hero-label">Total account value</div>
    <div class="hero-value" data-testid="clear-account-value">$1,246,820.42</div>
  </div>
  <nav class="tab-bar" data-testid="mobile-tab-bar"></nav>
  <div class="toast-container" id="radon-toast-viewport" style="z-index:10002;max-height:var(--toast-viewport-max-height, calc(100dvh - 48px - var(--safe-bottom, 0px)));max-width:var(--toast-viewport-max-width, calc(100vw - 40px));overflow-y:auto">
    <div class="toast toast-success" role="status">
      <span class="toast-message">IB Gateway · uplink restored</span>
      <button type="button" class="toast-close" aria-label="Dismiss" onclick="this.closest('.toast')?.remove()">×</button>
    </div>
  </div>
</body>
</html>`);

    const toast = page.locator(".toast-container .toast.toast-success").first();
    await expect(toast).toBeVisible();
    await expect(toast).toContainText("IB Gateway · uplink restored");
    await expect(toast.locator(".toast-close")).toBeVisible();

    const toastBox = await toast.boundingBox();
    const heroBox = await page.getByTestId("clear-account-value").boundingBox();
    const labelBox = await page.getByText("Total account value").first().boundingBox();
    const tabBox = await page.getByTestId("mobile-tab-bar").boundingBox();
    expect(toastBox).toBeTruthy();
    expect(heroBox).toBeTruthy();
    expect(tabBox).toBeTruthy();

    expect(boxesOverlap(toastBox!, heroBox!)).toBe(false);
    if (labelBox) expect(boxesOverlap(toastBox!, labelBox)).toBe(false);
    expect(toastBox!.y).toBeGreaterThan(heroBox!.y + heroBox!.height);
    expect(toastBox!.y + toastBox!.height).toBeLessThanOrEqual(tabBox!.y + 1);

    const placement = await page.locator(".toast-container").evaluate((el) => {
      const style = getComputedStyle(el);
      return { left: style.left, right: style.right, bottom: style.bottom, top: style.top };
    });
    expect(parseFloat(placement.left)).toBeCloseTo(12, 0);
    expect(parseFloat(placement.right)).toBeCloseTo(12, 0);
    expect(parseFloat(placement.bottom)).toBeGreaterThanOrEqual(80);
    expect(parseFloat(placement.top)).toBeGreaterThan(400);

    const screenshotPath = testInfo.outputPath("mobile-toast-above-tab-bar.png");
    await page.screenshot({ path: screenshotPath, fullPage: false });
    if (process.env.ARTIFACT_DIR) {
      await page.screenshot({
        path: join(process.env.ARTIFACT_DIR, "mobile_toast_above_tab_bar.png"),
        fullPage: false,
      });
    }

    await toast.locator(".toast-close").click();
    await expect(page.locator(".toast-container .toast")).toHaveCount(0);
  });

  test("Try again stays clickable when a table cell sits under the toast", async ({ page }) => {
    await page.setContent(`<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {
  --font-mono: ui-monospace, SFMono-Regular, monospace;
  --border-dim: #2a3a34;
  --bg-canvas: #0c1210;
  --bg-panel: #101714;
  --text-primary: #edf4ef;
  --text-muted: #8a9a92;
  --positive: #05AD98;
  --negative: #e85d4c;
  --warning: #d4a017;
  --ease-out: cubic-bezier(0.25, 1, 0.5, 1);
  --safe-top: 0px;
  --safe-bottom: 0px;
  --mobile-app-bar-height: 56px;
  --mobile-tab-bar-height: 64px;
}
html, body { margin: 0; background: var(--bg-canvas); color: var(--text-primary); }
.underlay {
  position: fixed; left: 12px; right: 12px;
  bottom: calc(1rem + var(--mobile-tab-bar-height) + var(--safe-bottom));
  height: 120px; z-index: 1;
}
.btn-secondary { min-height: 44px; min-width: 88px; }
${toastCss}
</style>
</head>
<body data-mobile="true">
  <div class="underlay"><span>continue</span></div>
  <div class="toast-container">
    <div class="toast toast-error" role="alert">
      <div class="toast-message">
        This service is busy
        <button type="button" class="btn-secondary" id="retry">Try again</button>
      </div>
      <button type="button" class="toast-close" aria-label="Dismiss">×</button>
    </div>
  </div>
</body>
</html>`);
    await page.locator("#retry").evaluate((el) => {
      el.addEventListener("click", () => { el.dataset.hit = "1"; });
    });
    await page.getByRole("button", { name: /retry|try again/i }).click();
    await expect(page.locator("#retry")).toHaveAttribute("data-hit", "1");
  });
});
