// @vitest-environment jsdom

import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";

import {
  deriveMicrostructure,
  type Microstructure,
} from "../lib/book/microstructure";
import { MicrostructureStrip } from "../components/ticker-detail/MicrostructureStrip";
import type { DepthBook, DepthLevel } from "../lib/pricesProtocol";

afterEach(cleanup);

function level(price: number, size: number): DepthLevel {
  return { price, size, marketMaker: null, exchange: null };
}

function book(bid: DepthLevel[], ask: DepthLevel[]): DepthBook {
  return {
    symbol: "RKLB",
    kind: "stock",
    bid,
    ask,
    isSmartDepth: true,
    feed: "SMART DEPTH",
    entitled: true,
    timestamp: "2026-06-21T15:00:00Z",
  };
}

/* ── Derivation ── */

describe("deriveMicrostructure", () => {
  it("computes imbalance + microprice for a two-sided book", () => {
    const depth = book(
      [level(99.0, 100), level(98.0, 50)],
      [level(101.0, 300), level(102.0, 50)],
    );
    const m = deriveMicrostructure(depth) as Microstructure;
    // imbalance = (150 - 350)/500 = -0.4
    expect(m.imbalance).toBeCloseTo(-0.4, 10);
    // microprice = (99*300 + 101*100)/400 = 99.5
    expect(m.microprice).toBeCloseTo(99.5, 10);
    expect(m.mid).toBeCloseTo(100.0, 10);
    expect(m.microPremium).toBeCloseTo(-0.5, 10);
  });

  it("is neutral for a symmetric book", () => {
    const depth = book(
      [level(99.0, 100), level(98.0, 100)],
      [level(101.0, 100), level(102.0, 100)],
    );
    const m = deriveMicrostructure(depth) as Microstructure;
    expect(m.imbalance).toBe(0);
    expect(m.microprice).toBeCloseTo(100.0, 10);
    expect(m.microPremium).toBeCloseTo(0, 10);
  });

  it("respects top_n truncation", () => {
    const depth = book(
      [level(99.0, 100), level(98.0, 9999)],
      [level(101.0, 100), level(102.0, 9999)],
    );
    const m = deriveMicrostructure(depth, 1) as Microstructure;
    expect(m.imbalance).toBe(0);
  });

  it("saturates to +1 with an empty ask side", () => {
    const depth = book([level(99.0, 100)], []);
    const m = deriveMicrostructure(depth) as Microstructure;
    expect(m.imbalance).toBe(1);
    // microprice undefined without a best ask
    expect(m.microprice).toBeNull();
  });

  it("returns null for an empty book", () => {
    expect(deriveMicrostructure(book([], []))).toBeNull();
  });

  it("returns null when depth is absent or unentitled", () => {
    expect(deriveMicrostructure(null)).toBeNull();
    expect(
      deriveMicrostructure({ ...book([level(99, 1)], [level(101, 1)]), entitled: false }),
    ).toBeNull();
  });
});

/* ── Strip ── */

describe("MicrostructureStrip", () => {
  it("renders imbalance + microprice values", () => {
    const depth = book(
      [level(99.0, 100), level(98.0, 50)],
      [level(101.0, 300), level(102.0, 50)],
    );
    const { container } = render(<MicrostructureStrip depth={depth} />);
    const text = container.textContent ?? "";
    expect(text).toContain("IMBALANCE");
    expect(text).toContain("MICROPRICE");
    // -0.40 imbalance formatted, 99.50 microprice
    expect(text).toContain("99.50");
    // sign / direction label for an ask-heavy book
    expect(text.toUpperCase()).toContain("ASK");
  });

  it("renders nothing when there is no entitled depth", () => {
    const { container } = render(<MicrostructureStrip depth={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("uses brand tokens, never raw hex", () => {
    const depth = book([level(99.0, 100)], [level(101.0, 100)]);
    const { container } = render(<MicrostructureStrip depth={depth} />);
    expect(container.innerHTML).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });
});
