import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
const remote = vi.hoisted(() => vi.fn());
vi.mock("@/lib/radonApi", () => ({ radonFetch: remote }));
const model = { id: "antigravity-default", provider: "antigravity", label: "ANTIGRAVITY", refreshedAt: "2026-10-30", capabilities: { tools: false, images: false } };
const catalog = { models: [model], defaultId: model.id, source: "gateway" };
beforeEach(() => { remote.mockReset().mockResolvedValue(catalog); });
afterEach(() => vi.unstubAllEnvs());

describe("central model catalog", () => {
  it("serves service-owned availability and default even without local grants", async () => {
    const { resolveModelCatalog } = await import("@/lib/llm/catalog");
    await expect(resolveModelCatalog()).resolves.toEqual(catalog);
    expect(remote).toHaveBeenCalledWith("/model-gateway/models", expect.objectContaining({ cache: "no-store" }));
  });
  it("validates selection against the live service catalog", async () => {
    const { validateModelId } = await import("@/lib/llm/catalog");
    await expect(validateModelId(model.id)).resolves.toEqual(model);
    await expect(validateModelId("invented-model")).resolves.toBeNull();
    await expect(validateModelId(null)).resolves.toBeNull();
  });
  it("does not manufacture models when the service is unavailable", async () => {
    remote.mockRejectedValue(new Error("Gateway unavailable"));
    const { resolveModelCatalog } = await import("@/lib/llm/catalog");
    await expect(resolveModelCatalog()).rejects.toThrow("Gateway unavailable");
  });
  it("rejects a malformed catalog rather than advertising unsupported selection", async () => {
    remote.mockResolvedValue({ models: [model], defaultId: "not-listed", source: "gateway" });
    const { resolveModelCatalog } = await import("@/lib/llm/catalog");
    await expect(resolveModelCatalog()).rejects.toThrow(/malformed/i);
  });
  it("GET /api/models returns the same no-store catalog", async () => {
    const { GET } = await import("@/app/api/models/route");
    const response = await GET();
    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toContain("no-store");
    await expect(response.json()).resolves.toMatchObject(catalog);
  });
});
