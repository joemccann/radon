// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useChainAnchor } from "../lib/useChainAnchor";

type AnchorOptions = Parameters<typeof useChainAnchor>[0];

const defaultOptions: AnchorOptions = {
  ticker: "SPY",
  expiry: "2026-09-18",
  strikes: [90, 95, 100, 105, 110, 115, 120],
  currentPrice: 101,
  strikesPerSide: 10,
};

function renderAnchor(overrides: Partial<AnchorOptions> = {}) {
  return renderHook((options: AnchorOptions) => useChainAnchor(options), {
    initialProps: { ...defaultOptions, ...overrides },
  });
}

describe("useChainAnchor", () => {
  it("anchors to the first available quote and closest listed strike", () => {
    const { result } = renderAnchor();

    expect(result.current.anchorPrice).toBe(101);
    expect(result.current.anchorStrike).toBe(100);
  });

  it("waits for strikes before anchoring at the latest quote", () => {
    const { result, rerender } = renderAnchor({ strikes: [] });

    expect(result.current.anchorPrice).toBeNull();
    expect(result.current.anchorStrike).toBeNull();

    rerender({ ...defaultOptions, currentPrice: 109 });

    expect(result.current.anchorPrice).toBe(109);
    expect(result.current.anchorStrike).toBe(110);
  });

  it("waits for an expiry before initializing a chain view", () => {
    const { result, rerender } = renderAnchor({ expiry: null });

    expect(result.current.anchorPrice).toBeNull();
    expect(result.current.anchorStrike).toBeNull();

    rerender(defaultOptions);

    expect(result.current.anchorPrice).toBe(101);
    expect(result.current.anchorStrike).toBe(100);
  });

  it("can focus a known position before the underlying quote arrives", () => {
    const { result, rerender } = renderAnchor({
      currentPrice: null,
      focusKey: "position-95",
      focusStrike: 95,
    });
    const initialRevision = result.current.revision;

    expect(result.current.anchorPrice).toBe(95);
    expect(result.current.anchorStrike).toBe(95);

    rerender({ ...defaultOptions, focusKey: "position-95", focusStrike: 95 });

    expect(result.current.anchorPrice).toBe(95);
    expect(result.current.anchorStrike).toBe(95);
    expect(result.current.revision).toBe(initialRevision);
  });

  it("keeps rows anchored across price moves in both directions", () => {
    const { result, rerender } = renderAnchor();
    const initialRevision = result.current.revision;

    act(() => result.current.markBrowsing());
    for (const currentPrice of [109, 94, 119, 101]) {
      rerender({ ...defaultOptions, currentPrice });
      expect(result.current.anchorPrice).toBe(101);
      expect(result.current.anchorStrike).toBe(100);
      expect(result.current.revision).toBe(initialRevision);
    }
  });

  it("does not recenter on reconstructed strike arrays or quote reconnects", () => {
    const { result, rerender } = renderAnchor();
    const initialRevision = result.current.revision;

    rerender({ ...defaultOptions, strikes: [...defaultOptions.strikes], currentPrice: null });
    rerender({ ...defaultOptions, strikes: [...defaultOptions.strikes], currentPrice: 119 });

    expect(result.current.anchorPrice).toBe(101);
    expect(result.current.anchorStrike).toBe(100);
    expect(result.current.revision).toBe(initialRevision);
  });

  it("explicitly recenters at the latest live quote", () => {
    const { result, rerender } = renderAnchor();
    const initialRevision = result.current.revision;

    act(() => result.current.markBrowsing());
    rerender({ ...defaultOptions, currentPrice: 114 });
    act(() => result.current.recenter());

    expect(result.current.anchorPrice).toBe(114);
    expect(result.current.anchorStrike).toBe(115);
    expect(result.current.revision).not.toBe(initialRevision);
  });

  it("restores pane positioning when Recenter is pressed at an unchanged price", () => {
    const { result } = renderAnchor();
    const initialRevision = result.current.revision;

    act(() => result.current.markBrowsing());
    act(() => result.current.recenter());

    expect(result.current.anchorPrice).toBe(101);
    expect(result.current.anchorStrike).toBe(100);
    expect(result.current.revision).not.toBe(initialRevision);
  });

  it.each<Partial<AnchorOptions>>([
    { ticker: "QQQ" },
    { expiry: "2026-09-25" },
    { strikesPerSide: 20 },
    { focusKey: "position-2" },
  ])("resets the view for an explicit context change: %j", (change) => {
    const { result, rerender } = renderAnchor();
    const initialRevision = result.current.revision;

    act(() => result.current.markBrowsing());
    rerender({ ...defaultOptions, ...change, currentPrice: 114 });

    expect(result.current.anchorPrice).toBe(114);
    expect(result.current.anchorStrike).toBe(115);
    expect(result.current.revision).not.toBe(initialRevision);
  });

  it("preserves the position anchor when a quote changes the nearest focused leg", () => {
    const focusedOptions = { ...defaultOptions, focusKey: "spread-95-115", focusStrike: 95 };
    const { result, rerender } = renderAnchor(focusedOptions);
    const initialRevision = result.current.revision;

    expect(result.current.anchorPrice).toBe(95);
    expect(result.current.anchorStrike).toBe(95);

    rerender({ ...focusedOptions, currentPrice: 114, focusStrike: 115 });

    expect(result.current.anchorPrice).toBe(95);
    expect(result.current.anchorStrike).toBe(95);
    expect(result.current.revision).toBe(initialRevision);

    act(() => result.current.recenter());

    expect(result.current.anchorPrice).toBe(114);
    expect(result.current.anchorStrike).toBe(115);
  });

  it.each([null, Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY, 0, -1])(
    "does not anchor or recenter at an unavailable quote (%s)",
    (currentPrice) => {
      const { result, rerender } = renderAnchor({ currentPrice });

      expect(result.current.anchorPrice).toBeNull();
      expect(result.current.anchorStrike).toBeNull();

      rerender(defaultOptions);
      const anchoredRevision = result.current.revision;
      rerender({ ...defaultOptions, currentPrice });
      act(() => result.current.recenter());

      expect(result.current.anchorPrice).toBe(101);
      expect(result.current.anchorStrike).toBe(100);
      expect(result.current.revision).toBe(anchoredRevision);
    },
  );

  it("uses a late first valid quote when the user has not started browsing", () => {
    const { result, rerender } = renderAnchor({ currentPrice: null });

    rerender({ ...defaultOptions, currentPrice: 109 });

    expect(result.current.anchorPrice).toBe(109);
    expect(result.current.anchorStrike).toBe(110);
  });

  it("leaves an unpriced view undisturbed after browsing until explicit Recenter", () => {
    const { result, rerender } = renderAnchor({ currentPrice: null });
    const browsingRevision = result.current.revision;

    act(() => result.current.markBrowsing());
    rerender({ ...defaultOptions, currentPrice: 109 });

    expect(result.current.anchorPrice).toBeNull();
    expect(result.current.anchorStrike).toBeNull();
    expect(result.current.revision).toBe(browsingRevision);

    act(() => result.current.recenter());

    expect(result.current.anchorPrice).toBe(109);
    expect(result.current.anchorStrike).toBe(110);
    expect(result.current.revision).not.toBe(browsingRevision);
  });
});
