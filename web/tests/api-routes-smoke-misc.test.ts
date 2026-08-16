import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

/**
 * Smoke tests for the remaining miscellaneous routes:
 *   - GET /api/menthorq/cta
 *   - GET/POST /api/performance
 *   - GET/POST /api/scanner
 *   - GET /api/vcg
 *   - POST /api/journal/sync
 *   - POST /api/leap/scan
 *   - GET /api/options/chain
 *   - POST /api/ib/ws-ticket
 *   - GET/POST /api/prices (deprecated 405)
 *   - GET /api/ticker/info
 *
 * Companion file to `api-routes-smoke.test.ts`.
 */

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const mockRadonFetch = vi.fn();
vi.mock("@/lib/radonApi", () => ({
  radonFetch: mockRadonFetch,
  RadonApiError: class RadonApiError extends Error {
    status: number;
    detail: string;
    constructor(status: number, detail: string) {
      super(`Radon API ${status}: ${detail}`);
      this.name = "RadonApiError";
      this.status = status;
      this.detail = detail;
    }
  },
}));

const mockReadFile = vi.fn();
const mockReaddir = vi.fn();
const mockStat = vi.fn();
const mockMkdir = vi.fn();
const mockWriteFile = vi.fn();
vi.mock("fs/promises", () => ({
  readFile: mockReadFile,
  readdir: mockReaddir,
  stat: mockStat,
  mkdir: mockMkdir,
  writeFile: mockWriteFile,
}));
vi.mock("node:fs/promises", () => ({
  readFile: mockReadFile,
  readdir: mockReaddir,
  stat: mockStat,
  mkdir: mockMkdir,
  writeFile: mockWriteFile,
}));
// fs.promises (used by ticker/info route via `import { promises as fs } from "fs"`)
vi.mock("fs", () => ({
  statSync: vi.fn().mockImplementation(() => {
    throw new Error("ENOENT");
  }),
  promises: {
    readFile: mockReadFile,
    readdir: mockReaddir,
    stat: mockStat,
    mkdir: mockMkdir,
    writeFile: mockWriteFile,
  },
}));

const mockGetDb = vi.fn();
const mockSyncDb = vi.fn().mockResolvedValue(undefined);
vi.mock("@/lib/db", () => ({ resetDb: () => {},
  getDb: mockGetDb,
  syncDb: mockSyncDb,
}));

// child_process.spawn — menthorq/cta triggers a background sync via spawn
const spawnStub = {
  on: vi.fn(),
  unref: vi.fn(),
};
vi.mock("child_process", () => ({
  spawn: vi.fn().mockReturnValue(spawnStub),
}));

const mockImportLatestReconciliationToJournal = vi.fn();
const mockImportReconciliationSnapshotToJournal = vi.fn();
vi.mock("@/lib/journalDb", () => ({
  importLatestReconciliationToJournal: mockImportLatestReconciliationToJournal,
  importReconciliationSnapshotToJournal: mockImportReconciliationSnapshotToJournal,
}));

// Global fetch — used by ticker/info, etc.
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

