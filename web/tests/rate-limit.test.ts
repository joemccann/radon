import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { rateLimit, clientIp, SHARE_CARD_LIMIT, SHARE_PNL_LIMIT, SHARE_WINDOW_MS } from "../lib/rateLimit";

// Access the internal store via module re-import trick — we reset it between
// tests by fast-forwarding time past all windows (fake timers on Date.now).

describe("rateLimit — fixed-window per-key", () => {
  let fakeNow: number;

  beforeEach(() => {
    fakeNow = 1_000_000;
    vi.spyOn(Date, "now").mockImplementation(() => fakeNow);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    // Advance time so stale entries are evicted on next call.
    fakeNow += SHARE_WINDOW_MS * 2;
  });

  it("allows requests under the limit", () => {
    const key = `test-under-${Date.now()}`;
    for (let i = 0; i < SHARE_CARD_LIMIT; i++) {
      const result = rateLimit(key, { limit: SHARE_CARD_LIMIT, windowMs: SHARE_WINDOW_MS });
      expect(result.ok).toBe(true);
      expect(result.retryAfterSec).toBe(0);
    }
  });

  it("blocks the request that exceeds the limit", () => {
    const key = `test-exceed-${Date.now()}-${Math.random()}`;
    const limit = 5;
    const windowMs = 10_000;

    for (let i = 0; i < limit; i++) {
      const result = rateLimit(key, { limit, windowMs });
      expect(result.ok).toBe(true);
    }

    const over = rateLimit(key, { limit, windowMs });
    expect(over.ok).toBe(false);
    expect(over.retryAfterSec).toBeGreaterThan(0);
  });

  it("returns a non-zero retryAfterSec when blocked", () => {
    const key = `test-retry-${Date.now()}-${Math.random()}`;
    const limit = 3;
    const windowMs = 30_000;

    for (let i = 0; i < limit; i++) {
      rateLimit(key, { limit, windowMs });
    }

    const result = rateLimit(key, { limit, windowMs });
    expect(result.ok).toBe(false);
    // retryAfterSec should be at most windowMs / 1000 (ceiling of remaining window).
    expect(result.retryAfterSec).toBeLessThanOrEqual(Math.ceil(windowMs / 1000));
    expect(result.retryAfterSec).toBeGreaterThan(0);
  });

  it("resets the window after windowMs elapses", () => {
    const key = `test-reset-${Date.now()}-${Math.random()}`;
    const limit = 2;
    const windowMs = 5_000;

    // Exhaust the limit.
    for (let i = 0; i < limit; i++) {
      rateLimit(key, { limit, windowMs });
    }
    expect(rateLimit(key, { limit, windowMs }).ok).toBe(false);

    // Advance time past the window.
    fakeNow += windowMs + 1;

    // New window — should be allowed again.
    const reset = rateLimit(key, { limit, windowMs });
    expect(reset.ok).toBe(true);
    expect(reset.retryAfterSec).toBe(0);
  });

  it("tracks distinct keys independently", () => {
    const base = `test-independent-${Date.now()}-${Math.random()}`;
    const keyA = `${base}-A`;
    const keyB = `${base}-B`;
    const limit = 2;
    const windowMs = 5_000;

    // Exhaust keyA.
    for (let i = 0; i < limit; i++) {
      rateLimit(keyA, { limit, windowMs });
    }
    expect(rateLimit(keyA, { limit, windowMs }).ok).toBe(false);

    // keyB should still be under the limit.
    const resultB = rateLimit(keyB, { limit, windowMs });
    expect(resultB.ok).toBe(true);
  });

  it("named constants have sane values", () => {
    expect(SHARE_CARD_LIMIT).toBeGreaterThan(0);
    expect(SHARE_PNL_LIMIT).toBeGreaterThan(0);
    expect(SHARE_WINDOW_MS).toBeGreaterThan(0);
    // PNL (OG render) limit is >= card limit — it's less CPU-sensitive per call.
    expect(SHARE_PNL_LIMIT).toBeGreaterThanOrEqual(SHARE_CARD_LIMIT);
  });
});

describe("clientIp — IP extraction from Request headers", () => {
  function makeRequest(headers: Record<string, string> = {}): Request {
    return new Request("https://radon.run/api/share/pnl", { headers });
  }

  it("returns the first hop from x-forwarded-for", () => {
    const req = makeRequest({ "x-forwarded-for": "1.2.3.4, 10.0.0.1" });
    expect(clientIp(req)).toBe("1.2.3.4");
  });

  it("trims whitespace from the first hop", () => {
    const req = makeRequest({ "x-forwarded-for": "  5.6.7.8  , 10.0.0.1" });
    expect(clientIp(req)).toBe("5.6.7.8");
  });

  it("returns a single-entry xff directly", () => {
    const req = makeRequest({ "x-forwarded-for": "192.168.1.1" });
    expect(clientIp(req)).toBe("192.168.1.1");
  });

  it("falls back to 'unknown' when xff header is absent", () => {
    const req = makeRequest({});
    expect(clientIp(req)).toBe("unknown");
  });

  it("falls back to 'unknown' when xff header is empty", () => {
    const req = makeRequest({ "x-forwarded-for": "" });
    expect(clientIp(req)).toBe("unknown");
  });
});
