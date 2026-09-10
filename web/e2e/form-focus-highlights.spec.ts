import { expect, test, type Locator, type Page } from "@playwright/test";
import { CLEAR_FIXTURE_TIME, installClearFixtures } from "./clear-fixtures";

test.use({ serviceWorkers: "block", reducedMotion: "reduce" });

const ORDER = {
  orderId: 95, permId: 653624857, symbol: "SNDK C1750",
  contract: { conId: 987654, symbol: "SNDK", secType: "OPT", strike: 1750, right: "C", expiry: "2026-09-25" },
  action: "BUY", orderType: "LMT", totalQuantity: 10, limitPrice: 50, auxPrice: null,
  status: "Submitted", filled: 0, remaining: 10, avgFillPrice: null, tif: "GTC",
};

async function expectCompositeFocus(input: Locator, wrapper: Locator, offset = "2px") {
  await expect(input).toBeFocused();
  expect(await input.evaluate((element) => element.matches(":focus-visible"))).toBe(true);
  await expect(input).toHaveCSS("outline-style", "none");
  await expect(input).toHaveCSS("box-shadow", "none");
  await expect(wrapper).toHaveCSS("outline-style", "solid");
  await expect(wrapper).toHaveCSS("outline-width", "2px");
  await expect(wrapper).toHaveCSS("outline-offset", offset);
  const focusColor = await wrapper.evaluate((element) => {
    const sample = document.createElement("span");
    sample.style.color = "var(--border-focus)";
    element.appendChild(sample);
    const color = getComputedStyle(sample).color;
    sample.remove();
    return color;
  });
  await expect(wrapper).toHaveCSS("outline-color", focusColor);
}

async function setTheme(page: Page, theme: "light" | "dark", mobile: boolean) {
  // installClearFixtures starts in light mode; exercise the real theme owner.
  if (theme === "dark") {
    if (mobile) {
      await page.getByRole("button", { name: "Open more navigation", exact: true }).click();
      await page.getByRole("button", { name: "Switch to dark theme", exact: true }).click();
      await page.keyboard.press("Escape");
    } else {
      await page.getByRole("button", { name: "Toggle theme", exact: true }).click();
    }
  }
  await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
}