beforeEach(() => {
  vi.resetModules();
  mockRadonFetch.mockReset();
  // Default to a never-resolving promise so fire-and-forget background
  // triggers (vcg/performance/scanner/cri) don't crash on `.then` of
  // undefined when an explicit test doesn't set up their own resolution.
  mockRadonFetch.mockImplementation(() => new Promise(() => {}));
  mockReadFile.mockReset();
  mockReaddir.mockReset();
  mockStat.mockReset();
  mockMkdir.mockResolvedValue(undefined);
  mockWriteFile.mockResolvedValue(undefined);
  mockGetDb.mockReset();
  mockImportLatestReconciliationToJournal.mockReset();
  mockFetch.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

function req(url: string, init?: RequestInit): Request {
  return new Request(url, init);
}

async function jsonOf(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return { __raw: text };
  }
}

function dbStub(rows: unknown[] = []) {
  return {
    execute: vi.fn().mockResolvedValue({ rows }),
  };
}

// ---------------------------------------------------------------------------
// /api/menthorq/cta
// ---------------------------------------------------------------------------

describe("GET /api/menthorq/cta", () => {
  it("returns 200 with DB-sourced payload when DB has rows", async () => {
    mockGetDb.mockReturnValue(
      dbStub([
        {
          date: "2026-05-22",
          payload: JSON.stringify({
            date: "2026-05-22",
            fetched_at: "2026-05-22T10:00:00Z",
            tables: { main: [], index: [], commodity: [], currency: [] },
          }),
          fetched_at: "2026-05-22T10:00:00Z",
        },
      ]),
    );
    mockReadFile.mockRejectedValue(new Error("ENOENT")); // no sync_health on disk
    const { GET } = await import("../app/api/menthorq/cta/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.cache_meta).toBeDefined();
  });

  it("returns 503 when no DB rows and no disk cache exists", async () => {
    mockGetDb.mockReturnValue(dbStub([]));
    mockReaddir.mockRejectedValue(new Error("ENOENT"));
    mockReadFile.mockRejectedValue(new Error("ENOENT"));
    const { GET } = await import("../app/api/menthorq/cta/route");
    const res = await GET();
    expect(res.status).toBe(503);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.cache_meta).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// /api/performance
// ---------------------------------------------------------------------------

describe("GET /api/performance", () => {
  it("returns 200 with cached payload when available (fresh)", async () => {
    // Timestamps MUST track the current ET session. A hardcoded date rots:
    // once it falls behind today's trading session, the route enters the
    // /portfolio/sync branch and hangs forever on the never-resolving
    // radonFetch mock (see beforeEach). Matching cache + portfolio to "now"
    // keeps the route on the genuine fresh-cache path it's meant to assert.
    const now = new Date();
    const nowIso = now.toISOString();
    const nowEtDate = now.toLocaleDateString("sv", { timeZone: "America/New_York" });
    mockGetDb.mockReturnValue(
      dbStub([
        {
          payload: JSON.stringify({ last_sync: nowIso, as_of: nowEtDate }),
        },
      ]),
    );
    mockReadFile.mockResolvedValue(JSON.stringify({ last_sync: nowIso }));
    mockStat.mockResolvedValue({ mtimeMs: Date.now() }); // fresh
    const { GET } = await import("../app/api/performance/route");
    const res = await GET();
    expect(res.status).toBe(200);
  });

  it("returns 200 with status unavailable when cold start (no cache) and FastAPI fails", async () => {
    // §C.6: GET never emits a 4xx/5xx for missing data — it emits a status.
    mockGetDb.mockReturnValue(dbStub([]));
    mockReadFile.mockRejectedValue(new Error("ENOENT"));
    mockStat.mockRejectedValue(new Error("ENOENT"));
    mockRadonFetch.mockRejectedValue(new Error("upstream down"));
    const { GET } = await import("../app/api/performance/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.status).toBe("unavailable");
    expect(JSON.stringify(body)).not.toContain("upstream down");
  });
});

describe("POST /api/performance", () => {
  it("returns 200 on FastAPI success", async () => {
    mockRadonFetch.mockResolvedValueOnce({ as_of: "now", metrics: {} });
    const { POST } = await import("../app/api/performance/route");
    const res = await POST();
    expect(res.status).toBe(200);
  });

  it("returns 502 envelope on FastAPI failure", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("upstream down"));
    const { POST } = await import("../app/api/performance/route");
    const res = await POST();
    expect(res.status).toBe(502);
  });
});

// ---------------------------------------------------------------------------
// /api/scanner
// ---------------------------------------------------------------------------

describe("GET /api/scanner", () => {
  it("returns 200 with DB payload when available", async () => {
    mockGetDb.mockReturnValue(
      dbStub([{ payload: JSON.stringify({ scan_time: "now", top_signals: [] }) }]),
    );
    const { GET } = await import("../app/api/scanner/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.cache_meta).toBeDefined();
  });

  it("returns 200 with empty shape when DB and disk are both empty", async () => {
    mockGetDb.mockReturnValue(dbStub([]));
    mockReadFile.mockRejectedValue(new Error("ENOENT"));
    const { GET } = await import("../app/api/scanner/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.top_signals).toEqual([]);
  });
});

describe("POST /api/scanner", () => {
  it("returns 200 on FastAPI success", async () => {
    mockRadonFetch.mockResolvedValueOnce({ scan_time: "now", top_signals: [] });
    const { POST } = await import("../app/api/scanner/route");
    const res = await POST();
    expect(res.status).toBe(200);
  });

  it("returns 502 envelope when FastAPI fails and no cache", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("upstream down"));
    mockReadFile.mockRejectedValueOnce(new Error("ENOENT"));
    const { POST } = await import("../app/api/scanner/route");
    const res = await POST();
    expect(res.status).toBe(502);
  });
});

// ---------------------------------------------------------------------------
// /api/vcg (only GET; no POST handler in route file)
// ---------------------------------------------------------------------------

describe("GET /api/vcg", () => {
  it("returns 200 from DB snapshot when present", async () => {
    mockGetDb.mockReturnValue(
      dbStub([
        {
          payload: JSON.stringify({
            scan_time: "now",
            signal: {},
            history: [],
          }),
        },
      ]),
    );
    const { GET } = await import("../app/api/vcg/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.signal).toBeDefined();
  });

  it("returns 200 with empty payload when DB and disk are empty", async () => {
    mockGetDb.mockReturnValue(dbStub([]));
    mockReadFile.mockRejectedValue(new Error("ENOENT"));
    const { GET } = await import("../app/api/vcg/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.signal).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// /api/journal/sync
// ---------------------------------------------------------------------------

describe("POST /api/journal/sync", () => {
  it("returns 200 with import counts on success", async () => {
    const snapshotAt = new Date().toISOString();
    mockRadonFetch.mockResolvedValueOnce({ ok: true, snapshot_at: snapshotAt });
    mockImportReconciliationSnapshotToJournal.mockResolvedValueOnce({ imported: 3, skipped: 0 });
    const { POST } = await import("../app/api/journal/sync/route");
    const res = await POST();
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.imported).toBe(3);
    expect(mockImportReconciliationSnapshotToJournal).toHaveBeenCalledWith(snapshotAt);
  });

  it("returns 500 envelope on FastAPI reconcile failure", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("upstream down"));
    const { POST } = await import("../app/api/journal/sync/route");
    const res = await POST();
    expect(res.status).toBe(500);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.error).toBeDefined();
    expect(body.imported).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// /api/leap/scan
// ---------------------------------------------------------------------------

describe("POST /api/leap/scan", () => {
  it("returns 200 on FastAPI success", async () => {
    mockRadonFetch.mockResolvedValueOnce({ ok: true, results: [] });
    const { POST } = await import("../app/api/leap/scan/route");
    const res = await POST(req("http://localhost/api/leap/scan", { method: "POST" }));
    expect(res.status).toBe(200);
  });

  it("returns 502 envelope on FastAPI failure", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("upstream down"));
    const { POST } = await import("../app/api/leap/scan/route");
    const res = await POST(req("http://localhost/api/leap/scan", { method: "POST" }));
    expect(res.status).toBe(502);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.error).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// /api/options/chain
// ---------------------------------------------------------------------------

describe("GET /api/options/chain", () => {
  it("returns 400 when symbol is missing", async () => {
    const { GET } = await import("../app/api/options/chain/route");
    const res = await GET(req("http://localhost/api/options/chain"));
    expect(res.status).toBe(400);
  });

  it("returns 200 on FastAPI success", async () => {
    mockRadonFetch.mockResolvedValueOnce({
      symbol: "AMD",
      expirations: ["20260522"],
      strikes: [],
    });
    const { GET } = await import("../app/api/options/chain/route");
    const res = await GET(req("http://localhost/api/options/chain?symbol=AMD"));
    expect(res.status).toBe(200);
  });

  it("returns 502 envelope on FastAPI failure", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("upstream down"));
    const { GET } = await import("../app/api/options/chain/route");
    const res = await GET(req("http://localhost/api/options/chain?symbol=AMD"));
    expect(res.status).toBe(502);
  });
});

// ---------------------------------------------------------------------------
// /api/ib/ws-ticket
// ---------------------------------------------------------------------------

describe("POST /api/ib/ws-ticket", () => {
  it("returns 200 with ticket on FastAPI success", async () => {
    mockRadonFetch.mockResolvedValueOnce({ ticket: "abc123" });
    const { POST } = await import("../app/api/ib/ws-ticket/route");
    const res = await POST();
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.ticket).toBe("abc123");
    expect(res.headers.get("cache-control") || "").toMatch(/no-store/);
    expect(mockRadonFetch).toHaveBeenCalledWith("/ws-ticket", {
      method: "POST",
      token: undefined,
    });
  });

  it("forwards the Clerk bearer token to FastAPI", async () => {
    mockRadonFetch.mockResolvedValueOnce({ ticket: "abc123" });
    const { POST } = await import("../app/api/ib/ws-ticket/route");
    const res = await POST(req("http://localhost/api/ib/ws-ticket", {
      method: "POST",
      headers: { Authorization: "Bearer clerk-token-123" },
    }));
    expect(res.status).toBe(200);
    expect(mockRadonFetch).toHaveBeenCalledWith("/ws-ticket", {
      method: "POST",
      token: "clerk-token-123",
    });
  });

  it("returns 500 envelope on FastAPI failure", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("upstream down"));
    const { POST } = await import("../app/api/ib/ws-ticket/route");
    const res = await POST();
    expect(res.status).toBe(500);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.detail).toBeDefined();
    expect(res.headers.get("cache-control") || "").toMatch(/no-store/);
  });
});

// ---------------------------------------------------------------------------
// /api/prices — both verbs return 405 (deprecated SSE → WS)
// ---------------------------------------------------------------------------

describe("GET /api/prices", () => {
  it("returns 405 with deprecation envelope", async () => {
    const { GET } = await import("../app/api/prices/route");
    const res = await GET();
    expect(res.status).toBe(405);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.error).toContain("deprecated");
  });
});

describe("POST /api/prices", () => {
  it("returns 405 with deprecation envelope", async () => {
    const { POST } = await import("../app/api/prices/route");
    const res = await POST();
    expect(res.status).toBe(405);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.error).toContain("deprecated");
  });
});

// ---------------------------------------------------------------------------
// /api/ticker/info
// ---------------------------------------------------------------------------

describe("GET /api/ticker/info", () => {
  it("returns 400 when ticker is missing", async () => {
    const { GET } = await import("../app/api/ticker/info/route");
    const res = await GET(req("http://localhost/api/ticker/info"));
    expect(res.status).toBe(400);
  });

  it("returns 200 when cached entry has profile + non-expired stats", async () => {
    const fresh = new Date(Date.now() + 60 * 60 * 1000).toISOString();
    mockReadFile.mockResolvedValueOnce(
      JSON.stringify({
        ticker: "AMD",
        profile_expires: null,
        stats_expires: fresh,
        uw_info: { name: "AMD" },
        stock_state: { last: 100 },
        exa_profile: { ceo: "Lisa Su" },
        exa_stats: { pe_ratio: "30" },
        fetched_at: "2026-05-22T10:00:00Z",
      }),
    );
    // stock-state refresh fetches UW too — return ok
    mockFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ data: { last: 101 } }),
    });
    const oldToken = process.env.UW_TOKEN;
    process.env.UW_TOKEN = "test-token";
    try {
      const { GET } = await import("../app/api/ticker/info/route");
      const res = await GET(req("http://localhost/api/ticker/info?ticker=AMD"));
      expect(res.status).toBe(200);
      const body = (await jsonOf(res)) as Record<string, unknown>;
      expect(body.uw_info).toBeDefined();
    } finally {
      if (oldToken == null) delete process.env.UW_TOKEN;
      else process.env.UW_TOKEN = oldToken;
    }
  });

  it("returns 500 when no UW_TOKEN env var and cache missing", async () => {
    mockReadFile.mockRejectedValueOnce(new Error("ENOENT"));
    const oldToken = process.env.UW_TOKEN;
    delete process.env.UW_TOKEN;
    try {
      const { GET } = await import("../app/api/ticker/info/route");
      const res = await GET(req("http://localhost/api/ticker/info?ticker=AMD"));
      expect(res.status).toBe(500);
      const body = (await jsonOf(res)) as Record<string, unknown>;
      expect(body.error).toContain("UW_TOKEN");
    } finally {
      if (oldToken != null) process.env.UW_TOKEN = oldToken;
    }
  });
});

// ---------------------------------------------------------------------------
// TODO smoke tests — routes that don't fit the smoke-test pattern.
//
// next/og ImageResponse routes can't be reasonably exercised in unit tests
// without a full headless browser:
//
//   - GET /api/share/pnl                         (next/og)
//   - GET /api/menthorq/cta/image                (next/og)
//   - GET /api/menthorq/[command]/image          (next/og)
//
// These are integration-tested via the share PnL E2E (web/e2e/share-pnl.spec.ts)
// or visual diff against reports/ artifacts.
// ---------------------------------------------------------------------------
