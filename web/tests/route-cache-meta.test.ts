/**
 * TDD tests for cache_meta freshness metadata on GET (and POST) responses.
 *
 * Routes under test:
 *   - app/api/flow-analysis/route.ts
 *   - app/api/scanner/route.ts
 *   - app/api/discover/route.ts
 *
 * Run with:
 *   npx vitest run web/tests/route-cache-meta.test.ts
 *   (from the project root: /Users/joemccann/dev/apps/finance/convex-scavenger)
 */

import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

const STALE_THRESHOLD = 3600; // seconds; matches hourly flow-refresh cadence

function makeMtime(ageSeconds: number): Date {
  return new Date(Date.now() - ageSeconds * 1000);
}

// ---------------------------------------------------------------------------
// Mock setup — must happen before any route import
// ---------------------------------------------------------------------------

// We mock fs (sync statSync) and fs/promises so the routes never touch disk.
vi.mock("fs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("fs")>();
  return {
    ...actual,
    statSync: vi.fn(),
  };
});

vi.mock("fs/promises", () => ({
  readFile: vi.fn(),
  writeFile: vi.fn().mockResolvedValue(undefined),
  readdir: vi.fn(),
  stat: vi.fn(),
  mkdir: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("child_process", () => ({
  spawn: vi.fn(() => ({
    stdout: { on: vi.fn() },
    stderr: { on: vi.fn() },
    on: vi.fn(),
  })),
}));

// ---------------------------------------------------------------------------
// Import after mocks are registered
// ---------------------------------------------------------------------------

import * as fs from "fs";
import { readFile, writeFile } from "fs/promises";

// Lazy-import routes inside tests so mocks are in place
async function importFlowAnalysis() {
  // Re-import fresh each test via dynamic import with cache bust
  return import(`../app/api/flow-analysis/route?t=${Date.now()}`);
}
async function importScanner() {
  return import(`../app/api/scanner/route?t=${Date.now()}`);
}
async function importDiscover() {
  return import(`../app/api/discover/route?t=${Date.now()}`);
}

// Helper: call GET and extract JSON body
async function callGET(mod: { GET: () => Promise<Response> }) {
  const res = await mod.GET();
  return res.json();
}

// Helper: call POST and extract JSON body (for routes that accept POST)
async function callPOST(mod: { POST: () => Promise<Response> }) {
  const res = await mod.POST();
  return res.json();
}

// ---------------------------------------------------------------------------
// flow-analysis route
// ---------------------------------------------------------------------------

describe("GET /api/flow-analysis — cache_meta", () => {
  const validCacheData = JSON.stringify({
    analysis_time: "2026-03-09T14:00:00Z",
    positions_scanned: 5,
    supports: [],
    against: [],
    watch: [],
    neutral: [],
  });

  beforeEach(() => {
    vi.resetModules();
    vi.mocked(readFile).mockResolvedValue(validCacheData as unknown as string);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("returns age_seconds ≈ 30 and is_stale: false when file is 30s old", async () => {
    vi.mocked(fs.statSync).mockReturnValue({
      mtime: makeMtime(30),
    } as unknown as fs.Stats);

    const { GET } = await import("../app/api/flow-analysis/route");
    const body = await callGET({ GET });

    expect(body.cache_meta).toBeDefined();
    expect(body.cache_meta.last_refresh).not.toBeNull();
    expect(body.cache_meta.age_seconds).toBeGreaterThanOrEqual(28);
    expect(body.cache_meta.age_seconds).toBeLessThan(35);
    expect(body.cache_meta.is_stale).toBe(false);
    expect(body.cache_meta.stale_threshold_seconds).toBe(STALE_THRESHOLD);
  });

  it("returns is_stale: true when file is 3700s old", async () => {
    vi.mocked(fs.statSync).mockReturnValue({
      mtime: makeMtime(3700),
    } as unknown as fs.Stats);

    const { GET } = await import("../app/api/flow-analysis/route");
    const body = await callGET({ GET });

    expect(body.cache_meta.is_stale).toBe(true);
    expect(body.cache_meta.age_seconds).toBeGreaterThanOrEqual(3695);
  });

  it("returns last_refresh: null and is_stale: true when file not found (statSync throws)", async () => {
    vi.mocked(readFile).mockRejectedValue(new Error("ENOENT"));
    vi.mocked(fs.statSync).mockImplementation(() => {
      throw new Error("ENOENT: no such file");
    });

    const { GET } = await import("../app/api/flow-analysis/route");
    const body = await callGET({ GET });

    expect(body.cache_meta.last_refresh).toBeNull();
    expect(body.cache_meta.age_seconds).toBeNull();
    expect(body.cache_meta.is_stale).toBe(true);
    expect(body.cache_meta.stale_threshold_seconds).toBe(STALE_THRESHOLD);
  });
});

// ---------------------------------------------------------------------------
// scanner route
// ---------------------------------------------------------------------------

describe("GET /api/scanner — cache_meta", () => {
  const validCacheData = JSON.stringify({
    scan_time: "2026-03-09T14:00:00Z",
    tickers_scanned: 10,
    signals_found: 2,
    top_signals: [],
  });

  beforeEach(() => {
    vi.resetModules();
    vi.mocked(readFile).mockResolvedValue(validCacheData as unknown as string);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("returns age_seconds ≈ 30 and is_stale: false when file is 30s old", async () => {
    vi.mocked(fs.statSync).mockReturnValue({
      mtime: makeMtime(30),
    } as unknown as fs.Stats);

    const { GET } = await import("../app/api/scanner/route");
    const body = await callGET({ GET });

    expect(body.cache_meta).toBeDefined();
    expect(body.cache_meta.last_refresh).not.toBeNull();
    expect(body.cache_meta.age_seconds).toBeGreaterThanOrEqual(28);
    expect(body.cache_meta.age_seconds).toBeLessThan(35);
    expect(body.cache_meta.is_stale).toBe(false);
    expect(body.cache_meta.stale_threshold_seconds).toBe(STALE_THRESHOLD);
  });

  it("returns is_stale: true when file is 3700s old", async () => {
    vi.mocked(fs.statSync).mockReturnValue({
      mtime: makeMtime(3700),
    } as unknown as fs.Stats);

    const { GET } = await import("../app/api/scanner/route");
    const body = await callGET({ GET });

    expect(body.cache_meta.is_stale).toBe(true);
    expect(body.cache_meta.age_seconds).toBeGreaterThanOrEqual(3695);
  });

  it("returns last_refresh: null and is_stale: true when file not found (statSync throws)", async () => {
    vi.mocked(readFile).mockRejectedValue(new Error("ENOENT"));
    vi.mocked(fs.statSync).mockImplementation(() => {
      throw new Error("ENOENT: no such file");
    });

    const { GET } = await import("../app/api/scanner/route");
    const body = await callGET({ GET });

    expect(body.cache_meta.last_refresh).toBeNull();
    expect(body.cache_meta.age_seconds).toBeNull();
    expect(body.cache_meta.is_stale).toBe(true);
    expect(body.cache_meta.stale_threshold_seconds).toBe(STALE_THRESHOLD);
  });
});

// ---------------------------------------------------------------------------
// discover route
// ---------------------------------------------------------------------------

describe("GET /api/discover — cache_meta", () => {
  const validCacheData = JSON.stringify({
    discovery_time: "2026-03-09T14:00:00Z",
    alerts_analyzed: 20,
    candidates_found: 3,
    candidates: [],
  });

  beforeEach(() => {
    vi.resetModules();
    vi.mocked(readFile).mockResolvedValue(validCacheData as unknown as string);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("returns age_seconds ≈ 30 and is_stale: false when file is 30s old", async () => {
    vi.mocked(fs.statSync).mockReturnValue({
      mtime: makeMtime(30),
    } as unknown as fs.Stats);

    const { GET } = await import("../app/api/discover/route");
    const body = await callGET({ GET });

    expect(body.cache_meta).toBeDefined();
    expect(body.cache_meta.last_refresh).not.toBeNull();
    expect(body.cache_meta.age_seconds).toBeGreaterThanOrEqual(28);
    expect(body.cache_meta.age_seconds).toBeLessThan(35);
    expect(body.cache_meta.is_stale).toBe(false);
    expect(body.cache_meta.stale_threshold_seconds).toBe(STALE_THRESHOLD);
  });

  it("returns is_stale: true when file is 3700s old", async () => {
    vi.mocked(fs.statSync).mockReturnValue({
      mtime: makeMtime(3700),
    } as unknown as fs.Stats);

    const { GET } = await import("../app/api/discover/route");
    const body = await callGET({ GET });

    expect(body.cache_meta.is_stale).toBe(true);
    expect(body.cache_meta.age_seconds).toBeGreaterThanOrEqual(3695);
  });

  it("returns last_refresh: null and is_stale: true when file not found (statSync throws)", async () => {
    vi.mocked(readFile).mockRejectedValue(new Error("ENOENT"));
    vi.mocked(fs.statSync).mockImplementation(() => {
      throw new Error("ENOENT: no such file");
    });

    const { GET } = await import("../app/api/discover/route");
    const body = await callGET({ GET });

    expect(body.cache_meta.last_refresh).toBeNull();
    expect(body.cache_meta.age_seconds).toBeNull();
    expect(body.cache_meta.is_stale).toBe(true);
    expect(body.cache_meta.stale_threshold_seconds).toBe(STALE_THRESHOLD);
  });
});
