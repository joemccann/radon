/**
 * @vitest-environment jsdom
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/IBStatusContext", () => ({
  useIBStatusContext: () => ({ displayStatus: "connected" }),
}));

vi.mock("@/lib/useServiceHealth", () => ({
  useServiceHealth: () => ({ data: null, loading: false, error: "health read failed" }),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("FooterTelemetryStrip unknown state", () => {
  it("never renders unknown service or token state with nominal tone", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    const { default: FooterTelemetryStrip } = await import("../components/FooterTelemetryStrip");

    render(<FooterTelemetryStrip />);

    const services = screen.getByText("Services").closest("[data-tone]");
    const flex = screen.getByText("Flex Token").closest("[data-tone]");
    expect(services?.getAttribute("data-tone")).toBe("warn");
    expect(flex?.getAttribute("data-tone")).toBe("warn");
  });

  it("does not call an empty service inventory nominal", async () => {
    const { servicesChipFor } = await import("../components/FooterTelemetryStrip");

    expect(servicesChipFor(0, 0, false)).toEqual({ text: "Unknown", cls: "warn" });
  });
});
