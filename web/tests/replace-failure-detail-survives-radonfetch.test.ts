/**
 * @vitest-environment node
 *
 * REL-044 / R-090 (P1): the indeterminate-replace renderer is unreachable.
 *
 * `errorDetailAsString` short-circuits on `typeof detail === "string"` before
 * it can reach the REPLACE_PARTIAL / REPLACE_INDETERMINATE branch, and
 * `RadonApiError.detail` is always a string because every branch of
 * `coerceRadonErrorDetail` returns one — FastAPI's structured detail becomes
 * `JSON.stringify(raw)`. So the operator never sees "the working orders were
 * already cancelled at IB ... Check open orders before retrying", the sentence
 * that stops a blind retry from double-placing while the position sits
 * unhedged. The existing regression test only passes through an
 * `as unknown as string` cast that cannot occur through `radonFetch`.
 *
 * This drives the REAL `radonFetch` against a mocked `fetch`.
 */
import { describe, it, expect, vi, afterEach } from "vitest";

import { coerceRadonErrorDetail, RadonApiError, radonFetch } from "../lib/radonApi";

const STRUCTURED_DETAIL = {
  code: "REPLACE_PARTIAL",
  phase: "placement",
  cancelled: [{ orderId: 77 }],
  replacementOrderRef: "radon-replace-9f2c",
  upstream: "Order not acknowledged by IB",
};

function mockFetch(body: unknown, status = 502) {
  const spy = vi.fn(async () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", spy);
  return spy;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("structured FastAPI detail survives radonFetch", () => {
  it("keeps a {code,...} detail as an object, not JSON.stringify", async () => {
    mockFetch({ detail: STRUCTURED_DETAIL });

    await expect(radonFetch("/orders/modify", { method: "POST" }))
      .rejects.toBeInstanceOf(RadonApiError);

    try {
      await radonFetch("/orders/modify", { method: "POST" });
    } catch (error) {
      const detail = (error as RadonApiError).detail;
      expect(typeof detail).toBe("object");
      expect((detail as Record<string, unknown>).code).toBe("REPLACE_PARTIAL");
      expect((detail as Record<string, unknown>).replacementOrderRef).toBe(
        "radon-replace-9f2c",
      );
    }
  });

  it("reaches the operator-facing replace copy through the real route helper", async () => {
    mockFetch({ detail: STRUCTURED_DETAIL });

    const { POST } = await import("../app/api/orders/modify/route");
    const request = new Request("http://localhost/api/orders/modify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        orderId: 77,
        permId: 0,
        newLimitPrice: 1.25,
        newQuantity: 1,
      }),
    });
    const response = await POST(request as never);
    const payload = await response.json();

    expect(String(payload.error)).toContain("Replacement incomplete during placement");
    expect(String(payload.error)).toContain("#77");
    expect(String(payload.error)).toContain("radon-replace-9f2c");
    expect(String(payload.error)).not.toContain('{"code"');
  });

  it("renders the REPLACE_INDETERMINATE stop-a-blind-retry sentence", async () => {
    mockFetch({
      detail: { ...STRUCTURED_DETAIL, code: "REPLACE_INDETERMINATE" },
    });

    const { POST } = await import("../app/api/orders/modify/route");
    const request = new Request("http://localhost/api/orders/modify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        orderId: 77,
        permId: 0,
        newLimitPrice: 1.25,
        newQuantity: 1,
      }),
    });
    const payload = await (await POST(request as never)).json();

    expect(String(payload.error)).toContain("already cancelled at IB");
    expect(String(payload.error)).toContain("Check open orders before retrying");
  });

  it("still flattens a plain string detail", () => {
    expect(coerceRadonErrorDetail({ detail: "boom" }, 500)).toBe("boom");
    expect(coerceRadonErrorDetail("boom", 500)).toBe("boom");
    expect(coerceRadonErrorDetail(null, 503)).toBe("HTTP 503");
  });

  it("still flattens a structured detail that carries no code", () => {
    expect(coerceRadonErrorDetail({ detail: { message: "nope" } }, 500)).toBe("nope");
    expect(coerceRadonErrorDetail({ detail: { a: 1 } }, 500)).toBe('{"a":1}');
  });

  it("RadonApiError.message stays a readable string for a structured detail", () => {
    const error = new RadonApiError(502, STRUCTURED_DETAIL);
    expect(error.message).toContain("502");
    expect(error.message).toContain("REPLACE_PARTIAL");
  });
});
