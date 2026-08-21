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
      // Fixture-declared net, not resolveEntryCost(pos). Credits stay
      // unavailable. A positive net debit is capital even on mixed longs/shorts
      // (CBRS debit risk reversal, 2026-08-21).
      const declaredNet = pos.entry_cost;
      expect(getPnlCapital(pos)).toBe(declaredNet > 0 ? declaredNet : null);
      if (declaredNet < 0) expect(getPnlPct(pos)).toBeNull();
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
      // Fixture-declared net, not resolveEntryCost(pos) — the source must agree
      // with the position payload it was handed, not merely with itself.
      const declaredNet = pos.entry_cost;
      expect(getPnlCapital(pos)).toBe(declaredNet > 0 ? declaredNet : null);
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

/**
 * Hand-computed denominators — one per risk_profile bucket.
 *
 * Every expectation below is a numeric literal worked out from the leg premiums
 * written into the fixture, never an expression that calls back into
 * positionUtils. The catalog sweeps above prove all 58 structures BUILD and
 * resolve; these prove the arithmetic is the arithmetic we intend.
 */
function handBuiltPosition(
  fields: {
    structure: string;
    risk_profile: PortfolioPosition["risk_profile"];
    entry_cost: number;
    contracts: number;
    direction: PortfolioPosition["direction"];
    max_risk?: number | null;
    market_value?: number | null;
    expiry?: string;
  },
  legs: PortfolioLeg[],
): PortfolioPosition {
  return {
    id: 1,
    ticker: "TEST",
    structure: fields.structure,
    structure_type: fields.structure,
    risk_profile: fields.risk_profile,
    expiry: fields.expiry ?? "2026-09-18",
    contracts: fields.contracts,
    direction: fields.direction,
    entry_cost: fields.entry_cost,
    max_risk: fields.max_risk ?? null,
    init_margin_at_entry: null,
    return_capital: null,
    market_value: fields.market_value ?? null,
    kelly_optimal: null,
    target: null,
    stop: null,
    entry_date: "2026-08-01",
    legs,
  };
}

function optionLeg(
  direction: "LONG" | "SHORT",
  strike: number,
  contracts: number,
  entryPerUnit: number,
  markPerUnit: number,
): PortfolioLeg {
  return {
    direction,
    contracts,
    type: "Call",
    strike,
    entry_cost: entryPerUnit * contracts * 100,
    avg_cost: entryPerUnit * 100,
    market_price: markPerUnit,
    market_value: markPerUnit * contracts * 100,
  };
}

