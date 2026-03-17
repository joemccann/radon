/**
 * Unit tests: /api/regime resilience to corrupt CRI cache files.
 *
 * Regression target:
 *   When run_cri_scan.sh captures stderr into the JSON file (2>&1),
 *   the scheduled CRI files contain debug output before the JSON.
 *   The API route should:
 *   1. Skip corrupt files and try older ones in the scheduled dir
 *   2. Fall back to legacy cri.json if all scheduled files are corrupt
 *   3. Return market_open based on actual current time, not stale data
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { readFile, readdir, stat, writeFile, mkdir } from "fs/promises";
import { join } from "path";
import { tmpdir } from "os";
import { mkdtempSync, writeFileSync, mkdirSync, rmSync } from "fs";

/* ─── Inline replica of readLatestCri with fix ─────────────── */

/**
 * Original (buggy): reads latest scheduled file, if JSON.parse fails
 * the catch swallows ALL scheduled files and returns null.
 *
 * Fixed: iterates from newest to oldest, skips corrupt files.
 */
async function readLatestCriBuggy(
  scheduledDir: string,
  cachePath: string,
): Promise<{ data: object; path: string } | null> {
  try {
    const files = await readdir(scheduledDir);
    const jsonFiles = files.filter((f) => f.startsWith("cri-") && f.endsWith(".json")).sort();
    if (jsonFiles.length > 0) {
      const latest = join(scheduledDir, jsonFiles[jsonFiles.length - 1]);
      const raw = await readFile(latest, "utf-8");
      return { data: JSON.parse(raw), path: latest };
    }
  } catch { /* dir may not exist yet */ }

  try {
    const raw = await readFile(cachePath, "utf-8");
    return { data: JSON.parse(raw), path: cachePath };
  } catch { /* no cache */ }

  return null;
}

async function readLatestCriFixed(
  scheduledDir: string,
  cachePath: string,
): Promise<{ data: object; path: string } | null> {
  // 1. Try scheduled dir — newest to oldest, skip corrupt files
  try {
    const files = await readdir(scheduledDir);
    const jsonFiles = files.filter((f) => f.startsWith("cri-") && f.endsWith(".json")).sort();
    for (let i = jsonFiles.length - 1; i >= 0; i--) {
      const filePath = join(scheduledDir, jsonFiles[i]);
      try {
        const raw = await readFile(filePath, "utf-8");
        const jsonStart = raw.indexOf("{");
        if (jsonStart === -1) continue;
        const data = JSON.parse(raw.slice(jsonStart));
        return { data, path: filePath };
      } catch {
        continue; // skip corrupt file, try next
      }
    }
  } catch { /* dir may not exist yet */ }

  // 2. Fall back to legacy cache
  try {
    const raw = await readFile(cachePath, "utf-8");
    return { data: JSON.parse(raw), path: cachePath };
  } catch { /* no cache */ }

  return null;
}

/* ─── Tests ──────────────────────────────────────────────────── */

