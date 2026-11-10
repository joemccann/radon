import path from "path";
import { readFile, readdir, stat } from "fs/promises";
import { test, expect } from "@playwright/test";
import { selectPreferredCriCandidate, type CriCacheCandidate } from "../lib/criCache";

const DATA_DIR = path.join(process.cwd(), "..", "data");
const CACHE_PATH = path.join(DATA_DIR, "cri.json");
const SCHEDULED_DIR = path.join(DATA_DIR, "cri_scheduled");

const PORTFOLIO_EMPTY = {
  bankroll: 100_000,
  positions: [],
  account_summary: {},
  exposure: {},
  violations: [],
};

const ORDERS_EMPTY = {
  last_sync: new Date().toISOString(),
  open_orders: [],
  executed_orders: [],
  open_count: 0,
  executed_count: 0,
};

async function readCriCandidate(filePath: string): Promise<CriCacheCandidate | null> {
  try {
    const raw = await readFile(filePath, "utf-8");
    const jsonStart = raw.indexOf("{");
    if (jsonStart === -1) return null;
    const fileStat = await stat(filePath);
    return {
      path: filePath,
      mtimeMs: fileStat.mtimeMs,
      data: JSON.parse(raw.slice(jsonStart)) as Record<string, unknown>,
    };
  } catch {
    return null;
  }
}

async function readLatestCri(): Promise<Record<string, unknown>> {
  let scheduled: CriCacheCandidate | null = null;
  try {
    const files = await readdir(SCHEDULED_DIR);
    const jsonFiles = files.filter((file) => file.startsWith("cri-") && file.endsWith(".json")).sort();
    for (let index = jsonFiles.length - 1; index >= 0; index -= 1) {
      scheduled = await readCriCandidate(path.join(SCHEDULED_DIR, jsonFiles[index]));
      if (scheduled) break;
    }
  } catch {
    scheduled = null;
  }

  const selected = selectPreferredCriCandidate(scheduled, await readCriCandidate(CACHE_PATH));
  if (!selected) throw new Error("No readable CRI cache candidate found.");
  return selected.data as Record<string, unknown>;
}

async function setupNonRegimeMocks(page: import("@playwright/test").Page) {
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.route("**/api/portfolio", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PORTFOLIO_EMPTY),
    }),
  );
  await page.route("**/api/orders", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(ORDERS_EMPTY),
    }),
  );
  await page.route("**/api/prices", (route) => route.abort());
  await page.route("**/api/ib-status", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ connected: false }),
    }),
  );
  await page.route("**/api/blotter", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ as_of: new Date().toISOString(), summary: { realized_pnl: 0 }, closed_trades: [], open_trades: [] }),
    }),
  );
  await page.route("**/api/menthorq/cta", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ tables: [] }),
    }),
  );
}

test.describe("/regime page — live route COR1M cache", () => {
  test("renders the same COR1M value as the latest CRI cache file", async ({ page }) => {
    const cri = await readLatestCri();
    const cor1m = typeof cri.cor1m === "number" ? cri.cor1m : null;

    test.skip(cor1m == null, "Latest CRI cache does not contain a numeric COR1M value.");

    await setupNonRegimeMocks(page);
    await page.goto("/regime");

    const cor1mCell = page.locator('[data-testid="strip-cor1m"]');
    await cor1mCell.waitFor({ timeout: 10_000 });

    await expect(cor1mCell.locator(".regime-strip-value")).toHaveText(cor1m.toFixed(2));
  });
});
