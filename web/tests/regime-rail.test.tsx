/**
 * @vitest-environment jsdom
 *
 * Grouped indicator rail for /regime (design: "Regime — Grouped Rail").
 * The flat 19-button tab strip becomes a grouped left rail on wide desktop:
 * Composite / Volatility / Positioning / Breadth & sentiment / Models, with a
 * filter input, per-group flagged counts, per-indicator status dot + value,
 * and a footer count line. Navigation stays URL-driven via the same routes.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";
import React from "react";
import { render, cleanup, fireEvent, within } from "@testing-library/react";
import {
  REGIME_RAIL_GROUPS,
  REGIME_TABS,
  REGIME_TAB_LABEL,
  buildRailStatuses,
} from "../lib/regimeRail";
import type { VcgData } from "../lib/useVcg";
import type { GexData } from "../lib/useGex";
import RegimeRail from "../components/RegimeRail";

const ROOT = join(__dirname, "..");
const read = (rel: string) => readFileSync(join(ROOT, rel), "utf-8");

/* ─── 1. Registry ──────────────────────────────────────── */

describe("REGIME_RAIL_GROUPS — grouped registry covers every tab exactly once", () => {
  it("uses the five design group labels in order", () => {
    expect(REGIME_RAIL_GROUPS.map((g) => g.label)).toEqual([
      "Composite",
      "Volatility",
      "Positioning",
      "Breadth & sentiment",
      "Models",
    ]);
  });

  it("flattens to all 20 regime tabs with no duplicates", () => {
    expect(REGIME_TABS).toHaveLength(20);
    expect(new Set(REGIME_TABS).size).toBe(20);
    expect([...REGIME_TABS].sort()).toEqual(
      ["cri", "vcg", "gex", "grg", "breadth", "bpi", "margin", "credit", "straddle", "cor", "vixcor", "ivrank", "skew", "skew2d", "curve", "cot", "ats", "short", "llm", "backtest"].sort(),
    );
  });

  it("groups match the design assignment", () => {
    const groupOf = Object.fromEntries(
      REGIME_RAIL_GROUPS.flatMap((g) => g.tabs.map((t) => [t, g.label])),
    );
    expect(groupOf.cri).toBe("Composite");
    expect(groupOf.grg).toBe("Composite");
    expect(groupOf.vcg).toBe("Composite");
    expect(groupOf.vixcor).toBe("Volatility");
    expect(groupOf.ivrank).toBe("Volatility");
    expect(groupOf.skew).toBe("Volatility");
    expect(groupOf.skew2d).toBe("Volatility");
    expect(groupOf.curve).toBe("Volatility");
    expect(groupOf.straddle).toBe("Volatility");
    expect(groupOf.gex).toBe("Positioning");
    expect(groupOf.margin).toBe("Positioning");
    expect(groupOf.credit).toBe("Positioning");
    expect(groupOf.cot).toBe("Positioning");
    expect(groupOf.short).toBe("Positioning");
    expect(groupOf.ats).toBe("Positioning");
    expect(groupOf.breadth).toBe("Breadth & sentiment");
    expect(groupOf.bpi).toBe("Breadth & sentiment");
    expect(groupOf.cor).toBe("Breadth & sentiment");
    expect(groupOf.llm).toBe("Models");
    expect(groupOf.backtest).toBe("Models");
  });

  it("every tab has a display label", () => {
    for (const tab of REGIME_TABS) {
      expect(REGIME_TAB_LABEL[tab]).toBeTruthy();
    }
    expect(REGIME_TAB_LABEL.bpi).toBe("BULLISH %");
    expect(REGIME_TAB_LABEL.skew2d).toBe("SKEW 2D");
    expect(REGIME_TAB_LABEL.vixcor).toBe("VIX-COR");
    expect(REGIME_TAB_LABEL.ivrank).toBe("IV RANK");
    expect(REGIME_TAB_LABEL.credit).toBe("CREDIT");
  });
});

/* ─── 2. Status derivation ─────────────────────────────── */

const vcgData = (interpretation: string): VcgData =>
  ({ signal: { interpretation, regime: "CONTANGO", pi_panic: 0.12 } }) as unknown as VcgData;

const gexData = (direction: string): GexData =>
  ({ ticker: "SPY", spot: 776, bias: { direction }, levels: {}, expected_range: null }) as unknown as GexData;

