import type { Page } from "@playwright/test";

/**
 * A known US trading day (Wednesday). Specs that assert an IB daily P&L must
 * pin the browser clock to one: `isIbDailyPnlCurrent` gates the whole field on
 * `isUsTradingDay(etDate(now))` (`web/lib/ibDailyPnlSession.ts:18-20`), so any
 * spec running on a Saturday, Sunday or market holiday reads a nulled field.
 */
export const TRADING_DAY = "2026-08-26";

/** The instant the fixtures and the browser both treat as "now". */
export const TRADING_DAY_ISO = `${TRADING_DAY}T16:32:06Z`;

/** Freeze the page's `Date` to `TRADING_DAY_ISO` before any app code runs. */
export async function freezeToTradingDay(page: Page): Promise<void> {
  await page.addInitScript((iso) => {
    const fixedNow = new Date(iso).valueOf();
    const RealDate = Date;
    class MockDate extends RealDate {
      constructor(...args: ConstructorParameters<typeof Date>) {
        if (args.length === 0) { super(fixedNow); return; }
        super(...args);
      }
      static now() { return fixedNow; }
    }
    Object.defineProperty(window, "Date", { value: MockDate, configurable: true, writable: true });
  }, TRADING_DAY_ISO);
}
