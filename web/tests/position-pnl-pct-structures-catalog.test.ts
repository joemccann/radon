/**
 * Exhaustive Return % capital-denominator coverage for every structure in
 * docs/options-structures.json (marketing / options structures catalog).
 *
 * For each structure we assert:
 *  1. Synthetic portfolio position builds with correct risk_profile
 *  2. Exact max risk wins for defined-risk positions
 *  3. Undefined risk without verified opening margin returns unavailable
 *  4. Bare projected margin is ignored
 *  5. Isolated observed opening margin is accepted only with v2 provenance
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  getPnlCapital,
  getPnlDollars,
  getPnlPct,
  resolveEntryCost,
  resolveMarketValue,
} from "../lib/positionUtils";
import type {
  PortfolioLeg,
  PortfolioPosition,
  PositionReturnCapitalPayload,
} from "../lib/types";

type CatalogLeg = {
  type: string;
  action: string;
  strike?: string | number | null;
  expiry?: string | null;
  ratio?: number;
};

type CatalogStructure = {
  name: string;
  risk_profile: string;
  legs: CatalogLeg[];
  category?: string;
};

const catalogPath = resolve(__dirname, "../../docs/options-structures.json");
const CATALOG = JSON.parse(readFileSync(catalogPath, "utf8")) as CatalogStructure[];

const UNDERLYING = 100;

/** Map symbolic catalog strikes → numeric for synthetic books. */
function resolveStrike(raw: string | number | null | undefined, legType: string): number | null {
  if (raw == null || raw === "") return null;
  if (typeof raw === "number") return raw;
  const s = String(raw).trim();
  if (/^\d+(\.\d+)?$/.test(s)) return Number(s);
  const t = legType.toLowerCase();
  switch (s) {
    case "ATM":
    case "same":
    case "middle":
      return UNDERLYING;
    case "middle-lower":
      return UNDERLYING - 5;
    case "middle-upper":
    case "middle+5":
      return UNDERLYING + 5;
    case "lower":
      return UNDERLYING - 10;
    case "higher":
      return UNDERLYING + 10;
    case "lowest":
    case "very_low":
    case "OTM_low":
      return UNDERLYING - 20;
    case "highest":
    case "very_high":
    case "OTM_high":
      return UNDERLYING + 20;
    case "OTM":
      return t === "put" ? UNDERLYING - 10 : UNDERLYING + 10;
    default:
      // unknown token — still produce a distinct strike
      return UNDERLYING + (s.length % 7);
  }
}

type MergedLeg = {
  direction: "LONG" | "SHORT";
  type: "Call" | "Put" | "Stock";
  strike: number | null;
  contracts: number;
};

function catalogToMergedLegs(struct: CatalogStructure, unitQty = 1): MergedLeg[] {
  const map = new Map<string, MergedLeg>();
  for (const leg of struct.legs ?? []) {
    const t = String(leg.type || "").toLowerCase();
    let type: MergedLeg["type"];
    if (t === "stock") type = "Stock";
    else if (t === "put") type = "Put";
    else type = "Call";
    const direction: "LONG" | "SHORT" =
      String(leg.action || "BUY").toUpperCase() === "SELL" ? "SHORT" : "LONG";
    const strike = type === "Stock" ? null : resolveStrike(leg.strike, t);
    const contracts = Math.max(1, Number(leg.ratio) || 1) * unitQty;
    const key = `${direction}|${type}|${strike ?? "STK"}`;
    const prev = map.get(key);
    if (prev) prev.contracts += contracts;
    else map.set(key, { direction, type, strike, contracts });
  }
  return [...map.values()];
}

function buildPosition(
  struct: CatalogStructure,
  opts: {
    unitQty?: number;
    init_margin_at_entry?: number | null;
    return_capital?: PositionReturnCapitalPayload | null;
    max_risk?: number | null;
    /** Scale mark vs entry to create nonzero P&L */
    markFactor?: number;
  } = {},
): PortfolioPosition {
  const unitQty = opts.unitQty ?? 1;
  const markFactor = opts.markFactor ?? 0.7;
  const merged = catalogToMergedLegs(struct, unitQty);
  const risk =
    struct.risk_profile === "defined"
      ? "defined"
      : struct.risk_profile.includes("undefined")
        ? "undefined"
        : struct.risk_profile;

  const legs: PortfolioLeg[] = merged.map((m) => {
    const mult = m.type === "Stock" ? 1 : 100;
    // Synthetic premiums: shorts "richer" entry so net credits are common for undefined.
    const perUnit = m.type === "Stock" ? UNDERLYING : m.direction === "SHORT" ? 8 : 5;
    const entry_cost = perUnit * m.contracts * mult;
    const mark = perUnit * markFactor;
    const market_value = mark * m.contracts * mult;
    return {
      direction: m.direction,
      contracts: m.contracts,
      type: m.type,
      strike: m.strike,
      entry_cost,
      avg_cost: perUnit * (m.type === "Stock" ? 1 : mult),
      market_price: mark,
      market_value,
    };
  });

  const entry_cost = legs.reduce((s, l) => {
    const sign = l.direction === "LONG" ? 1 : -1;
    return s + sign * Math.abs(l.entry_cost);
  }, 0);

  const baseContracts = Math.max(...legs.map((l) => l.contracts), 1);

  // Defined-risk: set max_risk when catalog says defined (debit = |entry|, credit = |entry| + stub width)
  const max_risk: number | null = opts.max_risk !== undefined
    ? opts.max_risk
    : risk === "defined"
      ? Math.max(Math.abs(entry_cost), 100)
      : null;

  return {
    id: 1,
    ticker: "TEST",
    structure: struct.name,
    structure_type: struct.name,
    risk_profile: risk,
    expiry: merged.some((m) => m.type === "Stock") && merged.length === 1 ? "N/A" : "2026-09-18",
    contracts: baseContracts,
    direction: legs.length > 1 ? "COMBO" : legs[0]?.direction ?? "LONG",
    entry_cost,
    max_risk,
    init_margin_at_entry: opts.init_margin_at_entry,
    return_capital: opts.return_capital,
    market_value: null, // force multi-leg recompute from legs
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: "2026-08-01",
    legs,
  };
}

