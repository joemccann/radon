/**
 * @vitest-environment jsdom
 *
 * OrderQuoteTelemetry — the single nine-field quote panel every order surface
 * renders (new order entry, order modification, chain tickets, mobile tickets).
 * Same model, same formatter, same closed-market fallback as the portfolio
 * position drawer.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { OrderQuoteTelemetry } from "@/components/QuoteTelemetry";
import type { PriceData } from "@/lib/pricesProtocol";
import {
  buildQuoteTelemetryModel,
  comboQuotePriceData,
  type QuoteFallback,
} from "@/lib/quoteTelemetry";

afterEach(cleanup);

const NINE_LABELS = ["BID", "MID", "ASK", "SPREAD", "VOLUME", "HIGH", "LOW", "DAY"];

function makePriceData(overrides: Partial<PriceData> & { symbol: string }): PriceData {
  return {
    last: null,
    lastIsCalculated: false,
    bid: null,
    ask: null,
    bidSize: null,
    askSize: null,
    volume: null,
    high: null,
    low: null,
    open: null,
    close: null,
    week52High: null,
    week52Low: null,
    avgVolume: null,
    delta: null,
    gamma: null,
    theta: null,
    vega: null,
    impliedVol: null,
    undPrice: null,
    timestamp: new Date().toISOString(),
    ...overrides,
  };
}

const LIVE_QUOTE = makePriceData({
  symbol: "META_20260828_550_P",
  bid: 12.8,
  ask: 13.2,
  last: 13.0,
  close: 12.5,
  volume: 4231,
  high: 14.1,
  low: 12.4,
});

function labelsIn(container: HTMLElement): string[] {
  return [...container.querySelectorAll(".price-bar-label")].map((n) => n.textContent ?? "");
}

describe("OrderQuoteTelemetry", () => {
  it("renders all nine telemetry fields", () => {
    const { container } = render(<OrderQuoteTelemetry priceData={LIVE_QUOTE} />);
    const labels = labelsIn(container);
    for (const label of NINE_LABELS) {
      expect(labels).toContain(label);
    }
    expect(labels).toContain("LAST");
    expect(labels).toHaveLength(9);
  });

  it("renders the optional instrument label above the fields", () => {
    const { container } = render(
      <OrderQuoteTelemetry priceData={LIVE_QUOTE} label="META 2026-08-28 $550 P" />,
    );
    expect(labelsIn(container)[0]).toBe("META 2026-08-28 $550 P");
    expect(labelsIn(container)).toHaveLength(10);
  });

  it("renders all nine fields at tight density and tags the container", () => {
    const { container } = render(<OrderQuoteTelemetry priceData={LIVE_QUOTE} density="tight" />);
    const bar = container.querySelector(".price-bar");
    expect(bar).not.toBeNull();
    expect(bar?.classList.contains("price-bar--tight")).toBe(true);
    expect(labelsIn(container)).toHaveLength(9);
    for (const label of NINE_LABELS) {
      expect(labelsIn(container)).toContain(label);
    }
  });

  it("renders the no-real-time-data empty state instead of a grid of dashes", () => {
    const { container } = render(<OrderQuoteTelemetry priceData={null} />);
    expect(screen.getByText("No real-time data")).toBeTruthy();
    expect(container.querySelectorAll(".price-bar-label")).toHaveLength(0);
  });

  it("tones DAY positive when the quote is above the prior close", () => {
    const { container } = render(<OrderQuoteTelemetry priceData={LIVE_QUOTE} />);
    const dayValue = [...container.querySelectorAll(".price-bar-item")]
      .find((item) => item.querySelector(".price-bar-label")?.textContent === "DAY")
      ?.querySelector(".price-bar-value");
    expect(dayValue?.className).toContain("positive");
    expect(dayValue?.textContent).toContain("+4.00%");
  });

  it("tones DAY negative when the quote is below the prior close", () => {
    const { container } = render(
      <OrderQuoteTelemetry priceData={makePriceData({ ...LIVE_QUOTE, last: 11.0 })} />,
    );
    const dayValue = [...container.querySelectorAll(".price-bar-item")]
      .find((item) => item.querySelector(".price-bar-label")?.textContent === "DAY")
      ?.querySelector(".price-bar-value");
    expect(dayValue?.className).toContain("negative");
    expect(dayValue?.textContent).toContain("-12.00%");
  });

  it("labels LAST as CLOSE on the closed-market fallback path", () => {
    const fallback: QuoteFallback = {
      open: 610,
      high: 618.4,
      low: 604.2,
      close: 615.5,
      volume: 12_400_000,
      prevClose: 600,
    };
    const { container } = render(<OrderQuoteTelemetry priceData={null} fallback={fallback} />);
    const labels = labelsIn(container);
    expect(labels).toContain("CLOSE");
    expect(labels).not.toContain("LAST");
    expect(labels).toHaveLength(9);
    expect(screen.getByText("$615.50")).toBeTruthy();
  });

  it("accepts a pre-built model so combo tickets with no single PriceData can render", () => {
    const netQuote = comboQuotePriceData({ symbol: "AAOI", bid: -1.2, ask: -0.8, last: -1.0 });
    const model = buildQuoteTelemetryModel(netQuote);
    const { container } = render(<OrderQuoteTelemetry model={model} label="AAOI RR" />);
    expect(labelsIn(container)).toContain("SPREAD");
    expect(labelsIn(container)).toHaveLength(10);
  });
});
