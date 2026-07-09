/**
 * @vitest-environment jsdom
 *
 * Options chain OrderBuilder layout contract:
 * - no redundant leg pills when editable rows exist
 * - tappable quote strip for combos (no duplicate BID/MID/ASK chip row)
 * - fixed action chip (no full-row SELL expansion class)
 * - limit label without em dash
 * - risk teaser before confirm
 * - compact skew
 */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { OrderPriceStrip } from "../lib/order/components/OrderPriceStrip";
import { readFileSync } from "node:fs";
import { join } from "node:path";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("OrderPriceStrip interactive", () => {
  it("calls onSelect when a quote side is clicked", () => {
    const onSelect = vi.fn();
    render(
      <OrderPriceStrip
        prices={{
          bid: -27.5,
          mid: -28.6,
          ask: -29.7,
          spread: -2.2,
          spreadPct: 7.7,
          available: true,
        }}
        selected="mid"
        onSelect={onSelect}
      />,
    );
    fireEvent.click(screen.getByTestId("order-price-select-bid"));
    expect(onSelect).toHaveBeenCalledWith("bid", -27.5);
    expect(screen.getByTestId("order-price-select-mid").className).toMatch(/selected/);
  });

  it("does not use em dashes in price placeholders", () => {
    render(
      <OrderPriceStrip
        prices={{ bid: null, mid: null, ask: null, spread: null, spreadPct: null, available: false }}
      />,
    );
    expect(screen.getByTestId("order-price-strip").textContent ?? "").not.toMatch(/\u2014|—/);
    expect(screen.getAllByText("--").length).toBeGreaterThan(0);
  });
});

describe("OrderBuilder source contract", () => {
  it("uses fixed leg-action class, compact skew, risk teaser, and no em-dash limit label", () => {
    const src = readFileSync(
      join(__dirname, "../components/ticker-detail/OptionsChainTab.tsx"),
      "utf8",
    );
    expect(src).toContain("order-builder-leg-action");
    expect(src).toContain("order-builder-risk-teaser");
    expect(src).toContain("compact");
    expect(src).toContain("Limit price (");
    expect(src).not.toContain("Limit Price —");
    // Pills removed from OrderBuilder body (import may still exist elsewhere).
    expect(src).not.toMatch(/OrderLegPills legs=\{unifiedLegs\}/);
    // Combo path: tappable strip with onSelect, no secondary chip row for combos.
    expect(src).toContain("onSelect={applyQuoteSide}");
    expect(src).toContain("!isCombo &&");
  });

  it("CSS packs leg rows without fr stretch tracks", () => {
    const css = readFileSync(join(__dirname, "../app/globals.css"), "utf8");
    const legBlock = css.slice(
      css.indexOf(".order-builder-leg {"),
      css.indexOf(".order-builder-leg-action {"),
    );
    expect(legBlock).toMatch(/display:\s*flex/);
    expect(legBlock).toMatch(/width:\s*fit-content/);
    expect(legBlock).not.toMatch(/\dfr/);
    expect(css).toMatch(/\.order-builder\s*\{[^}]*max-width:\s*560px/s);
  });
});