function withObservedMargin(pos: PortfolioPosition, amount: number): PortfolioPosition {
  const position_instance_id = `PI-${pos.structure}`;
  const legs = pos.legs.map((leg, index) => ({ ...leg, con_id: 10_000 + index }));
  const conIds = legs.map((leg) => leg.con_id!);
  const return_capital: PositionReturnCapitalPayload = {
    version: 2,
    amount,
    currency: "USD",
    measurement: {
      quality: "observed",
      method: "isolated-account-margin-delta",
      measured_at: "2026-08-01T15:31:00Z",
      observation_id: `OBS-${pos.structure}`,
      isolation: "isolated",
      before_sample_id: "S1",
      after_sample_id: "S2",
      window_seconds: 60,
      concurrent_exec_ids: [],
    },
    linkage: {
      state: "linked",
      account_id: "U1",
      position_instance_id,
      con_ids: conIds,
      order_refs: [`radon-${pos.structure}`],
      perm_ids: [],
      exec_ids: conIds.map((id) => `E-${id}`),
      legs: conIds.map((con_id, index) => ({
        con_id,
        currency: "USD",
        multiplier: legs[index]?.type === "Stock" ? 1 : 100,
      })),
    },
  };
  return { ...pos, account_id: "U1", position_instance_id, legs, return_capital };
}

const UNDEFINED = CATALOG.filter((s) => s.risk_profile === "undefined");
const DEFINED = CATALOG.filter((s) => s.risk_profile === "defined");
const HYBRID = CATALOG.filter((s) => s.risk_profile === "defined or undefined");

describe("options-structures catalog load", () => {
  it("loads 58 structures with risk profiles", () => {
    expect(CATALOG.length).toBe(58);
    expect(UNDEFINED.length).toBe(19);
    expect(DEFINED.length).toBe(37);
    expect(HYBRID.length).toBe(2);
    expect(UNDEFINED.length + DEFINED.length + HYBRID.length).toBe(58);
  });

  it("every structure has at least one leg", () => {
    for (const s of CATALOG) {
      expect(s.legs?.length, s.name).toBeGreaterThan(0);
    }
  });
});

describe.each(UNDEFINED.map((s) => [s.name, s] as const))(
  "undefined risk: %s",
  (_name, struct) => {
    it("builds a position and resolves entry + MV", () => {
      const pos = buildPosition(struct);
      expect(pos.risk_profile).toBe("undefined");
      expect(pos.legs.length).toBeGreaterThan(0);
      expect(Number.isFinite(resolveEntryCost(pos))).toBe(true);
      expect(resolveMarketValue(pos)).not.toBeNull();
      expect(getPnlDollars(pos)).not.toBeNull();
    });

    it("without verified margin, undefined risk never uses opening credit", () => {
      const pos = buildPosition(struct, { max_risk: null, init_margin_at_entry: null });
      expect(pos.max_risk).toBeNull();
      const capital = getPnlCapital(pos);
      const ec = resolveEntryCost(pos);
      const isLongStock = pos.legs.length === 1
        && pos.legs[0]?.type === "Stock"
        && pos.legs[0].direction === "LONG";
      const isFullLossDebit = ec > 0 && (isLongStock
        || pos.legs.every((leg) => leg.type !== "Stock" && leg.direction === "LONG"));
      expect(capital).toBe(isFullLossDebit ? ec : null);
      if (ec < 0) expect(getPnlPct(pos)).toBeNull();
    });

    it("ignores a bare init_margin_at_entry projection", () => {
      const pos = buildPosition(struct, {
        max_risk: null,
        init_margin_at_entry: 50_000,
      });
      const withoutBareField = buildPosition(struct, { max_risk: null });
      expect(getPnlCapital(pos)).toBe(getPnlCapital(withoutBareField));
    });

    it("uses isolated observed opening margin", () => {
      const pos = withObservedMargin(buildPosition(struct, {
        max_risk: null,
      }), 50_000);
      expect(getPnlCapital(pos)).toBe(50_000);
      const pnl = getPnlDollars(pos)!;
      const pct = getPnlPct(pos)!;
      expect(pct).toBeCloseTo((pnl / 50_000) * 100, 6);
      // Must not equal inflated premium-only % when |entry| is small vs margin
      const premiumCapital = Math.abs(resolveEntryCost(pos));
      if (premiumCapital > 0 && premiumCapital < 50_000 * 0.5) {
        const premiumPct = (pnl / premiumCapital) * 100;
        expect(Math.abs(pct)).toBeLessThan(Math.abs(premiumPct) + 1e-9);
      }
    });

    it("ignores max_risk for undefined risk but accepts observed margin", () => {
      const pos = withObservedMargin(buildPosition(struct, {
        max_risk: 5_000,
      }), 40_000);
      expect(getPnlCapital(pos)).toBe(40_000);
    });
  },
);