describe("buildRailStatuses — pure derivation from loaded payloads", () => {
  it("returns empty for null inputs", () => {
    expect(buildRailStatuses({})).toEqual({});
    expect(buildRailStatuses({ cri: null, cor1m: null, vcg: null, gex: null })).toEqual({});
  });

  it("derives CRI value + tone from score/level", () => {
    expect(buildRailStatuses({ cri: { score: 3.2, level: "LOW" } }).cri).toEqual({ value: "3", tone: "core" });
    expect(buildRailStatuses({ cri: { score: 42, level: "ELEVATED" } }).cri).toEqual({ value: "42", tone: "warn" });
    expect(buildRailStatuses({ cri: { score: 71, level: "HIGH" } }).cri).toEqual({ value: "71", tone: "fault" });
    expect(buildRailStatuses({ cri: { score: 88, level: "CRITICAL" } }).cri).toEqual({ value: "88", tone: "fault" });
  });

  it("derives COR value with the >60 crash-trigger threshold", () => {
    expect(buildRailStatuses({ cor1m: 7.45 }).cor).toEqual({ value: "7.45", tone: "neutral" });
    expect(buildRailStatuses({ cor1m: 62.1 }).cor).toEqual({ value: "62.10", tone: "warn" });
  });

  it("derives VCG from the signal interpretation", () => {
    expect(buildRailStatuses({ vcg: vcgData("NOMINAL") }).vcg).toEqual({ value: "NOMINAL", tone: "core" });
    expect(buildRailStatuses({ vcg: vcgData("EDR") }).vcg).toEqual({ value: "EDR", tone: "warn" });
    expect(buildRailStatuses({ vcg: vcgData("RISK_OFF") }).vcg).toEqual({ value: "RISK OFF", tone: "fault" });
  });

  it("derives GEX from the bias direction", () => {
    expect(buildRailStatuses({ gex: gexData("BULL") }).gex).toEqual({ value: "BULL", tone: "core" });
    expect(buildRailStatuses({ gex: gexData("NEUTRAL") }).gex).toEqual({ value: "NEUTRAL", tone: "warn" });
    expect(buildRailStatuses({ gex: gexData("CAUTIOUS_BEAR") }).gex).toEqual({ value: "CAUTIOUS BEAR", tone: "fault" });
  });
});

/* ─── 3. Rail component ────────────────────────────────── */

describe("RegimeRail — grouped rail rendering + navigation", () => {
  afterEach(cleanup);

  const renderRail = (over: Partial<React.ComponentProps<typeof RegimeRail>> = {}) => {
    const onSelect = vi.fn();
    const utils = render(
      <RegimeRail activeTab="cri" onSelect={onSelect} statuses={{}} {...over} />,
    );
    return { onSelect, ...utils };
  };

  it("renders all five group headers and 20 items", () => {
    const { container } = renderRail();
    for (const g of REGIME_RAIL_GROUPS) {
      expect(within(container).getByText(g.label)).toBeTruthy();
    }
    expect(container.querySelectorAll("[data-tab]")).toHaveLength(20);
  });

  it("marks the active tab with the active class and aria-current", () => {
    const { container } = renderRail({ activeTab: "gex" });
    const item = container.querySelector('[data-tab="gex"]')!;
    expect(item.className).toMatch(/active/);
    expect(item.getAttribute("aria-current")).toBe("page");
    expect(container.querySelector('[data-tab="cri"]')!.className).not.toMatch(/active/);
  });

  it("clicking an item selects its tab", () => {
    const { container, onSelect } = renderRail();
    fireEvent.click(container.querySelector('[data-tab="skew2d"]')!);
    expect(onSelect).toHaveBeenCalledWith("skew2d");
  });

  it("renders status values and flags elevated groups", () => {
    const { container } = renderRail({
      statuses: {
        cri: { value: "3", tone: "core" },
        vcg: { value: "EDR", tone: "warn" },
        gex: { value: "BEAR", tone: "fault" },
      },
    });
    expect(within(container.querySelector('[data-tab="cri"]') as HTMLElement).getByText("3")).toBeTruthy();
    // Composite holds one flagged indicator (VCG warn), Positioning one (GEX fault).
    expect(within(container).getAllByText("1 FLAGGED")).toHaveLength(2);
    // Footer counts every indicator and every warn/fault status.
    expect(within(container).getByText(/20 INDICATORS · 2 ELEVATED/i)).toBeTruthy();
  });

  it("filter narrows items and hides empty groups", () => {
    const { container } = renderRail();
    const input = container.querySelector("input")!;
    fireEvent.change(input, { target: { value: "skew" } });
    expect(container.querySelector('[data-tab="skew"]')).toBeTruthy();
    expect(container.querySelector('[data-tab="skew2d"]')).toBeTruthy();
    expect(container.querySelector('[data-tab="gex"]')).toBeNull();
    expect(within(container).queryByText("Models")).toBeNull();
  });

  it("items carry a group-scoped aria-label so strip buttons keep unique names", () => {
    const { container } = renderRail();
    expect(container.querySelector('[data-tab="vcg"]')!.getAttribute("aria-label")).toBe("VCG — Composite");
  });
});

/* ─── 4. RegimePanel wiring pin ────────────────────────── */

describe("RegimePanel — renders the grouped rail from the shared registry", () => {
  const src = read("components/RegimePanel.tsx");

  it("imports RegimeRail and the shared registry", () => {
    expect(src).toMatch(/from\s+["']\.\/RegimeRail["']/);
    expect(src).toMatch(/from\s+["']@\/lib\/regimeRail["']/);
  });

  it("desktop layout uses the rail modifier class", () => {
    expect(src).toMatch(/regime-panel--rail/);
  });

  it("no longer hand-writes one desktop button per tab", () => {
    expect(src).not.toMatch(/goToTab\("backtest"\)}>BACKTEST/);
  });
});
