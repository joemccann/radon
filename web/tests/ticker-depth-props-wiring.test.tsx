/**
 * @vitest-environment jsdom
 *
 * T-070: live L2 delivery into the cockpit book, asserted by rendering.
 *
 * The prior version of this file matched three `.tsx` sources with regexes, so
 * it passed on any wiring that reads correctly and delivers nothing (a memo
 * comparator that omits `depths`, a child that never re-renders) and failed on
 * a pure rename. These cases render the real memoized `WorkspaceSections` →
 * `TickerWorkspace` chain with the leaf stubbed, and assert the book actually
 * arrives and keeps arriving when it changes.
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { cleanup, render, screen } from "@testing-library/react";
import { TickerDetailProvider } from "@/lib/TickerDetailContext";
import type { DepthBook, Trade } from "@/lib/pricesProtocol";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), back: vi.fn(), push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(""),
}));

let leafRenders = 0;

vi.mock("../components/TickerDetailContent", () => ({
  default: ({
    depths,
    tape,
  }: {
    depths?: Record<string, DepthBook>;
    tape?: Record<string, Trade[]>;
  }) => {
    leafRenders += 1;
    const symbols = Object.keys(depths ?? {}).join(",");
    const insideBid = depths?.SPY?.bid?.[0]?.price ?? null;
    const prints = Object.keys(tape ?? {}).join(",");
    return React.createElement(
      "div",
      { "data-testid": "book-leaf" },
      `symbols=${symbols} insideBid=${insideBid} tape=${prints} renders=${leafRenders}`,
    );
  },
}));

// Imported after the mocks so the stubbed leaf is the one the chain resolves.
const { default: WorkspaceSections } = await import("../components/WorkspaceSections");

function book(insideBid: number): DepthBook {
  return {
    symbol: "SPY",
    kind: "stock",
    bid: [{ price: insideBid, size: 400, marketMaker: "NSDQ", exchange: "NASDAQ" }],
    ask: [{ price: insideBid + 0.01, size: 300, marketMaker: "ARCA", exchange: "ARCA" }],
    isSmartDepth: true,
    feed: "SMART DEPTH · TOTALVIEW",
    entitled: true,
    timestamp: "2026-08-17T13:45:00Z",
  };
}

const TAPE: Record<string, Trade[]> = {
  SPY: [{ price: 640.12, size: 100, exchange: "ARCA", time: "2026-08-17T13:45:00Z" }],
};

function renderCockpit(depths?: Record<string, DepthBook>, tape?: Record<string, Trade[]>) {
  return render(
    <TickerDetailProvider>
      <WorkspaceSections section="ticker-detail" tickerParam="SPY" theme="dark" depths={depths} tape={tape} />
    </TickerDetailProvider>,
  );
}

afterEach(() => {
  cleanup();
  leafRenders = 0;
});

describe("ticker book depth wiring", () => {
  it("delivers the focused symbol's book through the memoized section chain", () => {
    renderCockpit({ SPY: book(640.11) }, TAPE);

    const text = screen.getByTestId("book-leaf").textContent ?? "";
    expect(text).toContain("symbols=SPY");
    expect(text).toContain("insideBid=640.11");
    expect(text).toContain("tape=SPY");
  });

  it("re-renders the book when the depth snapshot changes", () => {
    const { rerender } = renderCockpit({ SPY: book(640.11) }, TAPE);
    expect(screen.getByTestId("book-leaf").textContent).toContain("insideBid=640.11");
    const before = leafRenders;

    rerender(
      <TickerDetailProvider>
        <WorkspaceSections
          section="ticker-detail"
          tickerParam="SPY"
          theme="dark"
          depths={{ SPY: book(640.25) }}
          tape={TAPE}
        />
      </TickerDetailProvider>,
    );

    expect(screen.getByTestId("book-leaf").textContent).toContain("insideBid=640.25");
    expect(leafRenders).toBeGreaterThan(before);
  });

  it("prefers the shell-supplied book over the context fallback", () => {
    // getDepths() on a bare provider is empty; the prop must be what lands.
    renderCockpit({ SPY: book(639.5) }, TAPE);
    expect(screen.getByTestId("book-leaf").textContent).toContain("insideBid=639.5");
  });

  it("falls back to context depths when the shell passes none", () => {
    renderCockpit(undefined, undefined);
    const text = screen.getByTestId("book-leaf").textContent ?? "";
    expect(text).toContain("symbols=");
    expect(text).toContain("insideBid=null");
  });

  it("grows the cockpit book grid so the montage is not a header over empty space", () => {
    const css = readFileSync(join(import.meta.dirname, "..", "app/globals.css"), "utf8");
    expect(css).toMatch(/\.book-region \.book-window \.book-body-grid \{[^}]*flex:\s*1 1 auto/s);
  });
});
