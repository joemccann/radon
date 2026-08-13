import { beforeEach, describe, expect, it, vi } from "vitest";

const radonFetch = vi.fn();
vi.mock("@/lib/radonApi", () => ({
  radonFetch,
  RadonApiError: class RadonApiError extends Error {},
}));
vi.mock("@clerk/nextjs/server", () => ({
  auth: vi.fn(async () => ({ userId: "operator", getToken: async () => "token" })),
}));

describe("workflow execution confirmation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    process.env.ALLOWED_USER_IDS = "operator";
    radonFetch.mockResolvedValue({ steps: [], final_rows: [] });
  });

  it("confirm_order requires literal JSON true", async () => {
    const { POST } = await import("@/app/api/workflow/run/route");
    await POST(new Request("http://localhost/api/workflow/run", {
      method: "POST",
      body: JSON.stringify({ graph: { nodes: [], edges: [] }, confirm_order: "false" }),
    }));
    const request = radonFetch.mock.calls[0]?.[1] as { body: string };
    expect(JSON.parse(request.body).confirm_order).toBe(false);
  });
});
