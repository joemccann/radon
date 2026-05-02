/**
 * @vitest-environment jsdom
 */

import { afterEach, describe, expect, it, vi } from "vitest";

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("/api/menthorq/cta route — must be dynamic, not statically cached", () => {
  it("exports `dynamic = 'force-dynamic'` so Next.js never freezes a stale response", async () => {
    const mod = await import("../app/api/menthorq/cta/route");
    expect((mod as unknown as { dynamic?: string }).dynamic).toBe("force-dynamic");
  });
});

describe("useMenthorqCta hook — must request a fresh response", () => {
  it("calls fetch('/api/menthorq/cta') with cache: 'no-store'", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ date: null, fetched_at: null, tables: null }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    global.fetch = fetchMock as typeof fetch;

    const { renderHook } = await import("@testing-library/react");
    const { useMenthorqCta } = await import("../lib/useMenthorqCta");
    renderHook(() => useMenthorqCta());

    // Hook fires the fetch in a useEffect — give it one tick.
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(fetchMock).toHaveBeenCalled();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/menthorq/cta");
    expect((init as RequestInit | undefined)?.cache).toBe("no-store");
  });
});
