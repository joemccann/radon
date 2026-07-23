/**
 * @vitest-environment jsdom
 *
 * CompanyTab — the one-shot info fetch (fetched=true blocks retry) must
 * refetch when connectivity returns after an offline failure.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, cleanup, waitFor } from "@testing-library/react";

afterEach(() => {
  cleanup();
  vi.resetModules();
  vi.unstubAllGlobals();
});

type OfflineValue = { offline: boolean; cachedAt: number | null; offlineSince: number | null };

async function loadCompanyTab(offlineRef: { current: OfflineValue }) {
  vi.doMock("@/lib/offline/OfflineStatusContext", () => ({
    useOfflineStatus: () => offlineRef.current,
  }));
  vi.doMock("@/lib/order/hooks/useShortAvailability", () => ({
    useShortAvailability: () => ({ data: null }),
  }));
  const { default: CompanyTab } = await import("../components/ticker-detail/CompanyTab");
  return CompanyTab;
}

describe("CompanyTab offline retry", () => {
  it("refetches after an offline→online transition when the fetch had failed", async () => {
    const offlineRef = {
      current: { offline: false, cachedAt: null, offlineSince: null } as OfflineValue,
    };
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fetchMock);
    const CompanyTab = await loadCompanyTab(offlineRef);

    const { container, rerender } = render(
      <CompanyTab ticker="AAPL" active priceData={null} fundamentals={null} />,
    );
    await waitFor(() => expect(container.querySelector(".tab-error")).not.toBeNull());
    expect(fetchMock).toHaveBeenCalledTimes(1);

    offlineRef.current = { offline: true, cachedAt: null, offlineSince: 1 };
    rerender(<CompanyTab ticker="AAPL" active priceData={null} fundamentals={null} />);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    offlineRef.current = { offline: false, cachedAt: null, offlineSince: null };
    rerender(<CompanyTab ticker="AAPL" active priceData={null} fundamentals={null} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });
});
