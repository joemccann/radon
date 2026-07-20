import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  radonFetch: vi.fn(),
}));

vi.mock("@/lib/radonApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/radonApi")>();
  return {
    ...actual,
    radonFetch: mocks.radonFetch,
  };
});

const RESULT_ROW = {
  source: "evals",
  scope: "trading",
  doc_key: "evals/NVDA-2026-06-01",
  chunk_ix: 0,
  title: "NVDA 7-milestone eval",
  summary: "Bull call spread thesis off dark-pool accumulation.",
  content: "Signal: sustained dark-pool accumulation above prior VWAP...",
  metadata: { tickers: ["NVDA"] },
  score: 0.912,
  last_activity_at: "2026-06-01T14:00:00Z",
  neighbors: [],
};

const SEARCH_PAYLOAD = { results: [RESULT_ROW], retrieval: "hybrid" };
const PRIOR_EVALS_PAYLOAD = { ticker: "NVDA", results: [RESULT_ROW], retrieval: "hybrid" };

describe("POST /api/knowledge/search", () => {
  beforeEach(() => {
    vi.resetModules();
    mocks.radonFetch.mockReset();
  });

  it("forwards the body to FastAPI and returns the payload without caching", async () => {
    mocks.radonFetch.mockResolvedValue(SEARCH_PAYLOAD);
    const { POST } = await import("../app/api/knowledge/search/route");

    const body = {
      query: "gex flip regime",
      scopes: ["trading"],
      sources: ["evals", "journal"],
      limit: 6,
      compact: true,
    };
    const response = await POST(
      new Request("http://localhost/api/knowledge/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    );

    expect(response.status).toBe(200);
    expect((response.headers.get("Cache-Control") ?? "").toLowerCase()).toContain("no-store");
    expect(await response.json()).toEqual(SEARCH_PAYLOAD);

    expect(mocks.radonFetch).toHaveBeenCalledTimes(1);
    const [path, opts] = mocks.radonFetch.mock.calls[0];
    expect(path).toBe("/knowledge/search");
    expect(opts).toMatchObject({ method: "POST" });
    expect(JSON.parse(opts.body as string)).toEqual(body);
  });

  it("rejects invalid JSON without calling FastAPI", async () => {
    const { POST } = await import("../app/api/knowledge/search/route");

    const response = await POST(
      new Request("http://localhost/api/knowledge/search", {
        method: "POST",
        body: "not-json{",
      }),
    );

    expect(response.status).toBe(400);
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });

  it("rejects a missing or empty query without calling FastAPI", async () => {
    const { POST } = await import("../app/api/knowledge/search/route");

    const response = await POST(
      new Request("http://localhost/api/knowledge/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: "   " }),
      }),
    );

    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ error: expect.stringContaining("query") });
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });

  it("passes FastAPI error status and detail through without collapsing to 500", async () => {
    const { RadonApiError } = await import("@/lib/radonApi");
    mocks.radonFetch.mockRejectedValue(new RadonApiError(422, "query must be 1..500 chars"));
    const { POST } = await import("../app/api/knowledge/search/route");

    const response = await POST(
      new Request("http://localhost/api/knowledge/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: "x".repeat(10) }),
      }),
    );

    expect(response.status).toBe(422);
    expect(await response.json()).toMatchObject({ error: "query must be 1..500 chars" });
  });

  it("maps non-Radon failures to 502", async () => {
    mocks.radonFetch.mockRejectedValue(new Error("fetch failed"));
    const { POST } = await import("../app/api/knowledge/search/route");

    const response = await POST(
      new Request("http://localhost/api/knowledge/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: "gex" }),
      }),
    );

    expect(response.status).toBe(502);
    expect(await response.json()).toMatchObject({ error: "fetch failed" });
  });
});

describe("GET /api/knowledge/prior-evals", () => {
  beforeEach(() => {
    vi.resetModules();
    mocks.radonFetch.mockReset();
  });

  it("normalizes the ticker and forwards query params without caching", async () => {
    mocks.radonFetch.mockResolvedValue(PRIOR_EVALS_PAYLOAD);
    const { GET } = await import("../app/api/knowledge/prior-evals/route");

    const response = await GET(
      new Request("http://localhost/api/knowledge/prior-evals?ticker=nvda&limit=4"),
    );

    expect(response.status).toBe(200);
    expect((response.headers.get("Cache-Control") ?? "").toLowerCase()).toContain("no-store");
    expect(await response.json()).toEqual(PRIOR_EVALS_PAYLOAD);
    expect(mocks.radonFetch).toHaveBeenCalledWith(
      "/knowledge/prior-evals?ticker=NVDA&limit=4",
      expect.anything(),
    );
  });

  it("forwards compact through to FastAPI like the search proxy does", async () => {
    mocks.radonFetch.mockResolvedValue(PRIOR_EVALS_PAYLOAD);
    const { GET } = await import("../app/api/knowledge/prior-evals/route");

    await GET(
      new Request("http://localhost/api/knowledge/prior-evals?ticker=NVDA&compact=true"),
    );

    expect(mocks.radonFetch).toHaveBeenCalledWith(
      "/knowledge/prior-evals?ticker=NVDA&compact=true",
      expect.anything(),
    );
  });

  it("omits limit when the caller does not supply one", async () => {
    mocks.radonFetch.mockResolvedValue(PRIOR_EVALS_PAYLOAD);
    const { GET } = await import("../app/api/knowledge/prior-evals/route");

    await GET(new Request("http://localhost/api/knowledge/prior-evals?ticker=NVDA"));

    expect(mocks.radonFetch).toHaveBeenCalledWith(
      "/knowledge/prior-evals?ticker=NVDA",
      expect.anything(),
    );
  });

  it.each([
    "http://localhost/api/knowledge/prior-evals",
    "http://localhost/api/knowledge/prior-evals?ticker=TOOLONGG",
    "http://localhost/api/knowledge/prior-evals?ticker=BR%24E",
  ])("rejects a missing or malformed ticker before FastAPI: %s", async (url) => {
    const { GET } = await import("../app/api/knowledge/prior-evals/route");

    const response = await GET(new Request(url));

    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ error: expect.stringContaining("ticker") });
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });

  it("passes FastAPI error status and detail through without collapsing to 500", async () => {
    const { RadonApiError } = await import("@/lib/radonApi");
    mocks.radonFetch.mockRejectedValue(new RadonApiError(503, "Knowledge retrieval unavailable"));
    const { GET } = await import("../app/api/knowledge/prior-evals/route");

    const response = await GET(
      new Request("http://localhost/api/knowledge/prior-evals?ticker=NVDA"),
    );

    expect(response.status).toBe(503);
    expect(await response.json()).toMatchObject({ error: "Knowledge retrieval unavailable" });
  });

  it("maps non-Radon failures to 502", async () => {
    mocks.radonFetch.mockRejectedValue(new Error("socket hang up"));
    const { GET } = await import("../app/api/knowledge/prior-evals/route");

    const response = await GET(
      new Request("http://localhost/api/knowledge/prior-evals?ticker=NVDA"),
    );

    expect(response.status).toBe(502);
    expect(await response.json()).toMatchObject({ error: "socket hang up" });
  });
});
