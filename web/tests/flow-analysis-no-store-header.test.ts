/**
 * Bug guard: /api/flow-analysis must return Cache-Control: no-store on every
 * response so browsers and intermediaries never cache a stale snapshot.
 *
 * The route already opts out of Next.js's static page cache via
 * `export const dynamic = "force-dynamic"`, but that only affects
 * server-side rendering — the response itself was missing a Cache-Control
 * header, which let Chrome / Safari serve the previous body until the user
 * hard-refreshed. Result: stale ghost positions (e.g. closed TSLA / AAOI /
 * GOOGL legs) kept appearing on /flow-analysis after the underlying journal
 * + flow_analysis_snapshots had already moved on.
 *
 * Sibling routes (portfolio, etc.) emit no-store via setNoStoreResponseHeaders;
 * flow-analysis must do the same on both GET and POST.
 */

import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";

vi.mock("fs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("fs")>();
  return { ...actual, statSync: vi.fn() };
});

vi.mock("fs/promises", () => ({
  readFile: vi.fn(),
  writeFile: vi.fn().mockResolvedValue(undefined),
  readdir: vi.fn(),
  stat: vi.fn(),
  mkdir: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("@/lib/db", () => ({ resetDb: () => {},
  getDb: () => ({
    execute: vi.fn().mockResolvedValue({ rows: [] }),
  }),
}));

vi.mock("@/lib/radonApi", () => ({
  radonFetch: vi.fn().mockResolvedValue({
    analysis_time: "2026-05-09T02:04:09.999Z",
    positions_scanned: 0,
    supports: [],
    against: [],
    watch: [],
    neutral: [],
  }),
}));

import * as fs from "fs";
import { readFile } from "fs/promises";

const validCachePayload = JSON.stringify({
  analysis_time: "2026-05-09T02:04:09.999Z",
  positions_scanned: 0,
  supports: [],
  against: [],
  watch: [],
  neutral: [],
});

function expectNoStore(res: Response): void {
  const cc = res.headers.get("Cache-Control") ?? "";
  expect(cc.toLowerCase()).toContain("no-store");
}

describe("/api/flow-analysis — Cache-Control: no-store", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.mocked(readFile).mockResolvedValue(validCachePayload as unknown as string);
    vi.mocked(fs.statSync).mockReturnValue({
      mtime: new Date(),
    } as unknown as fs.Stats);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("GET sets no-store when serving the disk-fallback cache file", async () => {
    const { GET } = await import("../app/api/flow-analysis/route");
    const res = await GET();
    expectNoStore(res);
  });

  it("GET sets no-store on the empty-fallback envelope when both DB and disk are empty", async () => {
    vi.mocked(readFile).mockRejectedValue(new Error("ENOENT"));
    vi.mocked(fs.statSync).mockImplementation(() => {
      throw new Error("ENOENT");
    });

    const { GET } = await import("../app/api/flow-analysis/route");
    const res = await GET();
    expectNoStore(res);
  });

  it("POST sets no-store on the FastAPI-passthrough response", async () => {
    const { POST } = await import("../app/api/flow-analysis/route");
    const res = await POST();
    expectNoStore(res);
  });
});
