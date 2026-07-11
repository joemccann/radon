import { describe, it, expect, vi, beforeEach } from "vitest";

/**
 * Tests for web/lib/radonApi.ts — the fetch helper that ALL migrated routes
 * depend on for FastAPI communication.
 *
 * Edge cases:
 * - Successful JSON response
 * - HTTP error with JSON { detail } body (FastAPI HTTPException)
 * - HTTP error with JSON { error } body (legacy format)
 * - HTTP error with non-JSON body
 * - Timeout propagation
 * - Network errors (connection refused)
 */

// Mock global fetch before importing radonApi
const mockFetch = vi.fn();
globalThis.fetch = mockFetch as unknown as typeof fetch;

// Now import — it will use our mocked fetch
const { radonFetch, RadonApiError } = await import("../lib/radonApi");

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function textResponse(text: string, status = 500): Response {
  return new Response(text, { status });
}

beforeEach(() => {
  mockFetch.mockReset();
});

// =============================================================================
// Successful responses
// =============================================================================

describe("radonFetch — success", () => {
  it("returns parsed JSON on 200", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ positions: 20, bankroll: 100000 }));

    const data = await radonFetch<{ positions: number; bankroll: number }>("/portfolio/sync", {
      method: "POST",
    });

    expect(data.positions).toBe(20);
    expect(data.bankroll).toBe(100000);
  });

  it("calls correct URL with RADON_API base", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ ok: true }));

    await radonFetch("/health");

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url] = mockFetch.mock.calls[0];
    expect(url).toBe("http://localhost:8321/health");
  });

  it("passes through request init options", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ status: "ok" }));

    await radonFetch("/orders/place", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol: "AAPL" }),
    });

    const [, opts] = mockFetch.mock.calls[0];
    expect(opts.method).toBe("POST");
    expect(opts.cache).toBe("no-store");
    // radonFetch normalizes init.headers into a Headers instance (so it can
    // inject Authorization), so assert via the Headers API, not deep-equal.
    expect(new Headers(opts.headers).get("Content-Type")).toBe("application/json");
    expect(opts.body).toContain("AAPL");
  });

  it("returns 202 accepted as success", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ status: "accepted" }, 202));

    const data = await radonFetch("/portfolio/background-sync", { method: "POST" });
    expect(data).toEqual({ status: "accepted" });
  });
});

// =============================================================================
// Error responses — FastAPI detail format
// =============================================================================

describe("radonFetch — error handling", () => {
  it("throws RadonApiError with detail from FastAPI HTTPException", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ detail: "IB pool not connected" }, 503));

    try {
      await radonFetch("/portfolio/sync", { method: "POST" });
      expect.unreachable("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(RadonApiError);
      const e = err as InstanceType<typeof RadonApiError>;
      expect(e.status).toBe(503);
      expect(e.detail).toBe("IB pool not connected");
      expect(e.message).toContain("503");
    }
  });

  it("throws RadonApiError with error field from legacy JSON", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ error: "Scanner failed" }, 502));

    try {
      await radonFetch("/scan", { method: "POST" });
      expect.unreachable("should have thrown");
    } catch (err) {
      const e = err as InstanceType<typeof RadonApiError>;
      expect(e.status).toBe(502);
      expect(e.detail).toBe("Scanner failed");
    }
  });

  it("handles non-JSON error body gracefully", async () => {
    // When res.json() fails on a text body, the body stream is consumed.
    // res.text() also fails → falls back to "HTTP {status}"
    mockFetch.mockResolvedValue(textResponse("Internal Server Error", 500));

    try {
      await radonFetch("/attribution");
      expect.unreachable("should have thrown");
    } catch (err) {
      const e = err as InstanceType<typeof RadonApiError>;
      expect(e.status).toBe(500);
      // Body consumed by failed json() → text() also fails → fallback
      expect(e.detail).toBe("HTTP 500");
    }
  });

  it("handles empty error body", async () => {
    mockFetch.mockResolvedValue(new Response("", { status: 502 }));

    try {
      await radonFetch("/scan", { method: "POST" });
      expect.unreachable("should have thrown");
    } catch (err) {
      const e = err as InstanceType<typeof RadonApiError>;
      expect(e.status).toBe(502);
    }
  });

  it("handles 400 bad request with detail", async () => {
    mockFetch.mockResolvedValue(
      jsonResponse({ detail: "Discovery returned error" }, 400),
    );

    try {
      await radonFetch("/discover", { method: "POST" });
      expect.unreachable("should have thrown");
    } catch (err) {
      const e = err as InstanceType<typeof RadonApiError>;
      expect(e.status).toBe(400);
      expect(e.detail).toContain("error");
    }
  });
});