describe("hand-computed capital and return (literal expectations)", () => {
  it("defined multi-leg debit spread: capital is the net debit, 5 × ($5.00 − $2.00) × 100", () => {
    // LONG C100 ×5 @ $5.00 = $2,500 debit · SHORT C110 ×5 @ $2.00 = $1,000 credit
    // Marks: $3.50 → $1,750 · $1.20 → $600
    const pos = handBuiltPosition(
      {
        structure: "Bull Call Spread",
        risk_profile: "defined",
        entry_cost: 1_500,
        contracts: 5,
        direction: "COMBO",
      },
      [optionLeg("LONG", 100, 5, 5, 3.5), optionLeg("SHORT", 110, 5, 2, 1.2)],
    );
    expect(resolveEntryCost(pos)).toBe(1_500);
    expect(resolveMarketValue(pos)).toBe(1_150);
    expect(getPnlDollars(pos)).toBe(-350);
    expect(getPnlCapital(pos)).toBe(1_500);
    expect(getPnlPct(pos)).toBeCloseTo(-23.333333, 6);
  });

  it("defined single-leg long call: capital is the $900 debit paid", () => {
    const pos = handBuiltPosition(
      {
        structure: "Long Call",
        risk_profile: "defined",
        entry_cost: 900,
        contracts: 2,
        direction: "LONG",
      },
      [optionLeg("LONG", 100, 2, 4.5, 6.2)],
    );
    expect(resolveMarketValue(pos)).toBe(1_240);
    expect(getPnlDollars(pos)).toBe(340);
    expect(getPnlCapital(pos)).toBe(900);
    expect(getPnlPct(pos)).toBeCloseTo(37.777778, 6);
  });

  it("undefined debit risk reversal: net debit paid is the return denominator", () => {
    // CBRS 2026-08-21: LONG 50x C$205 @ $2.33, SHORT 50x P$200 @ $1.58.
    // Net debit $0.75 × 50 × 100 = $3,750. Marks $2.23 / $1.30.
    const legs: PortfolioLeg[] = [
      optionLeg("LONG", 205, 50, 2.33, 2.23),
      { ...optionLeg("SHORT", 200, 50, 1.58, 1.3), type: "Put" },
    ];
    const pos = handBuiltPosition(
      {
        structure: "Risk Reversal (P$200.0/C$205.0)",
        risk_profile: "undefined",
        entry_cost: 3_750,
        contracts: 50,
        direction: "COMBO",
      },
      legs,
    );
    expect(resolveEntryCost(pos)).toBe(3_750);
    expect(resolveMarketValue(pos)).toBe(4_650);
    expect(getPnlDollars(pos)).toBe(900);
    expect(getPnlCapital(pos)).toBe(3_750);
    expect(getPnlPct(pos)).toBeCloseTo(24, 6);
  });

  it("undefined multi-leg CREDIT combo: −$5,000 net credit is never a denominator", () => {
    // SHORT P95 ×10 @ $8.00 = $8,000 credit · LONG C110 ×10 @ $3.00 = $3,000 debit
    // Marks: $5.00 → $5,000 · $1.80 → $1,800
    const legs: PortfolioLeg[] = [
      { ...optionLeg("SHORT", 95, 10, 8, 5), type: "Put" },
      optionLeg("LONG", 110, 10, 3, 1.8),
    ];
    const pos = handBuiltPosition(
      {
        structure: "Long Risk Reversal",
        risk_profile: "undefined",
        entry_cost: -5_000,
        contracts: 10,
        direction: "COMBO",
      },
      legs,
    );
    expect(resolveEntryCost(pos)).toBe(-5_000);
    expect(resolveMarketValue(pos)).toBe(-3_200);
    expect(getPnlDollars(pos)).toBe(1_800);
    expect(getPnlCapital(pos)).toBeNull();
    expect(getPnlPct(pos)).toBeNull();
    expect(getPnlCapital(withObservedMargin(pos, 40_000))).toBe(40_000);
    expect(getPnlPct(withObservedMargin(pos, 40_000))).toBeCloseTo(4.5, 9);
  });

  it("undefined single-leg short put: no verified capital, no return", () => {
    const pos = handBuiltPosition(
      {
        structure: "Short Put",
        risk_profile: "undefined",
        entry_cost: -1_200,
        contracts: 3,
        direction: "SHORT",
      },
      [{ ...optionLeg("SHORT", 95, 3, 4, 2.5), type: "Put" }],
    );
    expect(getPnlCapital(pos)).toBeNull();
    expect(getPnlPct(pos)).toBeNull();
  });

  it("long stock: the $10,000 paid is the full loss and the denominator", () => {
    const pos = handBuiltPosition(
      {
        structure: "Stock",
        risk_profile: "undefined",
        entry_cost: 10_000,
        contracts: 200,
        direction: "LONG",
        expiry: "N/A",
      },
      [
        {
          direction: "LONG",
          contracts: 200,
          type: "Stock",
          strike: null,
          entry_cost: 10_000,
          avg_cost: 50,
          market_price: 53,
          market_value: 10_600,
        },
      ],
    );
    expect(getPnlDollars(pos)).toBe(600);
    expect(getPnlCapital(pos)).toBe(10_000);
    expect(getPnlPct(pos)).toBeCloseTo(6, 9);
  });

  it("leg entry_cost is a MAGNITUDE: direction alone sets the sign of the fold", () => {
    // Per web/CLAUDE.md the sign comes from leg.direction; entry_cost is stored
    // as a positive magnitude. A payload whose SHORT leg arrives already signed
    // must still fold to the same net — dropping the Math.abs in
    // resolveEntryCost / resolveMarketValue turns this case red.
    // LONG C100 ×4 @ $7.00 = +$2,800 · SHORT C110 ×4 @ $4.00 = −$1,600
    const shortLeg = optionLeg("SHORT", 110, 4, 4, 2.25);
    const pos = handBuiltPosition(
      {
        structure: "Bull Call Spread",
        risk_profile: "defined",
        entry_cost: 1_200,
        contracts: 4,
        direction: "COMBO",
      },
      [
        optionLeg("LONG", 100, 4, 7, 5),
        { ...shortLeg, entry_cost: -1_600, market_value: -900 },
      ],
    );
    expect(resolveEntryCost(pos)).toBe(1_200);
    expect(resolveMarketValue(pos)).toBe(1_100);
    expect(getPnlDollars(pos)).toBe(-100);
    expect(getPnlCapital(pos)).toBe(1_200);
    expect(getPnlPct(pos)).toBeCloseTo(-8.333333, 6);
  });
});
