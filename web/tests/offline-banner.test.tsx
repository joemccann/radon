/**
 * @vitest-environment jsdom
 *
 * <OfflineBanner /> — hidden when online, terse OFFLINE readout with data age
 * when offline. doMock-before-dynamic-import per service-health-banner pattern.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, cleanup, screen } from "@testing-library/react";

afterEach(() => {
  cleanup();
  vi.resetModules();
});

function mockOfflineStatus(value: {
  offline: boolean;
  cachedAt: number | null;
  offlineSince: number | null;
}): void {
  vi.doMock("@/lib/offline/OfflineStatusContext", () => ({
    useOfflineStatus: () => value,
  }));
}

describe("<OfflineBanner />", () => {
  it("renders nothing when online", async () => {
    mockOfflineStatus({ offline: false, cachedAt: null, offlineSince: null });
    const { default: Banner } = await import("../components/OfflineBanner");
    const { container } = render(<Banner />);
    expect(container.firstChild).toBeNull();
  });

  it("renders OFFLINE + last-known-data copy without an age when cachedAt is null", async () => {
    mockOfflineStatus({ offline: true, cachedAt: null, offlineSince: 1_700_000_000_000 });
    const { default: Banner } = await import("../components/OfflineBanner");
    render(<Banner />);
    const el = screen.getByTestId("offline-banner");
    expect(el.getAttribute("role")).toBe("status");
    expect(el.textContent).toContain("OFFLINE");
    expect(el.textContent).toContain("showing last known data");
    expect(el.textContent).not.toContain("as of");
  });

  it("renders the data age when cachedAt is set", async () => {
    mockOfflineStatus({
      offline: true,
      cachedAt: 1_700_000_000_000,
      offlineSince: 1_700_000_000_000,
    });
    const { default: Banner } = await import("../components/OfflineBanner");
    render(<Banner />);
    const el = screen.getByTestId("offline-banner");
    expect(el.textContent).toMatch(/as of \d{2}:\d{2}:\d{2}/);
  });

  it("copy contains no em dashes", async () => {
    mockOfflineStatus({
      offline: true,
      cachedAt: 1_700_000_000_000,
      offlineSince: 1_700_000_000_000,
    });
    const { default: Banner } = await import("../components/OfflineBanner");
    render(<Banner />);
    expect(screen.getByTestId("offline-banner").textContent).not.toMatch(/—/);
  });
});
