/**
 * PERF-020: newsfeed explain image loads are independent HTTPS GETs.
 * Two admitted media.radon.run URLs used to await one after the other
 * (max in-flight 1). Promise.all on the admitted batch raises it to 2
 * without extra fetches.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("PERF-020 newsfeed explain image fetch overlap", () => {
  it("fetches two admitted images concurrently", async () => {
    let inFlight = 0;
    let maxInFlight = 0;
    const fetchMock = vi.fn(async () => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await new Promise((resolve) => setTimeout(resolve, 50));
      inFlight -= 1;
      return new Response(PNG, { headers: { "content-type": "image/png" } });
    });
    vi.stubGlobal("fetch", fetchMock);
    const { loadExplainImages } = await import("@/lib/newsfeedExplainImages");
    const images = await loadExplainImages([
      "https://media.radon.run/newsfeed/a.png",
      "https://media.radon.run/newsfeed/b.png",
    ]);
    expect(images).toHaveLength(2);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(maxInFlight).toBe(2);
  });
});
