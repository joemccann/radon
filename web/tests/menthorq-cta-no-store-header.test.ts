/**
 * Bug guard: /api/menthorq/cta must return Cache-Control: no-store on every
 * response so browsers and intermediaries never serve a stale snapshot.
 */

import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";

vi.mock("fs/promises", () => ({
  readdir: vi.fn(),
  readFile: vi.fn(),
  stat: vi.fn(),
}));

vi.mock("child_process", () => ({
  spawn: vi.fn(() => ({
    stdout: { on: vi.fn() },
    stderr: { on: vi.fn() },
    on: vi.fn(),
    unref: vi.fn(),
  })),
}));

vi.mock("@/lib/db", () => ({ resetDb: () => {},
  getDb: () => ({
    execute: vi.fn().mockResolvedValue({ rows: [] }),
  }),
}));

import { readFile, readdir, stat } from "fs/promises";

function expectNoStore(res: Response): void {
  const cc = res.headers.get("Cache-Control") ?? "";
  expect(cc.toLowerCase()).toContain("no-store");
}

describe("/api/menthorq/cta — Cache-Control: no-store", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.mocked(readdir).mockResolvedValue(["cta_2026-05-08.json"] as never);
    vi.mocked(readFile).mockResolvedValue(
      JSON.stringify({
        date: "2026-05-08",
        fetched_at: "2026-05-08T20:00:00Z",
        tables: { main: [], index: [], commodity: [], currency: [] },
      }) as unknown as string,
    );
    vi.mocked(stat).mockResolvedValue({ mtimeMs: Date.now() } as never);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("GET sets no-store on the primary success path", async () => {
    const { GET } = await import("../app/api/menthorq/cta/route");
    const res = await GET();
    expectNoStore(res);
  });

  it("GET sets no-store on the 503 cold-cache envelope", async () => {
    vi.mocked(readdir).mockResolvedValue([] as never);
    vi.mocked(readFile).mockRejectedValue(new Error("ENOENT"));
    vi.mocked(stat).mockRejectedValue(new Error("ENOENT"));
    const { GET } = await import("../app/api/menthorq/cta/route");
    const res = await GET();
    expectNoStore(res);
  });
});
