import { beforeEach, describe, expect, it, vi } from "vitest";
import { RadonApiError } from "@/lib/radonApi";

const fetchMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/radonApi", async (original) => ({
  ...await original<typeof import("@/lib/radonApi")>(),
  radonFetch: fetchMock,
}));
import { GET } from "@/app/api/ai-cycle/route";

describe("AI infrastructure read proxy", () => {
  beforeEach(() => { fetchMock.mockReset(); });
  it("reads the backend snapshot with no-store and returns provenance intact", async () => {
    const payload = { version: 1, indicators: [], sources: [], shadow: { state: "insufficient_evidence" } };
    fetchMock.mockResolvedValue(payload);
    const response = await GET();
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(payload);
    expect(response.headers.get("cache-control")).toContain("no-store");
    expect(fetchMock).toHaveBeenCalledWith("/ai-cycle", { timeout: 45_000, cache: "no-store" });
  });
  it("preserves upstream failure status and detail", async () => {
    fetchMock.mockRejectedValue(new RadonApiError(503, "Snapshot store unavailable"));
    const response = await GET();
    expect(response.status).toBe(503);
    expect(response.headers.get("cache-control")).toContain("no-store");
    expect(response.headers.get("x-request-id")).toBeTruthy();
    expect(await response.json()).toEqual({ error: "Snapshot store unavailable" });
  });
  it("does not leak arbitrary transport errors", async () => {
    fetchMock.mockRejectedValue(new Error("credential=private"));
    const response = await GET();
    expect(response.status).toBe(502);
    expect(response.headers.get("cache-control")).toContain("no-store");
    expect(response.headers.get("x-request-id")).toBeTruthy();
    expect(await response.text()).not.toContain("private");
  });
});