for (const viewport of [
  { width: 1440, height: 1000, theme: "light", label: "desktop-light" },
  { width: 1440, height: 1000, theme: "dark", label: "desktop-dark" },
  { width: 390, height: 844, theme: "light", label: "mobile-light" },
  { width: 390, height: 844, theme: "dark", label: "mobile-dark" },
] as const) {
  test(`Modify Order focus encloses the currency field: ${viewport.label}`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport);
    const apiRequests = await installClearFixtures(page);
    await page.route("**/api/orders", (route) => route.fulfill({ json: {
      last_sync: CLEAR_FIXTURE_TIME, open_orders: [ORDER], executed_orders: [], open_count: 1, executed_count: 0,
    } }));
    await page.goto("/orders");
    const mobile = viewport.width <= 640;
    await setTheme(page, viewport.theme, mobile);

    if (mobile) {
      await page.getByTestId(`mobile-order-single-${ORDER.permId}`).click();
      await page.getByTestId("mobile-order-action-modify").click();
    } else {
      const row = page.locator("tbody tr").filter({ hasText: "SNDK" }).first();
      await row.getByRole("button", { name: "MODIFY", exact: true }).click();
    }

    const modal = page.locator(".modify-dialog");
    await expect(modal).toBeVisible();
    const quantity = modal.locator("#modify-quantity-input");
    const price = modal.locator("#modify-price-input");
    const wrapper = price.locator("..");
    await quantity.focus();
    const measureLayout = () => wrapper.evaluate((element) => {
      const row = element as HTMLElement;
      return { left: row.offsetLeft, top: row.offsetTop, width: row.offsetWidth, height: row.offsetHeight };
    });
    const before = await measureLayout();
    await page.keyboard.press("Tab");
    await expect(price).toBeFocused();
    await price.fill("50.00");
    await page.screenshot({ path: testInfo.outputPath(`focus-modify-${viewport.label}.png`), animations: "disabled" });
    await expectCompositeFocus(price, wrapper);
    expect(await measureLayout()).toEqual(before);
    const geometry = await wrapper.evaluate((element) => {
      const row = element.getBoundingClientRect();
      const prefix = element.querySelector(".modify-price-prefix")!.getBoundingClientRect();
      const field = element.closest(".modify-field")!.getBoundingClientRect();
      return { rowLeft: row.left, rowRight: row.right, prefixLeft: prefix.left, prefixRight: prefix.right, fieldLeft: field.left, fieldRight: field.right };
    });
    expect(geometry.prefixLeft).toBeGreaterThanOrEqual(geometry.rowLeft);
    expect(geometry.prefixRight).toBeLessThanOrEqual(geometry.rowRight);
    expect(geometry.rowLeft).toBeGreaterThanOrEqual(geometry.fieldLeft - 1);
    expect(geometry.rowRight).toBeLessThanOrEqual(geometry.fieldRight + 1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    // No broker mutation is necessary to verify keyboard presentation.
    expect(apiRequests.filter((request) => /POST \/api\/orders\/(place|modify|cancel)/.test(request))).toEqual([]);

    if (!mobile) {
      await page.keyboard.press("Escape");
      await expect(modal).not.toBeVisible();
      const search = page.getByPlaceholder("Filter orders...");
      await search.fill("SNDK");
      await page.keyboard.press("Tab");
      await page.keyboard.press("Shift+Tab");
      await expectCompositeFocus(search, search.locator(".."));
      await page.screenshot({ path: testInfo.outputPath(`focus-filter-${viewport.label}.png`), animations: "disabled" });
      await page.keyboard.press("Tab");
      const clear = search.locator("..").getByRole("button", { name: "Clear filter" });
      await expect(clear).toBeFocused();
      await expect(clear).toHaveCSS("outline-style", "solid");
      await expect(search.locator("..")).toHaveCSS("outline-style", "none");
      const ticker = page.getByRole("combobox").first();
      await ticker.focus();
      await expect(ticker).toHaveCSS("outline-style", "solid");
      await expect(ticker).toHaveCSS("outline-width", "2px");
    }
  });

  test(`Shared CSS retains standalone and composite keyboard focus: ${viewport.label}`, async ({ page, request }, testInfo) => {
    await page.setViewportSize(viewport);
    // An isolated markup matrix exercises the real compiled CSS, including
    // composite controls that are not all present on one app route.
    const response = await request.get("/kit");
    expect(response.status()).toBe(200);
    const html = await response.text();
    const styles = [...html.matchAll(/<link\b[^>]*rel="stylesheet"[^>]*>/g)].map((match) => match[0]).join("");
    expect(styles).not.toBe("");
    const htmlClass = html.match(/<html[^>]*class="([^"]*)"/)?.[1] ?? "";
    const bodyClass = html.match(/<body[^>]*class="([^"]*)"/)?.[1] ?? "";
    expect(bodyClass).toContain("radon-clear");
    await page.route("**/__form-focus-matrix", (route) => route.fulfill({
      contentType: "text/html",
      body: `<!doctype html><html lang="en" class="${htmlClass}" data-theme="${viewport.theme}"><head><meta name="viewport" content="width=device-width,initial-scale=1">${styles}<title>Form focus CSS matrix</title></head><body class="${bodyClass}"><main style="padding:24px;display:grid;gap:24px;max-width:640px">
        <h1>Form focus</h1>
        <button id="focus-start" type="button">Start keyboard traversal</button>
        <label>Standalone input <input id="standalone-input" value="50.00"></label>
        <label>Standalone select <select id="standalone-select"><option>GTC</option></select></label>
        <label>Standalone textarea <textarea id="standalone-textarea">Research note</textarea></label>
        <div class="theta-search"><input aria-label="Theta ticker" class="theta-search__input" value="SNDK"><button type="button" class="theta-search__button">Search</button></div>
        <div class="flow-ticker-input"><div class="flow-ticker-input-row"><span class="flow-ticker-input-icon">$</span><input aria-label="Flow ticker" value="SNDK"></div></div>
        <div class="command-palette-panel"><div class="command-palette-input-wrap"><input aria-label="Command search" class="command-palette-input" value="SNDK"><span class="command-palette-kbd">ESC</span></div></div>
        <div class="chat-panel"><div class="ask-composer"><div class="ask-composer__field"><textarea aria-label="Assistant message" class="ask-composer__input">Research note</textarea></div><div class="ask-composer__rail"><button type="button" class="ask-composer__attach">Attach</button></div></div></div>
      </main></body></html>`,
    }));
    await page.goto("/__form-focus-matrix");
    await page.locator("#focus-start").focus();
    for (const id of ["standalone-input", "standalone-select", "standalone-textarea"]) {
      await page.keyboard.press("Tab");
      const input = page.locator(`#${id}`);
      await expect(input).toBeFocused();
      await expect(input).toHaveCSS("outline-style", "solid");
      await expect(input).toHaveCSS("outline-width", "2px");
    }
    for (const [label, wrapperClass, offset] of [
      ["Theta ticker", ".theta-search", "2px"],
      ["Flow ticker", ".flow-ticker-input-row", "2px"],
      ["Command search", ".command-palette-input-wrap", "-2px"],
      ["Assistant message", ".ask-composer", "2px"],
    ]) {
      const input = page.getByLabel(label, { exact: true });
      // Enter keyboard modality before focusing each independent component.
      await page.keyboard.press("Tab");
      await input.focus();
      const wrapper = page.locator(wrapperClass);
      await expectCompositeFocus(input, wrapper, offset);
      await page.screenshot({
        path: testInfo.outputPath(`focus-matrix-${wrapperClass.slice(1)}-${viewport.label}.png`),
        fullPage: true,
        animations: "disabled",
      });
      if (label === "Theta ticker" || label === "Assistant message") {
        await page.keyboard.press("Tab");
        await expect(wrapper.getByRole("button")).toBeFocused();
        await expect(wrapper.getByRole("button")).toHaveCSS("outline-style", "solid");
        await expect(wrapper).toHaveCSS("outline-style", "none");
      }
    }
    await page.screenshot({ path: testInfo.outputPath(`focus-matrix-${viewport.label}.png`), fullPage: true, animations: "disabled" });
  });
}
