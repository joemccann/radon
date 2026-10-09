import { afterEach, describe, expect, it, vi } from "vitest";
import { GET } from "@/app/api/version/route";

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("GET /api/version", () => {
  it("returns the baked release and forbids caches", async () => {
    vi.stubEnv("NEXT_PUBLIC_RADON_VERSION", "0.8.0");
    vi.stubEnv("NEXT_PUBLIC_RADON_GIT_SHA", "abc123def456");
    vi.stubEnv("NEXT_PUBLIC_RADON_BUILT_AT", "2026-10-09T18:00:00Z");
    vi.stubEnv("NEXT_PUBLIC_RADON_CHANNEL", "production");

    const response = GET();

    expect(response.headers.get("cache-control")).toBe("no-store, no-cache, must-revalidate");
    expect(await response.json()).toEqual({
      version: "0.8.0",
      sha: "abc123def456",
      builtAt: "2026-10-09T18:00:00Z",
      channel: "production",
    });
  });
});
