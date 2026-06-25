/**
 * @vitest-environment node
 */
import { afterEach, describe, expect, it, vi } from "vitest";

const mockGetWsTicket = vi.fn();
vi.mock("@/lib/wsTicket", () => ({
  getWsTicket: mockGetWsTicket,
}));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.resetAllMocks();
});

describe("resolveRealtimeWebSocketUrl", () => {
  it("uses same-origin /ws in production browsers when no explicit URL is configured", async () => {
    vi.stubGlobal("window", {
      location: {
        protocol: "https:",
        host: "app.radon.run",
        hostname: "app.radon.run",
      },
    });

    const { resolveRealtimeWebSocketUrl } = await import("@/lib/realtimeSocketAuth");

    expect(resolveRealtimeWebSocketUrl("")).toBe("wss://app.radon.run/ws");
  });

  it("keeps the localhost relay fallback only for local development", async () => {
    vi.stubGlobal("window", {
      location: {
        protocol: "http:",
        host: "localhost:3000",
        hostname: "localhost",
      },
    });

    const { resolveRealtimeWebSocketUrl } = await import("@/lib/realtimeSocketAuth");

    expect(resolveRealtimeWebSocketUrl("")).toBe("ws://localhost:8765");
  });

  it("honors explicit websocket configuration", async () => {
    const { resolveRealtimeWebSocketUrl } = await import("@/lib/realtimeSocketAuth");

    expect(resolveRealtimeWebSocketUrl("wss://relay.example.test/ws")).toBe(
      "wss://relay.example.test/ws",
    );
  });
});

describe("buildAuthenticatedWebSocketUrl", () => {
  it("appends a FastAPI websocket ticket when a token getter is provided", async () => {
    mockGetWsTicket.mockResolvedValueOnce("ticket 123");
    const { buildAuthenticatedWebSocketUrl } = await import("@/lib/realtimeSocketAuth");

    const url = await buildAuthenticatedWebSocketUrl(
      "wss://app.radon.run/ws",
      async () => "clerk-token",
    );

    expect(url).toBe("wss://app.radon.run/ws?ticket=ticket%20123");
    expect(mockGetWsTicket).toHaveBeenCalledWith("clerk-token");
  });

  it("does not open an unauthenticated production socket when no token is available", async () => {
    const { buildAuthenticatedWebSocketUrl } = await import("@/lib/realtimeSocketAuth");

    await expect(
      buildAuthenticatedWebSocketUrl("wss://app.radon.run/ws", async () => null),
    ).rejects.toThrow("Realtime auth token unavailable");
    expect(mockGetWsTicket).not.toHaveBeenCalled();
  });
});
