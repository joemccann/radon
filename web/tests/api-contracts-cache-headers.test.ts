import { NextResponse } from "next/server";
import { describe, expect, it } from "vitest";

import { setCacheResponseHeaders } from "@/lib/apiContracts";

// Every route using this helper sits behind the Clerk auth gate. Browser
// caching is fine; a shared cache (CDN / reverse proxy) must never store the
// response, or it could hand an authenticated body to a later requester.
describe("setCacheResponseHeaders", () => {
  it("marks short-TTL responses private so shared caches never store them", () => {
    const res = setCacheResponseHeaders(NextResponse.json({ ok: true }), {
      maxAgeSeconds: 15,
      staleWhileRevalidateSeconds: 120,
      requestId: "rid",
    });
    const cc = res.headers.get("Cache-Control") ?? "";
    expect(cc).toBe("private, max-age=15, stale-while-revalidate=120");
    expect(cc).not.toMatch(/\bpublic\b/);
    expect(cc).not.toMatch(/s-maxage/);
  });
});
