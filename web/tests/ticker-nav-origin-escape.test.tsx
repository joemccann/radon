/**
 * @vitest-environment jsdom
 *
 * Ticker navigation must stay on this origin.
 *
 * `/${segment}` is a root-relative push only while the segment itself does not
 * start with `/`. A segment that does yields `//host`, a protocol-relative URL
 * the browser resolves off-origin BEFORE `app/[ticker]/page.tsx` can reject it,
 * so the server route guard never sees the navigation. Encoding the segment at
 * the single navigation chokepoint keeps every accepted ticker byte-identical
 * (equity symbols and the index registry are alphanumeric) while any other
 * shape stays on-origin and 404s as it already does.
 */
import React, { forwardRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { INDEX_SYMBOLS } from "@/lib/indexSymbols";
import { tickerHref } from "@/lib/tickerRoute";
import { useTickerNav } from "@/lib/useTickerNav";

const push = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/components/TickerSearch", () => ({
  default: forwardRef<HTMLInputElement, { onSelect: (symbol: string) => void; placeholder: string }>(
    function Search({ onSelect, placeholder }, ref) {
      return <input ref={ref} aria-label={placeholder} onKeyDown={(event) => {
        if (event.key === "Enter") onSelect(event.currentTarget.value);
      }} />;
    },
  ),
}));
import MobileTickerSearch from "../components/mobile/MobileTickerSearch";

afterEach(() => { cleanup(); push.mockClear(); });

const OFF_ORIGIN = "/evil.example";

describe("tickerHref — navigation chokepoint", () => {
  it("leaves every equity ticker shape byte-identical", () => {
    for (const symbol of ["A", "AA", "SPY", "NVDA", "GOOGL"]) {
      expect(tickerHref(symbol)).toBe(`/${symbol}`);
    }
  });

  it("leaves every registered index symbol byte-identical", () => {
    for (const symbol of Object.keys(INDEX_SYMBOLS)) {
      expect(tickerHref(symbol)).toBe(`/${symbol}`);
    }
  });

  it("keeps a leading-slash segment on this origin", () => {
    const href = tickerHref(OFF_ORIGIN);
    expect(href.startsWith("//")).toBe(false);
    expect(new URL(href, "https://app.radon.run").origin).toBe("https://app.radon.run");
  });

  it("appends posId as a query parameter", () => {
    expect(tickerHref("SPY", 42)).toBe("/SPY?posId=42");
  });
});

describe("useTickerNav", () => {
  it("pushes an unchanged path for a valid ticker", () => {
    const { result } = renderHook(() => useTickerNav());
    result.current.navigateToTicker("spy", 7);
    expect(push).toHaveBeenCalledWith("/SPY?posId=7");
  });

  it("never pushes a protocol-relative path", () => {
    const { result } = renderHook(() => useTickerNav());
    result.current.navigateToTicker(OFF_ORIGIN);
    const pushed = push.mock.calls[0][0] as string;
    expect(pushed.startsWith("//")).toBe(false);
    expect(new URL(pushed, "https://app.radon.run").origin).toBe("https://app.radon.run");
  });
});

describe("MobileTickerSearch", () => {
  it("never pushes a protocol-relative path on select", () => {
    render(<MobileTickerSearch open onClose={vi.fn()} inputRef={React.createRef<HTMLInputElement>()} />);
    const input = screen.getByRole("textbox", { name: "Search ticker" });
    fireEvent.change(input, { target: { value: OFF_ORIGIN } });
    fireEvent.keyDown(input, { key: "Enter" });
    const pushed = push.mock.calls[0][0] as string;
    expect(pushed.startsWith("//")).toBe(false);
    expect(new URL(pushed, "https://app.radon.run").origin).toBe("https://app.radon.run");
  });
});
