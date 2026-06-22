import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";
import { fileURLToPath } from "url";

/**
 * Source-inspection tests confirming WorkspaceShell subscribes WATCHLIST symbols
 * to the IB real-time feed. Bug (2026-06-22): a watched symbol that was NOT also
 * a portfolio/order ticker (e.g. SPCX) was never in `allSymbols`, so usePrices
 * never subscribed it and the profile WATCHLIST row rendered "---" while MU (a
 * portfolio ticker) quoted. Index watchlist entries (VIX/SPX/…) must route via
 * the `indexes` channel, not `symbols`.
 *
 * Parses the component source rather than rendering it (the shell pulls in many
 * providers); mirrors regime-spy-subscription.test.ts.
 */
const TEST_DIR = fileURLToPath(new URL(".", import.meta.url));
const SHELL_PATH = join(TEST_DIR, "../components/WorkspaceShell.tsx");
const source = readFileSync(SHELL_PATH, "utf-8");

describe("WorkspaceShell — watchlist symbols are subscribed to live prices", () => {
  it("reads the watchlist via useWatchlist", () => {
    expect(source).toContain("useWatchlist()");
  });

  it("derives watchlist stock symbols (non-index) for the symbols channel", () => {
    expect(source).toContain("watchlistSymbols");
    expect(source).toMatch(/watchlist[\s\S]{0,80}filter\([\s\S]{0,40}!isIndexSymbol/);
  });

  it("folds watchlist stock symbols into allSymbols (so they reach usePrices)", () => {
    const base = source.match(/const base = \[[^\]]*\]/)?.[0] ?? "";
    expect(base).toContain("...watchlistSymbols");
    expect(source).toContain("symbols: allSymbols");
  });

  it("routes watchlist index symbols (VIX/SPX/…) onto the indexes channel", () => {
    expect(source).toContain("watchlistIndexes");
    // watchlistIndexes is merged into the de-duped allIndexes set.
    const allIndexesBlock = source.slice(
      source.indexOf("const allIndexes"),
      source.indexOf("const allIndexes") + 600,
    );
    expect(allIndexesBlock).toContain("...watchlistIndexes");
  });
});
