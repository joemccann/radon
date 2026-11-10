/**
 * @vitest-environment jsdom
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/IBStatusContext", () => ({
  useIBStatusContext: () => ({ displayStatus: "connected" }),
}));

vi.mock("@/lib/useServiceHealth", () => ({
  useServiceHealth: () => ({
    data: {
      services: [],
      failing: [],
      degraded_count: 0,
      summary: { total: 12, failing_count: 0 },
    },
    loading: false,
    error: "Service health unavailable (503)",
  }),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("service-health transport failure UI", () => {
  it("overrides cached nominal footer data with an unavailable warning", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    const { default: FooterTelemetryStrip } = await import("../components/FooterTelemetryStrip");

    render(<FooterTelemetryStrip />);

    const services = screen.getByText("Services").closest("[data-tone]");
    expect(services?.getAttribute("data-tone")).toBe("warn");
    expect(services?.textContent).toContain("Unavailable");
  });
});
