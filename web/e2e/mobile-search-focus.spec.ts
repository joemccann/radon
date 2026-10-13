import { expect, test, type Page } from "@playwright/test";
import { installClearFixtures } from "./clear-fixtures";

async function installSearchFixtures(page: Page) {
  await installClearFixtures(page);
  await page.routeWebSocket(/(?:localhost|127\.0\.0\.1):8765|\/ws(?:\?|$)/, (socket) => {
    socket.send(JSON.stringify({ type: "status", ib_connected: true, market_data_degraded: false, subscriptions: [] }));
    socket.onMessage((raw) => {
      const message = JSON.parse(raw.toString());
      if (message.action !== "search") return;
      socket.send(JSON.stringify({
        type: "searchResults", pattern: message.pattern,
        results: [{ conId: 265598, symbol: "AAPL", secType: "STK", primaryExchange: "NASDAQ", currency: "USD", description: "Apple Inc." }],
      }));
    });
  });
  // Observe at window bubbling, after React handles its delegated document
  // click but before the trusted gesture finishes. An
  // eventual toBeFocused() assertion would accept the old delayed focus,
  // which cannot request the iOS keyboard from the original user gesture.
  // Headless WebKit verifies this event boundary, not a native iOS keyboard.
  await page.addInitScript(() => {
    const observations: Array<{ trusted: boolean; focused: boolean }> = [];
    Object.defineProperty(window, "__mobileSearchFocus", { value: observations });
    window.addEventListener("click", (event) => {
      if (!(event.target instanceof Element) || !event.target.closest('[data-testid="mobile-app-bar-search"]')) return;
      const input = document.querySelector('.mobile-search-input input');
      observations.push({ trusted: event.isTrusted, focused: input !== null && document.activeElement === input });
    });
  });
}

async function assertOpeningGestureFocus(page: Page, openingCount: number) {
  const observations = await page.evaluate(() => (window as unknown as {
    __mobileSearchFocus: Array<{ trusted: boolean; focused: boolean }>;
  }).__mobileSearchFocus);
  expect(observations).toHaveLength(openingCount);
  expect(observations.at(-1)).toEqual({ trusted: true, focused: true });
}

for (const width of [320, 393]) {
  test(`mobile search keeps touch focus, caret and result navigation at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 852 });
    await installSearchFixtures(page);
    await page.goto("/dashboard");
    const open = page.getByTestId("mobile-app-bar-search");
    await expect(open).toBeVisible();
    await open.tap();
    await assertOpeningGestureFocus(page, 1);

    const overlay = page.getByTestId("mobile-ticker-search");
    const input = overlay.getByRole("combobox", { name: "Search ticker", exact: true });
    await expect(input).toBeFocused();
    await expect(overlay).toBeVisible();
    expect(await input.evaluate((el) => parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(16);
    await input.evaluate((el) => (el as HTMLInputElement).blur());
    await expect(input).not.toBeFocused();
    await input.tap();
    await expect(input).toBeFocused();
    // Keyboard input, unlike locator.fill(), does not focus the input for us.
    await page.keyboard.type("aa");
    await expect(input).toHaveValue("AA");
    expect(await input.evaluate((el) => ({
      start: (el as HTMLInputElement).selectionStart,
      end: (el as HTMLInputElement).selectionEnd,
    }))).toEqual({ start: 2, end: 2 });
    await expect(overlay.getByRole("option")).toContainText("AAPL");
    // Synthetic Clear fixtures can emit unrelated provider/recovery toasts;
    // dismiss them so the visual artifact shows the input and result row.
    const dismiss = page.getByRole("button", { name: "Dismiss", exact: true });
    // Fixture toasts can auto-expire during actionability checks. Cleanup
    // their current dismiss controls directly; search interactions stay taps.
    await dismiss.evaluateAll((buttons) => {
      for (const button of buttons) (button as HTMLButtonElement).click();
    });
    await expect(dismiss).toHaveCount(0);
    await input.tap();
    await expect(input).toBeFocused();
    await expect(overlay.getByRole("option")).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath(`mobile-search-focus-${width}.png`), animations: "disabled" });
    await overlay.getByRole("option").tap();
    await expect(page).toHaveURL(/\/AAPL$/);
    await expect(overlay).toBeHidden();

    await open.tap();
    await assertOpeningGestureFocus(page, 2);
    await expect(input).toBeFocused();
    await page.getByTestId("mobile-ticker-search-close").tap();
    await expect(overlay).toBeHidden();
    await open.tap();
    await assertOpeningGestureFocus(page, 3);
    await expect(input).toBeFocused();
    await page.keyboard.type("a");
    await expect(input).toHaveValue("A");
    await page.keyboard.press("Escape");
    await expect(overlay).toBeHidden();
    expect(await page.evaluate(() => document.body.style.overflow)).not.toBe("hidden");
  });
}