// =============================================================================
// Structured error detail (e.g. /admin/services returns {detail: {...}})
// Regression for the bug where the UI rendered "Radon API 502: [object Object]"
// because body.detail was an object and template literal stringified it.
// =============================================================================

describe("radonFetch — structured error detail", () => {
  it("extracts nested .detail string from object-shaped HTTPException", async () => {
    mockFetch.mockResolvedValue(
      jsonResponse(
        {
          detail: {
            unit: "radon-cta-sync.service",
            action: "start",
            ok: false,
            detail: "Failed to start radon-cta-sync.service: Interactive authentication required.",
            returncode: 1,
          },
        },
        502,
      ),
    );

    await expect(radonFetch("/admin/services/radon-cta-sync.service/start", { method: "POST" })).rejects.toMatchObject({
      status: 502,
      detail: "Failed to start radon-cta-sync.service: Interactive authentication required.",
      message: expect.stringContaining("Interactive authentication required"),
    });
  });

  it("does not produce [object Object] for unrecognised object shapes", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ detail: { foo: "bar", baz: 1 } }, 500));
    try {
      await radonFetch("/anything");
      throw new Error("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(RadonApiError);
      expect((err as Error).message).not.toContain("[object Object]");
      expect((err as RadonApiError).detail).toContain("foo");
    }
  });

  it("extracts string detail when detail is already a string (FastAPI HTTPException default)", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ detail: "Plain string error" }, 400));
    await expect(radonFetch("/anything")).rejects.toMatchObject({
      detail: "Plain string error",
    });
  });
});

// =============================================================================
// Timeout behavior
// =============================================================================

describe("radonFetch — timeout", () => {
  it("uses default 30s timeout when none specified", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ ok: true }));

    await radonFetch("/health");

    const [, opts] = mockFetch.mock.calls[0];
    expect(opts.signal).toBeDefined();
  });

  it("passes custom timeout to AbortSignal", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ ok: true }));

    await radonFetch("/scan", { method: "POST", timeout: 120_000 });

    const [, opts] = mockFetch.mock.calls[0];
    expect(opts.signal).toBeDefined();
  });

  it("propagates abort error from timed-out fetch", async () => {
    mockFetch.mockRejectedValue(new DOMException("The operation was aborted", "AbortError"));

    await expect(radonFetch("/performance", { method: "POST", timeout: 1 })).rejects.toThrow(
      "aborted",
    );
  });

  it("combines caller cancellation with the helper timeout", async () => {
    mockFetch.mockResolvedValue(jsonResponse({ ok: true }));
    const caller = new AbortController();

    await radonFetch("/health", { signal: caller.signal });

    const [, opts] = mockFetch.mock.calls[0];
    expect(opts.signal).not.toBe(caller.signal);
    expect(opts.signal.aborted).toBe(false);
    caller.abort();
    expect(opts.signal.aborted).toBe(true);
  });
});

// =============================================================================
// Network errors
// =============================================================================

describe("radonFetch — network errors", () => {
  it("propagates connection refused as raw error", async () => {
    mockFetch.mockRejectedValue(new TypeError("fetch failed"));

    await expect(radonFetch("/health")).rejects.toThrow("fetch failed");
  });

  it("propagates DNS resolution failure", async () => {
    mockFetch.mockRejectedValue(new TypeError("getaddrinfo ENOTFOUND localhost"));

    await expect(radonFetch("/health")).rejects.toThrow("ENOTFOUND");
  });
});