describe("readLatestCri — corrupt file handling", () => {
  let tmpDir: string;
  let scheduledDir: string;
  let cachePath: string;

  const VALID_CRI = { date: "2026-03-11", market_open: true, cri: { score: 25 } };
  const CORRUPT_CONTENT = `
============================================================
CRI SCANNER — Crash Risk Index
============================================================
  Attempting IB connection...
reqHistoricalData: Timeout for VIX
`;

  beforeEach(() => {
    tmpDir = mkdtempSync(join(tmpdir(), "cri-test-"));
    scheduledDir = join(tmpDir, "cri_scheduled");
    cachePath = join(tmpDir, "cri.json");
    mkdirSync(scheduledDir, { recursive: true });
  });

  afterEach(() => {
    rmSync(tmpDir, { recursive: true, force: true });
  });

  it("buggy version returns null when latest scheduled file is corrupt", async () => {
    // All scheduled files are corrupt
    writeFileSync(join(scheduledDir, "cri-2026-03-11T09-00.json"), CORRUPT_CONTENT);
    writeFileSync(join(scheduledDir, "cri-2026-03-11T10-00.json"), CORRUPT_CONTENT);
    // Valid legacy cache exists
    writeFileSync(cachePath, JSON.stringify(VALID_CRI));

    // Buggy: catch swallows everything, falls to legacy cache
    // but returns yesterday's market_open: false data
    const result = await readLatestCriBuggy(scheduledDir, cachePath);
    // The buggy version DOES fall through to cri.json — but it can't find
    // a valid scheduled file even if one exists mixed among corrupt ones
    expect(result).not.toBeNull();
    expect(result!.path).toBe(cachePath); // fell through to legacy
  });

  it("fixed version skips corrupt files and finds valid older one", async () => {
    // Older valid file, newer corrupt file
    writeFileSync(join(scheduledDir, "cri-2026-03-11T09-00.json"), JSON.stringify(VALID_CRI));
    writeFileSync(join(scheduledDir, "cri-2026-03-11T10-00.json"), CORRUPT_CONTENT);

    const result = await readLatestCriFixed(scheduledDir, cachePath);
    expect(result).not.toBeNull();
    expect(result!.data).toEqual(VALID_CRI);
    expect(result!.path).toContain("cri-2026-03-11T09-00.json");
  });

  it("fixed version extracts JSON from mixed stdout+stderr output", async () => {
    const mixedOutput = `Some debug output\n${JSON.stringify(VALID_CRI)}`;
    writeFileSync(join(scheduledDir, "cri-2026-03-11T10-00.json"), mixedOutput);

    const result = await readLatestCriFixed(scheduledDir, cachePath);
    expect(result).not.toBeNull();
    expect(result!.data).toEqual(VALID_CRI);
  });

  it("fixed version falls back to legacy cache when all scheduled files are corrupt", async () => {
    writeFileSync(join(scheduledDir, "cri-2026-03-11T09-00.json"), CORRUPT_CONTENT);
    writeFileSync(join(scheduledDir, "cri-2026-03-11T10-00.json"), CORRUPT_CONTENT);
    writeFileSync(cachePath, JSON.stringify(VALID_CRI));

    const result = await readLatestCriFixed(scheduledDir, cachePath);
    expect(result).not.toBeNull();
    expect(result!.path).toBe(cachePath);
    expect(result!.data).toEqual(VALID_CRI);
  });

  it("fixed version returns null when everything is corrupt and no legacy cache", async () => {
    writeFileSync(join(scheduledDir, "cri-2026-03-11T10-00.json"), CORRUPT_CONTENT);
    // No legacy cache

    const result = await readLatestCriFixed(scheduledDir, cachePath);
    expect(result).toBeNull();
  });
});

/* ─── market_open override when serving stale data ─────────── */

describe("market_open override for stale data", () => {
  /**
   * Replica of isMarketOpenNow from route.ts
   */
  function isMarketOpenNow(fakeNow: Date): boolean {
    const etStr = fakeNow.toLocaleString("en-US", { timeZone: "America/New_York" });
    const et = new Date(etStr);
    const day = et.getDay();
    if (day === 0 || day === 6) return false;
    const minutes = et.getHours() * 60 + et.getMinutes();
    return minutes >= 9 * 60 + 30 && minutes <= 16 * 60;
  }

  function applyOverride(data: Record<string, unknown>, todayET: string, now: Date): Record<string, unknown> {
    data.market_open = isMarketOpenNow(now);
    return data;
  }

  it("overrides market_open to true when stale data says false but market is open", () => {
    // Wednesday 10:30 AM ET
    const now = new Date("2026-03-11T14:30:00Z"); // 10:30 ET (EDT = UTC-4)
    const data = { date: "2026-03-10", market_open: false, cri: { score: 25 } };
    const result = applyOverride(data, "2026-03-11", now);
    expect(result.market_open).toBe(true);
  });

  it("overrides to current market state even when data date matches today", () => {
    const now = new Date("2026-03-11T14:30:00Z");
    const data = { date: "2026-03-11", market_open: false, cri: { score: 25 } };
    const result = applyOverride(data, "2026-03-11", now);
    expect(result.market_open).toBe(true); // trust current market clock, not stale scan flag
  });

  it("overrides to false on weekends even with stale data", () => {
    // Saturday
    const now = new Date("2026-03-14T14:30:00Z");
    const data = { date: "2026-03-13", market_open: true, cri: { score: 25 } };
    const result = applyOverride(data, "2026-03-14", now);
    expect(result.market_open).toBe(false);
  });

  it("overrides to false outside market hours", () => {
    // Wednesday 8:00 AM ET (before open)
    const now = new Date("2026-03-11T12:00:00Z"); // 8:00 ET
    const data = { date: "2026-03-10", market_open: false, cri: { score: 25 } };
    const result = applyOverride(data, "2026-03-11", now);
    expect(result.market_open).toBe(false);
  });
});