describe.each(DEFINED.map((s) => [s.name, s] as const))(
  "defined risk: %s",
  (_name, struct) => {
    it("builds a defined-risk position", () => {
      const pos = buildPosition(struct);
      expect(pos.risk_profile).toBe("defined");
      expect(pos.max_risk).not.toBeNull();
      expect(pos.max_risk!).toBeGreaterThan(0);
    });

    it("without init_margin, capital prefers max_risk over |entry|", () => {
      const pos = buildPosition(struct, {
        init_margin_at_entry: null,
        max_risk: 12_345,
      });
      expect(getPnlCapital(pos)).toBe(12_345);
      const pnl = getPnlDollars(pos)!;
      expect(getPnlPct(pos)!).toBeCloseTo((pnl / 12_345) * 100, 6);
    });

    it("exact max risk wins over opening margin", () => {
      const pos = withObservedMargin(buildPosition(struct, {
        max_risk: 3_000,
      }), 8_000);
      expect(getPnlCapital(pos)).toBe(3_000);
    });

    it("uses debit paid only when it is the full loss", () => {
      const pos = buildPosition(struct, {
        init_margin_at_entry: null,
        max_risk: null,
      });
      const ec = resolveEntryCost(pos);
      expect(getPnlCapital(pos)).toBe(ec > 0 ? ec : null);
    });
  },
);

describe.each(HYBRID.map((s) => [s.name, s] as const))(
  "hybrid defined-or-undefined: %s",
  (_name, struct) => {
    it("uses isolated observed margin when classified undefined", () => {
      const pos = withObservedMargin(buildPosition(struct, {
        max_risk: null,
      }), 15_000);
      // catalog hybrid → treated as undefined path in builder when includes "undefined"
      expect(getPnlCapital(pos)).toBe(15_000);
      expect(getPnlPct(pos)).not.toBeNull();
    });
  },
);

describe("credit vs debit extremes (catalog-derived)", () => {
  it("Long Risk Reversal credit: bare return is unavailable, verified margin is finite", () => {
    const struct = CATALOG.find((s) => s.name === "Long Risk Reversal")!;
    const pos = buildPosition(struct, { unitQty: 10, markFactor: 0.4 });
    const ec = resolveEntryCost(pos);
    // short put premium 8 > long call 5 → net credit
    expect(ec).toBeLessThan(0);
    expect(getPnlPct(pos)).toBeNull();
    const withMargin = getPnlPct(withObservedMargin(pos, 100_000));
    expect(withMargin).toBeCloseTo((getPnlDollars(pos)! / 100_000) * 100, 6);
  });

  it("Bull Call Spread: max_risk is capital when no margin", () => {
    const struct = CATALOG.find((s) => s.name === "Bull Call Spread")!;
    const pos = buildPosition(struct, {
      unitQty: 5,
      init_margin_at_entry: null,
      max_risk: 2_500,
    });
    // Synthetic unit prices may net credit or debit; capital still max_risk.
    expect(getPnlCapital(pos)).toBe(2_500);
    expect(getPnlPct(pos)!).toBeCloseTo((getPnlDollars(pos)! / 2_500) * 100, 6);
  });

  it("Short Straddle undefined: no verified margin means unavailable", () => {
    const struct = CATALOG.find((s) => s.name === "Short Straddle")!;
    const pos = buildPosition(struct, { max_risk: null });
    expect(pos.max_risk).toBeNull();
    expect(getPnlCapital(pos)).toBeNull();
  });

  it("Iron Butterfly defined credit: max_risk preferred", () => {
    const struct = CATALOG.find((s) => s.name === "Iron Butterfly")!;
    const pos = buildPosition(struct, { max_risk: 4_000, init_margin_at_entry: null });
    expect(getPnlCapital(pos)).toBe(4_000);
  });

  it("Seagull / Jade Lizard / ratio spreads accept isolated observed margin", () => {
    for (const name of [
      "Seagull Spread",
      "Jade Lizard",
      "Long Call Ratio Spread",
      "Ratio Call Spread (1x2 Short focused)",
      "Naked Strangle (Short 1xN Strangle)",
    ]) {
      const struct = CATALOG.find((s) => s.name === name)!;
      const pos = withObservedMargin(buildPosition(struct), 33_000);
      expect(getPnlCapital(pos), name).toBe(33_000);
    }
  });
});
