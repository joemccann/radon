/**
 * @vitest-environment jsdom
 */

import { act } from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetRiskFreeRateCacheForTests, useRiskFreeRate } from "../lib/useRiskFreeRate";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function RateProbe({ id }: { id: string }) {
  const rate = useRiskFreeRate();
  return <output data-testid={id}>{rate}</output>;
}

beforeEach(() => {
  resetRiskFreeRateCacheForTests();
});

afterEach(() => {
  cleanup();
  resetRiskFreeRateCacheForTests();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("useRiskFreeRate", () => {
  it("shares one no-store request and cached result across concurrent consumers", async () => {
    let now = Date.parse("2026-08-25T12:00:00Z");
    vi.spyOn(Date, "now").mockImplementation(() => now);
    let resolvePayload: ((value: { rate: number; source: "FRED:DFF"; stale: false }) => void) | null = null;
    const payload = new Promise<{ rate: number; source: "FRED:DFF"; stale: false }>((resolve) => {
      resolvePayload = resolve;
    });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => payload,
    });
    vi.stubGlobal("fetch", fetchMock);

    const first = render(
      <>
        <RateProbe id="rate-a" />
        <RateProbe id="rate-b" />
        <RateProbe id="rate-c" />
      </>,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledWith("/api/risk-free-rate", { cache: "no-store" });

    await act(async () => {
      resolvePayload?.({ rate: 0.0435, source: "FRED:DFF", stale: false });
      await payload;
      await Promise.resolve();
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(screen.getByTestId("rate-a").textContent).toBe("0.0435");
      expect(screen.getByTestId("rate-b").textContent).toBe("0.0435");
      expect(screen.getByTestId("rate-c").textContent).toBe("0.0435");
    });

    first.unmount();
    render(<RateProbe id="rate-late" />);
    expect(screen.getByTestId("rate-late").textContent).toBe("0.0435");
    expect(fetchMock).toHaveBeenCalledTimes(1);

    cleanup();
    now += 24 * 60 * 60 * 1000 + 1;
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ rate: 0.044, source: "FRED:DFF", stale: false }),
    });
    render(<RateProbe id="rate-stale" />);

    expect(screen.getByTestId("rate-stale").textContent).toBe("0.0435");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByTestId("rate-stale").textContent).toBe("0.044"));
  });

  it("does not cache a stale fallback as a fresh zero and preserves the last-good rate", async () => {
    let now = Date.parse("2026-08-25T12:00:00Z");
    vi.spyOn(Date, "now").mockImplementation(() => now);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ rate: 0.0435, source: "FRED:DFF", stale: false }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ rate: 0, source: "fallback", stale: true }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ rate: 0.044, source: "FRED:DFF", stale: false }),
      });
    vi.stubGlobal("fetch", fetchMock);

    const initial = render(<RateProbe id="rate-initial" />);
    await waitFor(() => expect(screen.getByTestId("rate-initial").textContent).toBe("0.0435"));
    initial.unmount();

    now += 24 * 60 * 60 * 1000 + 1;
    const fallback = render(<RateProbe id="rate-fallback" />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId("rate-fallback").textContent).toBe("0.0435");
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    fallback.unmount();

    render(<RateProbe id="rate-retry" />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    await waitFor(() => expect(screen.getByTestId("rate-retry").textContent).toBe("0.044"));
  });
});
