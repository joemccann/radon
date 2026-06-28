/**
 * Bug guard: /api/discover must return Cache-Control: no-store on every
 * response so browsers and intermediaries never serve a stale snapshot.
 *
 * Same class of bug as flow-analysis (commit ee8c401): `dynamic = "force-dynamic"`
 * only opts out of Next.js's static-page cache; the response itself was
 * heuristically cached by Caddy/the browser.
 */

import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";

vi.mock("fs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("fs")>();
  return { ...actual, statSync: vi.fn() };
});

vi.mock("fs/promises", () => ({
  readFile: vi.fn(),
}));

vi.mock("@/lib/db", () => ({ resetDb: () => {},
  getDb: () => ({
    execute: vi.fn().mockResolvedValue({ rows: [] }),
  }),
}));

vi.mock("@/lib/radonApi", () => ({
  radonFetch: vi.fn().mockResolvedValue({
    discovery_time: "2026-05-09T02:04:09.999Z",
    alerts_analyzed: 0,
    candidates_found: 0,
    candidates: [],
  }),
}));

import * as fs from "fs";
import { readFile } from "fs/promises";

const validCachePayload = JSON.stringify({
  discovery_time: "2026-05-09T02:04:09.999Z",
  alerts_analyzed: 0,
  candidates_found: 0,
  candidates: [],
});

function expectNoStore(res: Response): void {
  const cc = res.headers.get("Cache-Control") ?? "";
  expect(cc.toLowerCase()).toContain("no-store");
}

describe("/api/discover — Cache-Control: no-store", () => {
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
    const { GET } = await import("../app/api/discover/route");
    const res = await GET();
    expectNoStore(res);
  });

  it("GET sets no-store on the empty-fallback envelope when both DB and disk are empty", async () => {
    vi.mocked(readFile).mockRejectedValue(new Error("ENOENT"));
    vi.mocked(fs.statSync).mockImplementation(() => {
      throw new Error("ENOENT");
    });

    const { GET } = await import("../app/api/discover/route");
    const res = await GET();
    expectNoStore(res);
  });

  it("POST sets no-store on the FastAPI-passthrough response", async () => {
    const { POST } = await import("../app/api/discover/route");
    const res = await POST();
    expectNoStore(res);
  });
});
