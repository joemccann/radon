import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

/**
 * API route smoke tests.
 *
 * One assertion per route: "responds with JSON, status 200 or a known error
 * envelope (400/4xx/5xx)". Backs the post-incident promise (2026-05-22) that
 * every API route has at least one test exercising its happy/sad path with
 * the FastAPI backend mocked.
 *
 * If you add a route under `web/app/api/`, add a describe block here. If the
 * route reaches out beyond the patterns mocked at the top of this file, mock
 * the dependency too.
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

const mockRunScript = vi.fn();
vi.mock("@tools/runner", () => ({
  runScript: mockRunScript,
  resolveProjectRoot: vi.fn().mockReturnValue("/mock/root"),
  resolvePythonBin: vi.fn().mockReturnValue("/mock/.venv/bin/python3.13"),
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

const mockReadDataFile = vi.fn();
vi.mock("@tools/data-reader", () => ({
  readDataFile: mockReadDataFile,
}));

const mockGetDb = vi.fn();
const mockSyncDb = vi.fn().mockResolvedValue(undefined);
vi.mock("@/lib/db", () => ({ resetDb: () => {},
  getDb: mockGetDb,
  syncDb: mockSyncDb,
}));

const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

// statSync / existsSync from `fs` (some routes import these synchronously)
vi.mock("fs", () => ({
  statSync: vi.fn().mockImplementation(() => {
    throw new Error("ENOENT");
  }),
  existsSync: vi.fn().mockReturnValue(false),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

function expectJsonResponse(res: Response): void {
  expect(res).toBeInstanceOf(Response);
  expect(res.status).toBeGreaterThanOrEqual(200);
  expect(res.status).toBeLessThan(600);
}

function dbStub(rows: unknown[] = []) {
  return {
    execute: vi.fn().mockResolvedValue({ rows }),
  };
}

beforeEach(() => {
  vi.resetModules();
  mockRadonFetch.mockReset();
  mockRunScript.mockReset();
  mockReadFile.mockReset();
  mockReaddir.mockReset();
  mockStat.mockReset();
  mockMkdir.mockResolvedValue(undefined);
  mockWriteFile.mockResolvedValue(undefined);
  mockReadDataFile.mockReset();
  mockGetDb.mockReset();
  mockFetch.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("GET /api/ticker/ratings (FastAPI passthrough)", () => {
  it("returns 200 with parsed payload when FastAPI succeeds", async () => {
    mockRadonFetch.mockResolvedValueOnce({ ticker: "AMD", consensus: "Buy", buy_count: 26 });
    const { GET } = await import("../app/api/ticker/ratings/route");
    const res = await GET(req("http://localhost/api/ticker/ratings?ticker=AMD"));
    expectJsonResponse(res);
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.ticker).toBe("AMD");
    expect(body.consensus).toBe("Buy");
    expect(mockRadonFetch).toHaveBeenCalledWith(
      expect.stringMatching(/^\/ticker\/ratings\?ticker=AMD$/),
      expect.objectContaining({ timeout: expect.any(Number) }),
    );
  });

  it("returns 400 when ticker query param is missing", async () => {
    const { GET } = await import("../app/api/ticker/ratings/route");
    const res = await GET(req("http://localhost/api/ticker/ratings"));
    expect(res.status).toBe(400);
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("returns 502 with error envelope when FastAPI fails (the production symptom)", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("upstream: subprocess died"));
    const { GET } = await import("../app/api/ticker/ratings/route");
    const res = await GET(req("http://localhost/api/ticker/ratings?ticker=AMD"));
    expect(res.status).toBe(502);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.error).toContain("Failed to fetch ratings");
    expect(body.detail).toContain("subprocess died");
  });
});

describe("POST /api/pi (FastAPI passthrough)", () => {
  function piReq(input: string): Request {
    return new Request("http://localhost/api/pi", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ input }),
    });
  }

  it("rejects unknown commands with 400 (no FastAPI call)", async () => {
    const { POST } = await import("../app/api/pi/route");
    const res = await POST(piReq("rm -rf /") as never);
    expect(res.status).toBe(400);
    expect(mockRadonFetch).not.toHaveBeenCalled();
  });

  it("forwards /scan to FastAPI /pi/exec and returns the assembled output", async () => {
    mockRadonFetch.mockResolvedValueOnce({
      ok: true,
      stdout: "Scanner results: 3 tickers\nAAPL +0.42",
      stderr: "",
      exit_code: 0,
      timed_out: false,
    });
    const { POST } = await import("../app/api/pi/route");
    const res = await POST(piReq("/scan --top 5") as never);
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.command).toBe("scan");
    expect(body.status).toBe("ok");
    expect(body.output).toContain("Scanner results");
    const call = mockRadonFetch.mock.calls[0];
    expect(call[0]).toBe("/pi/exec");
    expect(call[1]).toMatchObject({ method: "POST" });
    const bodyJson = JSON.parse(call[1].body as string);
    expect(bodyJson.script).toBe("scanner.py");
    expect(bodyJson.args).toEqual(["--top", "5"]);
  });

  it("returns 422 envelope when FastAPI reports a script failure", async () => {
    mockRadonFetch.mockResolvedValueOnce({
      ok: false,
      stdout: "",
      stderr: "Traceback: KeyError",
      exit_code: 1,
      timed_out: false,
    });
    const { POST } = await import("../app/api/pi/route");
    const res = await POST(piReq("/scan") as never);
    expect(res.status).toBe(422);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.command).toBe("scan");
    expect(body.status).toBe("error");
    expect(body.stderr).toContain("KeyError");
  });
});

describe("GET /api/cash-flows", () => {
  it("returns 200 JSON on success", async () => {
    mockRadonFetch.mockResolvedValueOnce({ rows: [], count: 0, summary: null });
    const { GET } = await import("../app/api/cash-flows/route");
    const res = await GET(req("http://localhost/api/cash-flows?days=30") as never);
    expectJsonResponse(res);
    expect(res.status).toBe(200);
  });

  it("returns 502 error envelope on FastAPI failure", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("connection refused"));
    const { GET } = await import("../app/api/cash-flows/route");
    const res = await GET(req("http://localhost/api/cash-flows") as never);
    expect(res.status).toBe(502);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.rows).toEqual([]);
    expect(body.error).toBeDefined();
  });
});

describe("GET /api/llm-token-index", () => {
  it("returns 200 JSON when FastAPI succeeds", async () => {
    mockRadonFetch.mockResolvedValueOnce({ rows: [{ date: "2026-05-22", index: 1.02 }], count: 1, days: 180 });
    const { GET } = await import("../app/api/llm-token-index/route");
    const res = await GET(req("http://localhost/api/llm-token-index") as never);
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.count).toBe(1);
  });

  it("returns 502 envelope on FastAPI error", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("upstream down"));
    const { GET } = await import("../app/api/llm-token-index/route");
    const res = await GET(req("http://localhost/api/llm-token-index") as never);
    expect(res.status).toBe(502);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.rows).toEqual([]);
  });
});

describe("GET /api/attribution", () => {
  it("returns 200 JSON on success", async () => {
    mockRadonFetch.mockResolvedValueOnce({ contributions: [] });
    const { GET } = await import("../app/api/attribution/route");
    const res = await GET();
    expect(res.status).toBe(200);
  });

  it("returns 500 envelope on upstream failure", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("timeout"));
    const { GET } = await import("../app/api/attribution/route");
    const res = await GET();
    expect(res.status).toBe(500);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.error).toBeDefined();
  });
});

describe("GET /api/flex-token", () => {
  it("returns 200 with parsed config when file exists", async () => {
    mockReadFile.mockResolvedValueOnce(JSON.stringify({
      token_masked: "abc***",
      activated_at: "2026-01-01",
      expires_at: "2027-01-01",
      renewal_url: "https://ib.test",
      breadcrumb: "Reports > Flex",
      reminder_days: [30, 14, 7, 1],
      reminders_sent: {},
      notes: "",
    }));
    const { GET } = await import("../app/api/flex-token/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.expires_at).toBeDefined();
    expect(typeof body.days_remaining).toBe("number");
  });

  it("returns 200 with 'not found' envelope when file is missing", async () => {
    mockReadFile.mockRejectedValueOnce(new Error("ENOENT"));
    const { GET } = await import("../app/api/flex-token/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.days_remaining).toBeNull();
    expect(body.error).toContain("not found");
  });
});

describe("GET /api/leap", () => {
  it("returns 200 with payload when leap.json exists", async () => {
    mockReadFile.mockResolvedValueOnce(JSON.stringify({
      scan_time: "2026-05-22T10:00:00Z",
      min_gap: 0.02,
      results: [{ ticker: "AAPL" }],
    }));
    const { GET } = await import("../app/api/leap/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(Array.isArray(body.results)).toBe(true);
    expect(body.cache_meta).toBeDefined();
  });

  it("returns 200 with empty results when file missing (graceful)", async () => {
    mockReadFile.mockRejectedValueOnce(new Error("ENOENT"));
    const { GET } = await import("../app/api/leap/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.results).toEqual([]);
  });
});

describe("GET /api/garch-convergence", () => {
  it("returns 200 with payload when garch_convergence.json exists", async () => {
    mockReadFile.mockResolvedValueOnce(JSON.stringify({
      scan_time: "2026-05-22T10:00:00Z",
      tickers: { NVDA: { price: 800 } },
      pairs: [{ pair: ["NVDA", "AMD"], divergence: 0.42 }],
    }));
    const { GET } = await import("../app/api/garch-convergence/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(Array.isArray(body.pairs)).toBe(true);
    expect(body.cache_meta).toBeDefined();
  });

  it("returns 200 with empty pairs when file missing (graceful)", async () => {
    mockReadFile.mockRejectedValueOnce(new Error("ENOENT"));
    const { GET } = await import("../app/api/garch-convergence/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.pairs).toEqual([]);
    expect(body.tickers).toEqual({});
  });
});

describe("POST /api/garch-convergence/scan", () => {
  it("forwards body to FastAPI and returns the payload on success", async () => {
    mockRadonFetch.mockResolvedValueOnce({
      scan_time: "2026-05-22T10:00:00Z",
      tickers: {},
      pairs: [{ pair: ["NVDA", "AMD"], divergence: 0.1 }],
    });
    const { POST } = await import("../app/api/garch-convergence/scan/route");
    const res = await POST(req("http://localhost/api/garch-convergence/scan", {
      method: "POST",
      body: JSON.stringify({ preset: "semis" }),
    }));
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(Array.isArray(body.pairs)).toBe(true);
    expect(mockRadonFetch).toHaveBeenCalledWith(
      "/garch-convergence/scan?preset=semis",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("returns 502 envelope when FastAPI fails", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("upstream timeout"));
    const { POST } = await import("../app/api/garch-convergence/scan/route");
    const res = await POST(req("http://localhost/api/garch-convergence/scan", {
      method: "POST",
      body: JSON.stringify({}),
    }));
    expect(res.status).toBe(502);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.error).toBeDefined();
  });
});

describe("GET /api/options/expirations", () => {
  it("returns 400 when symbol missing", async () => {
    const { GET } = await import("../app/api/options/expirations/route");
    const res = await GET(req("http://localhost/api/options/expirations"));
    expect(res.status).toBe(400);
  });

  it("returns 200 JSON on success", async () => {
    mockRadonFetch.mockResolvedValueOnce({ symbol: "AMD", expirations: ["20260522"] });
    const { GET } = await import("../app/api/options/expirations/route");
    const res = await GET(req("http://localhost/api/options/expirations?symbol=amd"));
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.symbol).toBe("AMD");
  });

  it("returns 502 on FastAPI failure", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("upstream"));
    const { GET } = await import("../app/api/options/expirations/route");
    const res = await GET(req("http://localhost/api/options/expirations?symbol=AMD"));
    expect(res.status).toBe(502);
  });
});

describe("GET /api/admin/health", () => {
  it("returns 200 with FastAPI health payload", async () => {
    mockRadonFetch.mockResolvedValueOnce({ auth_state: "authenticated", port_listening: true });
    const { GET } = await import("../app/api/admin/health/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.auth_state).toBe("authenticated");
  });

  it("returns 502 envelope when FastAPI is unreachable", async () => {
    mockRadonFetch.mockRejectedValueOnce(new Error("ECONNREFUSED"));
    const { GET } = await import("../app/api/admin/health/route");
    const res = await GET();
    expect(res.status).toBe(502);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.error).toBeDefined();
  });
});

describe("GET /api/service-health", () => {
  it("returns 200 with services array when DB returns rows", async () => {
    mockGetDb.mockReturnValueOnce(dbStub([
      {
        service: "ib-sync",
        state: "ok",
        last_attempt_started_at: "2026-05-22T10:00:00Z",
        last_attempt_finished_at: "2026-05-22T10:00:05Z",
        last_error: null,
        updated_at: new Date().toISOString(),
      },
    ]));
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(Array.isArray(body.services)).toBe(true);
    expect((body.services as unknown[]).length).toBeGreaterThan(0);
  });

  it("returns 200 with degraded provider row when DB throws", async () => {
    mockGetDb.mockReturnValueOnce({
      execute: vi.fn().mockRejectedValue(new Error("turso unreachable")),
    });
    const { GET } = await import("../app/api/service-health/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    const services = body.services as Array<Record<string, unknown>>;
    expect(services).toHaveLength(1);
    expect(services[0]).toMatchObject({ service: "turso-db", state: "error" });
    expect(body.degraded_count).toBe(1);
    expect(body.warning).toContain("turso");
  });
});

describe("GET /api/newsfeed/posts", () => {
  it("returns 200 with array of posts when DB returns rows", async () => {
    mockGetDb.mockReturnValueOnce(dbStub([
      {
        id: "1",
        title: "Test post",
        content: "Body",
        timestamp: "2026-05-22T10:00:00Z",
        images: null,
        raw_images: null,
        tags: '["VIX"]',
        tags_text: null,
        tags_vision: null,
        created_at: "2026-05-22T10:00:00Z",
        updated_at: "2026-05-22T10:00:00Z",
      },
    ]));
    const { GET } = await import("../app/api/newsfeed/posts/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = await jsonOf(res);
    expect(Array.isArray(body)).toBe(true);
  });

  it("returns 503 error envelope when DB throws", async () => {
    mockGetDb.mockReturnValueOnce({
      execute: vi.fn().mockRejectedValue(new Error("db down")),
    });
    const { GET } = await import("../app/api/newsfeed/posts/route");
    const res = await GET();
    expect(res.status).toBe(503);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.error).toContain("newsfeed");
  });
});

describe("GET /api/risk-free-rate", () => {
  it("returns 200 with rate from FRED CSV", async () => {
    const csv = "DATE,DFF\n2026-05-21,4.33\n2026-05-22,4.35\n";
    mockFetch.mockResolvedValueOnce({
      ok: true,
      text: async () => csv,
    });
    const { GET } = await import("../app/api/risk-free-rate/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.rate).toBeCloseTo(0.0435, 4);
    expect(body.source).toBe("FRED:DFF");
  });

  it("returns 200 with stale fallback when FRED is unreachable", async () => {
    mockFetch.mockRejectedValueOnce(new Error("network"));
    const { GET } = await import("../app/api/risk-free-rate/route");
    const res = await GET();
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.rate).toBe(0);
    expect(body.source).toBe("fallback");
    expect(body.stale).toBe(true);
  });
});

describe("GET /api/preferences (FastAPI passthrough)", () => {
  // Clerk is mocked per-test (doMock + doUnmock) so the shared module registry
  // used by every other block in this file stays untouched.
  it("returns 200 with the preferences payload for a signed-in user", async () => {
    vi.doMock("@clerk/nextjs/server", () => ({
      auth: vi.fn(async () => ({ userId: "user_smoke" })),
    }));
    mockRadonFetch.mockResolvedValueOnce({
      preferences: [],
      groups: ["Order Limits"],
      store: { available: true, error: null, checked_at: "2026-08-11T17:02:11Z" },
      generated_at: "2026-08-11T17:02:11Z",
    });
    const { GET } = await import("../app/api/preferences/route");
    const res = await GET();
    expectJsonResponse(res);
    expect(res.status).toBe(200);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(Array.isArray(body.preferences)).toBe(true);
    vi.doUnmock("@clerk/nextjs/server");
  });

  it("returns a 502 error envelope when FastAPI is unreachable", async () => {
    vi.doMock("@clerk/nextjs/server", () => ({
      auth: vi.fn(async () => ({ userId: "user_smoke" })),
    }));
    mockRadonFetch.mockRejectedValueOnce(new Error("connect ECONNREFUSED"));
    const { GET } = await import("../app/api/preferences/route");
    const res = await GET();
    expectJsonResponse(res);
    expect(res.status).toBe(502);
    const body = (await jsonOf(res)) as Record<string, unknown>;
    expect(body.code).toBe("UPSTREAM_ERROR");
    vi.doUnmock("@clerk/nextjs/server");
  });
});
