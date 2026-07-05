import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const mocks = vi.hoisted(() => ({
  radonFetch: vi.fn(),
}));

vi.mock("@/lib/radonApi", () => ({
  radonFetch: mocks.radonFetch,
  RadonApiError: class RadonApiError extends Error {
    status: number;
    constructor(status: number, detail: string) {
      super(detail);
      this.status = status;
    }
  },
}));

function noStoreHeader(res: Response): string {
  return (res.headers.get("Cache-Control") ?? "").toLowerCase();
}

beforeEach(() => {
  vi.resetModules();
  mocks.radonFetch.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("POST /api/leap/scan", () => {
  it("forwards the preset scan to FastAPI", async () => {
    mocks.radonFetch.mockResolvedValueOnce({ results: [] });
    const { POST } = await import("../app/api/leap/scan/route");
    const req = new Request("http://localhost/api/leap/scan", {
      method: "POST",
      body: JSON.stringify({ preset: "mag7" }),
    });

    const res = await POST(req);

    expect(res.status).toBe(200);
    expect(noStoreHeader(res)).toContain("no-store");
    expect(mocks.radonFetch).toHaveBeenCalledWith("/leap/scan?preset=mag7", {
      method: "POST",
      timeout: 310_000,
    });
  });

  it("forwards a custom ticker list, normalised and deduped", async () => {
    mocks.radonFetch.mockResolvedValueOnce({ universe: "explicit", results: [] });
    const { POST } = await import("../app/api/leap/scan/route");
    const req = new Request("http://localhost/api/leap/scan", {
      method: "POST",
      body: JSON.stringify({ tickers: "nvda, amd, nvda" }),
    });

    const res = await POST(req);
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.universe).toBe("explicit");
    expect(mocks.radonFetch).toHaveBeenCalledWith("/leap/scan?tickers=NVDA%2CAMD", {
      method: "POST",
      timeout: 310_000,
    });
  });

  it("accepts an array of tickers in the body", async () => {
    mocks.radonFetch.mockResolvedValueOnce({ results: [] });
    const { POST } = await import("../app/api/leap/scan/route");
    const req = new Request("http://localhost/api/leap/scan", {
      method: "POST",
      body: JSON.stringify({ tickers: ["NVDA", "AMD"] }),
    });

    await POST(req);

    expect(mocks.radonFetch).toHaveBeenCalledWith("/leap/scan?tickers=NVDA%2CAMD", {
      method: "POST",
      timeout: 310_000,
    });
  });

  it("rejects malformed ticker lists before hitting FastAPI", async () => {
    const { POST } = await import("../app/api/leap/scan/route");
    const req = new Request("http://localhost/api/leap/scan", {
      method: "POST",
      body: JSON.stringify({ tickers: "NVDA,AM-D" }),
    });

    const res = await POST(req);
    const body = await res.json();

    expect(res.status).toBe(400);
    expect(body.error).toContain("1-6 letter");
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });
});

describe("POST /api/garch-convergence/scan", () => {
  it("forwards the preset scan to FastAPI", async () => {
    mocks.radonFetch.mockResolvedValueOnce({ pairs: [] });
    const { POST } = await import("../app/api/garch-convergence/scan/route");
    const req = new Request("http://localhost/api/garch-convergence/scan", {
      method: "POST",
      body: JSON.stringify({ preset: "mega-tech" }),
    });

    const res = await POST(req);

    expect(res.status).toBe(200);
    expect(noStoreHeader(res)).toContain("no-store");
    expect(mocks.radonFetch).toHaveBeenCalledWith("/garch-convergence/scan?preset=mega-tech", {
      method: "POST",
      timeout: 190_000,
    });
  });

  it("forwards pair tickers without deduping", async () => {
    mocks.radonFetch.mockResolvedValueOnce({ pairs: [] });
    const { POST } = await import("../app/api/garch-convergence/scan/route");
    const req = new Request("http://localhost/api/garch-convergence/scan", {
      method: "POST",
      body: JSON.stringify({ tickers: "nvda, amd, nvda, tsm" }),
    });

    const res = await POST(req);

    expect(res.status).toBe(200);
    expect(mocks.radonFetch).toHaveBeenCalledWith(
      "/garch-convergence/scan?tickers=NVDA%2CAMD%2CNVDA%2CTSM",
      { method: "POST", timeout: 190_000 },
    );
  });

  it("rejects an odd ticker count before hitting FastAPI", async () => {
    const { POST } = await import("../app/api/garch-convergence/scan/route");
    const req = new Request("http://localhost/api/garch-convergence/scan", {
      method: "POST",
      body: JSON.stringify({ tickers: "NVDA,AMD,TSM" }),
    });

    const res = await POST(req);
    const body = await res.json();

    expect(res.status).toBe(400);
    expect(body.error).toContain("even number of tickers");
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });

  it("rejects malformed pair symbols before hitting FastAPI", async () => {
    const { POST } = await import("../app/api/garch-convergence/scan/route");
    const req = new Request("http://localhost/api/garch-convergence/scan", {
      method: "POST",
      body: JSON.stringify({ tickers: "NVDA,4MD" }),
    });

    const res = await POST(req);
    const body = await res.json();

    expect(res.status).toBe(400);
    expect(body.error).toContain("1-6 letter");
    expect(mocks.radonFetch).not.toHaveBeenCalled();
  });
});
