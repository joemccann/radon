/**
 * @vitest-environment jsdom
 *
 * Tests that <ServiceHealthBanner /> renders ``stale`` rows distinctly
 * from ``error`` rows. ``stale`` is a softer signal — the worker is
 * silent, not crashed — so the banner uses an amber tone instead of red.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, cleanup, screen } from "@testing-library/react";

afterEach(() => {
  cleanup();
  vi.resetModules();
});

function mockUseServiceHealth(payload: {
  failing?: { service: string; state: string; last_error: string | null }[];
} | null): void {
  vi.doMock("@/lib/useServiceHealth", () => ({
    useServiceHealth: () => ({
      data: payload === null
        ? null
        : {
          services: payload.failing ?? [],
          failing: payload.failing ?? [],
          summary: {
            total: (payload.failing ?? []).length,
            failing_count: (payload.failing ?? []).length,
          },
        },
      loading: false,
    }),
  }));
}

describe("<ServiceHealthBanner /> stale variant", () => {
  it("renders a stale-tone banner when only stale rows are failing", async () => {
    mockUseServiceHealth({
      failing: [{ service: "newsfeed-scraper", state: "stale", last_error: null }],
    });
    const { default: Banner } = await import("../components/ServiceHealthBanner");
    render(<Banner />);
    const banner = screen.getByTestId("service-health-banner");
    // Both failure classes notify via the shared floating error stack.
    expect(banner.classList.contains("toast-error")).toBe(true);
    expect(banner.textContent).toContain("newsfeed-scraper");
    // The "stale" copy should make the silence explicit, not call it a
    // crash.
    expect(banner.textContent?.toLowerCase()).toMatch(/stale|silent|no recent/);
  });

  it("renders an error-tone banner when an error row is present (errors take precedence)", async () => {
    mockUseServiceHealth({
      failing: [
        { service: "cri-scan", state: "error", last_error: "WAL locked" },
        { service: "newsfeed-scraper", state: "stale", last_error: null },
      ],
    });
    const { default: Banner } = await import("../components/ServiceHealthBanner");
    render(<Banner />);
    const banner = screen.getByTestId("service-health-banner");
    expect(banner.classList.contains("toast-error")).toBe(true);
  });
});
