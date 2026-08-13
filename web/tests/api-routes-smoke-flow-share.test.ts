import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

/**
 * Smoke tests for flow-analysis, internals, and share-* routes.
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
vi.mock("fs", () => ({
  statSync: vi.fn().mockImplementation(() => {
    throw new Error("ENOENT");
  }),
}));

const mockGetDb = vi.fn();
const mockSyncDb = vi.fn().mockResolvedValue(undefined);
vi.mock("@/lib/db", () => ({ resetDb: () => {},
  getDb: mockGetDb,
  syncDb: mockSyncDb,
}));

// Authenticated route principal with a token for FastAPI proxy calls.
const mockAuth = vi.fn().mockResolvedValue({
  userId: "user_test",
  getToken: async () => "test-token",
});
vi.mock("@clerk/nextjs/server", () => ({
  auth: mockAuth,
}));

const originalAllowedUserIds = process.env.ALLOWED_USER_IDS;

beforeEach(() => {
  process.env.ALLOWED_USER_IDS = "user_test";
  vi.resetModules();
  mockRadonFetch.mockReset();
  mockReadFile.mockReset();
  mockReaddir.mockReset();
  mockStat.mockReset();
  mockMkdir.mockResolvedValue(undefined);
  mockWriteFile.mockResolvedValue(undefined);
  mockGetDb.mockReset();
  mockAuth.mockResolvedValue({
    userId: "user_test",
    getToken: async () => "test-token",
  });
});

afterEach(() => {
  if (originalAllowedUserIds === undefined) delete process.env.ALLOWED_USER_IDS;
  else process.env.ALLOWED_USER_IDS = originalAllowedUserIds;
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
// /api/flow-analysis
// ---------------------------------------------------------------------------

describe("GET /api/flow-analysis", () => {
  it("returns 200 from DB snapshot when present", async () => {
    mockGetDb.mockReturnValue(
      dbStub([{ payload: JSON.stringify({ analysis_time: "now", supports: [] }) }]),
    );
    const { GET } = await import("../app/api/flow-analysis/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.cache_meta).toBeDefined();
  });

  it("returns 200 with empty shape when both DB and disk are empty", async () => {
    mockGetDb.mockReturnValue(dbStub([]));
    mockReadFile.mockRejectedValue(new Error("ENOENT"));
    const { GET } = await import("../app/api/flow-analysis/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.supports).toEqual([]);
  });
});

describe("POST /api/flow-analysis", () => {
  it("returns 200 on FastAPI success", async () => {
    mockGetDb.mockReturnValue(dbStub([]));
    mockRadonFetch.mockResolvedValueOnce({
      analysis_time: "now",
      supports: [],
      against: [],
      watch: [],
      neutral: [],
    });
    const { POST } = await import("../app/api/flow-analysis/route");
    const res = await POST();
    expect(res.status).toBe(200);
  });

  it("returns 502 with error envelope when FastAPI fails and no cache exists", async () => {
    mockGetDb.mockReturnValue(dbStub([]));
    mockRadonFetch.mockRejectedValueOnce(new Error("upstream down"));
    mockReadFile.mockRejectedValue(new Error("ENOENT"));
    const { POST } = await import("../app/api/flow-analysis/route");
    const res = await POST();
    expect(res.status).toBe(502);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.error).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// /api/flow-analysis/[ticker]
// ---------------------------------------------------------------------------

describe("GET /api/flow-analysis/[ticker]", () => {
  it("returns 200 with cached payload when ticker cache exists", async () => {
    mockReadFile.mockResolvedValueOnce(
      JSON.stringify({ ticker: "AMD", windows: [] }),
    );
    const { GET } = await import("../app/api/flow-analysis/[ticker]/route");
    const res = await GET(
      req("http://localhost/api/flow-analysis/AMD"),
      { params: Promise.resolve({ ticker: "AMD" }) },
    );
    expect(res.status).toBe(200);
  });

  it("returns 200 with missing:true when no cache yet (graceful first-time)", async () => {
    mockReadFile.mockRejectedValueOnce(new Error("ENOENT"));
    const { GET } = await import("../app/api/flow-analysis/[ticker]/route");
    const res = await GET(
      req("http://localhost/api/flow-analysis/AMD"),
      { params: Promise.resolve({ ticker: "AMD" }) },
    );
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.missing).toBe(true);
  });

  it("returns 400 for invalid ticker", async () => {
    const { GET } = await import("../app/api/flow-analysis/[ticker]/route");
    const res = await GET(
      req("http://localhost/api/flow-analysis/invalid!"),
      { params: Promise.resolve({ ticker: "invalid!" }) },
    );
    expect(res.status).toBe(400);
  });
});

describe("POST /api/flow-analysis/[ticker]", () => {
  it("returns 200 on FastAPI success", async () => {
    mockRadonFetch.mockResolvedValueOnce({ ticker: "AMD", windows: [] });
    const { POST } = await import("../app/api/flow-analysis/[ticker]/route");
    const res = await POST(
      req("http://localhost/api/flow-analysis/AMD", { method: "POST" }),
      { params: Promise.resolve({ ticker: "AMD" }) },
    );
    expect(res.status).toBe(200);
  });

  it("returns 502 envelope when FastAPI fails and no cached file", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("upstream down"));
    mockReadFile.mockRejectedValueOnce(new Error("ENOENT"));
    const { POST } = await import("../app/api/flow-analysis/[ticker]/route");
    const res = await POST(
      req("http://localhost/api/flow-analysis/AMD", { method: "POST" }),
      { params: Promise.resolve({ ticker: "AMD" }) },
    );
    expect(res.status).toBe(502);
  });
});

// ---------------------------------------------------------------------------
// /api/internals
// ---------------------------------------------------------------------------

describe("GET /api/internals", () => {
  it("returns 200 even when nothing on disk (empty CRI shape)", async () => {
    mockReaddir.mockResolvedValue([]);
    mockReadFile.mockRejectedValue(new Error("ENOENT"));
    mockStat.mockRejectedValue(new Error("ENOENT"));
    // /internals/skew-history fetch should not be required
    mockRadonFetch.mockRejectedValue(new Error("upstream down"));
    const { GET } = await import("../app/api/internals/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.cri).toBeDefined();
  });
});

describe("POST /api/internals", () => {
  it("returns 200 on FastAPI success", async () => {
    mockReaddir.mockResolvedValue([]);
    mockReadFile.mockRejectedValue(new Error("ENOENT"));
    mockStat.mockRejectedValue(new Error("ENOENT"));
    mockRadonFetch.mockResolvedValueOnce({
      scan_time: "now",
      date: "2026-05-22",
      cri: { score: 1, level: "LOW", components: {} },
      cta: {},
      crash_trigger: {},
      history: [],
      spy_closes: [],
    });
    const { POST } = await import("../app/api/internals/route");
    const res = await POST();
    expect(res.status).toBe(200);
  });

  it("returns 502 envelope when FastAPI fails", async () => {
    mockReaddir.mockResolvedValue([]);
    mockReadFile.mockRejectedValue(new Error("ENOENT"));
    mockStat.mockRejectedValue(new Error("ENOENT"));
    mockRadonFetch.mockRejectedValue(new Error("upstream down"));
    const { POST } = await import("../app/api/internals/route");
    const res = await POST();
    expect(res.status).toBe(502);
  });
});

// ---------------------------------------------------------------------------
// Share endpoints (POST /<surface>/share — proxies to FastAPI)
// ---------------------------------------------------------------------------

describe("POST /api/internals/share", () => {
  it("returns 200 on FastAPI success", async () => {
    mockRadonFetch.mockResolvedValueOnce({ ok: true, url: "/share/foo" });
    const { POST } = await import("../app/api/internals/share/route");
    const res = await POST();
    expect(res.status).toBe(200);
  });

  it("returns 500 envelope on FastAPI failure", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("upstream down"));
    const { POST } = await import("../app/api/internals/share/route");
    const res = await POST();
    expect(res.status).toBe(500);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.error).toBeDefined();
  });
});

describe("POST /api/gex/share", () => {
  it("returns 200 on FastAPI success", async () => {
    mockRadonFetch.mockResolvedValueOnce({ ok: true, url: "/share/gex" });
    const { POST } = await import("../app/api/gex/share/route");
    const res = await POST();
    expect(res.status).toBe(200);
  });

  it("returns 500 envelope on FastAPI failure", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("upstream down"));
    const { POST } = await import("../app/api/gex/share/route");
    const res = await POST();
    expect(res.status).toBe(500);
  });
});

describe("POST /api/regime/share", () => {
  it("returns 200 on FastAPI success", async () => {
    mockRadonFetch.mockResolvedValueOnce({ ok: true, url: "/share/regime" });
    const { POST } = await import("../app/api/regime/share/route");
    const res = await POST();
    expect(res.status).toBe(200);
  });

  it("returns 500 envelope on FastAPI failure", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("upstream down"));
    const { POST } = await import("../app/api/regime/share/route");
    const res = await POST();
    expect(res.status).toBe(500);
  });
});

describe("POST /api/vcg/share", () => {
  it("returns 200 on FastAPI success", async () => {
    mockRadonFetch.mockResolvedValueOnce({ ok: true, url: "/share/vcg" });
    const { POST } = await import("../app/api/vcg/share/route");
    const res = await POST();
    expect(res.status).toBe(200);
  });

  it("returns 500 envelope on FastAPI failure", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("upstream down"));
    const { POST } = await import("../app/api/vcg/share/route");
    const res = await POST();
    expect(res.status).toBe(500);
  });
});

describe("POST /api/menthorq/cta/share", () => {
  it("returns 200 on FastAPI success", async () => {
    mockRadonFetch.mockResolvedValueOnce({ ok: true, url: "/share/cta" });
    const { POST } = await import("../app/api/menthorq/cta/share/route");
    const res = await POST();
    expect(res.status).toBe(200);
  });

  it("returns 500 envelope on FastAPI failure", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("upstream down"));
    const { POST } = await import("../app/api/menthorq/cta/share/route");
    const res = await POST();
    expect(res.status).toBe(500);
  });
});

// ---------------------------------------------------------------------------
// share/content GET — serves HTML report files inside reports/ dir.
// Path-traversal defense: 400 on missing path, 403 outside reports/, 404 if file missing.
// ---------------------------------------------------------------------------

describe("GET /api/gex/share/content", () => {
  it("returns 400 when path is missing", async () => {
    const { GET } = await import("../app/api/gex/share/content/route");
    const next = await import("next/server");
    const nextReq = new next.NextRequest("http://localhost/api/gex/share/content");
    const res = await GET(nextReq);
    expect(res.status).toBe(400);
  });

  it("returns 403 when path is outside reports/", async () => {
    const { GET } = await import("../app/api/gex/share/content/route");
    const next = await import("next/server");
    const nextReq = new next.NextRequest(
      "http://localhost/api/gex/share/content?path=/etc/passwd",
    );
    const res = await GET(nextReq);
    expect(res.status).toBe(403);
  });
});

describe("GET /api/regime/share/content", () => {
  it("returns 400 when path is missing", async () => {
    const { GET } = await import("../app/api/regime/share/content/route");
    const next = await import("next/server");
    const nextReq = new next.NextRequest(
      "http://localhost/api/regime/share/content",
    );
    const res = await GET(nextReq);
    expect(res.status).toBe(400);
  });

  it("returns 403 when path is outside reports/", async () => {
    const { GET } = await import("../app/api/regime/share/content/route");
    const next = await import("next/server");
    const nextReq = new next.NextRequest(
      "http://localhost/api/regime/share/content?path=/tmp/evil.html",
    );
    const res = await GET(nextReq);
    expect(res.status).toBe(403);
  });
});

describe("GET /api/vcg/share/content", () => {
  it("returns 400 when path is missing", async () => {
    const { GET } = await import("../app/api/vcg/share/content/route");
    const next = await import("next/server");
    const nextReq = new next.NextRequest("http://localhost/api/vcg/share/content");
    const res = await GET(nextReq);
    expect(res.status).toBe(400);
  });

  it("returns 403 when path is outside reports/", async () => {
    const { GET } = await import("../app/api/vcg/share/content/route");
    const next = await import("next/server");
    const nextReq = new next.NextRequest(
      "http://localhost/api/vcg/share/content?path=/etc/hosts",
    );
    const res = await GET(nextReq);
    expect(res.status).toBe(403);
  });
});

describe("GET /api/internals/share/content", () => {
  it("returns 400 when path is missing", async () => {
    const { GET } = await import("../app/api/internals/share/content/route");
    const next = await import("next/server");
    const nextReq = new next.NextRequest(
      "http://localhost/api/internals/share/content",
    );
    const res = await GET(nextReq);
    expect(res.status).toBe(400);
  });

  it("returns 403 when path is outside reports/", async () => {
    const { GET } = await import("../app/api/internals/share/content/route");
    const next = await import("next/server");
    const nextReq = new next.NextRequest(
      "http://localhost/api/internals/share/content?path=/root/secrets",
    );
    const res = await GET(nextReq);
    expect(res.status).toBe(403);
  });
});

describe("GET /api/menthorq/cta/share/content", () => {
  it("returns 400 when path is missing", async () => {
    const { GET } = await import(
      "../app/api/menthorq/cta/share/content/route"
    );
    const next = await import("next/server");
    const nextReq = new next.NextRequest(
      "http://localhost/api/menthorq/cta/share/content",
    );
    const res = await GET(nextReq);
    expect(res.status).toBe(400);
  });

  it("returns 403 when path is outside reports/", async () => {
    const { GET } = await import(
      "../app/api/menthorq/cta/share/content/route"
    );
    const next = await import("next/server");
    const nextReq = new next.NextRequest(
      "http://localhost/api/menthorq/cta/share/content?path=/etc/passwd",
    );
    const res = await GET(nextReq);
    expect(res.status).toBe(403);
  });
});
