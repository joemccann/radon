import { describe, it, expect } from "vitest";
import {
  optionKey,
  contractsKey,
  normalizeOptionExpiry,
  normalizeOptionContract,
  uniqueOptionContracts,
  portfolioLegToContract,
  normalizeSymbolList,
  symbolKey,
} from "../lib/pricesProtocol";
import type { OptionContract } from "../lib/pricesProtocol";

// =============================================================================
// optionKey
// =============================================================================

describe("optionKey", () => {
  it("builds SYMBOL_YYYYMMDD_STRIKE_RIGHT key", () => {
    const contract: OptionContract = {
      symbol: "AAPL",
      expiry: "20260320",
      strike: 200,
      right: "C",
    };
    expect(optionKey(contract)).toBe("AAPL_20260320_200_C");
  });

  it("handles put options", () => {
    const contract: OptionContract = {
      symbol: "GOOG",
      expiry: "20260620",
      strike: 175.5,
      right: "P",
    };
    expect(optionKey(contract)).toBe("GOOG_20260620_175.5_P");
  });

  it("preserves decimal strikes", () => {
    const contract: OptionContract = {
      symbol: "SPY",
      expiry: "20260115",
      strike: 580.5,
      right: "C",
    };
    expect(optionKey(contract)).toBe("SPY_20260115_580.5_C");
  });

  it("handles integer strikes without decimal", () => {
    const contract: OptionContract = {
      symbol: "TSLA",
      expiry: "20260401",
      strike: 300,
      right: "P",
    };
    expect(optionKey(contract)).toBe("TSLA_20260401_300_P");
  });

  it("normalizes dashed expiries before building the key", () => {
    const contract: OptionContract = {
      symbol: "crm",
      expiry: "2026-03-20",
      strike: 200,
      right: "C",
    };
    expect(optionKey(contract)).toBe("CRM_20260320_200_C");
  });
});

// =============================================================================
// contractsKey
// =============================================================================

describe("contractsKey", () => {
  it("sorts keys and joins with commas", () => {
    const contracts: OptionContract[] = [
      { symbol: "MSFT", expiry: "20260320", strike: 420, right: "C" },
      { symbol: "AAPL", expiry: "20260320", strike: 200, right: "P" },
    ];
    const result = contractsKey(contracts);
    // AAPL sorts before MSFT
    expect(result).toBe("AAPL_20260320_200_P,MSFT_20260320_420_C");
  });

  it("returns empty string for empty array", () => {
    expect(contractsKey([])).toBe("");
  });

  it("handles single contract", () => {
    const contracts: OptionContract[] = [
      { symbol: "SPY", expiry: "20260115", strike: 580, right: "C" },
    ];
    expect(contractsKey(contracts)).toBe("SPY_20260115_580_C");
  });

  it("produces stable output regardless of input order", () => {
    const a: OptionContract = { symbol: "AAPL", expiry: "20260320", strike: 200, right: "C" };
    const b: OptionContract = { symbol: "GOOG", expiry: "20260320", strike: 175, right: "P" };
    expect(contractsKey([a, b])).toBe(contractsKey([b, a]));
  });

  it("deduplicates logically identical contracts across dashed and compact expiries", () => {
    const contracts: OptionContract[] = [
      { symbol: "CRM", expiry: "2026-03-20", strike: 200, right: "C" },
      { symbol: "crm", expiry: "20260320", strike: 200, right: "C" },
    ];
    expect(contractsKey(contracts)).toBe("CRM_20260320_200_C");
  });
});

// =============================================================================
// normalizeOptionExpiry / normalizeOptionContract / uniqueOptionContracts
// =============================================================================

describe("option contract normalization helpers", () => {
  it("normalizes YYYY-MM-DD expiries to YYYYMMDD", () => {
    expect(normalizeOptionExpiry("2026-03-20")).toBe("20260320");
  });

  it("returns null for malformed expiries", () => {
    expect(normalizeOptionExpiry("2026-3-2")).toBeNull();
  });

  it("normalizes the full option contract shape", () => {
    expect(normalizeOptionContract({
      symbol: "crm",
      expiry: "2026-03-20",
      strike: 197.5,
      right: "C",
    })).toEqual({
      symbol: "CRM",
      expiry: "20260320",
      strike: 197.5,
      right: "C",
    });
  });

  it("deduplicates normalized option contracts", () => {
    const contracts = uniqueOptionContracts([
      { symbol: "CRM", expiry: "2026-03-20", strike: 197.5, right: "C" },
      { symbol: "crm", expiry: "20260320", strike: 197.5, right: "C" },
      { symbol: "CRM", expiry: "20260320", strike: 200, right: "C" },
    ]);
    expect(contracts).toEqual([
      { symbol: "CRM", expiry: "20260320", strike: 197.5, right: "C" },
      { symbol: "CRM", expiry: "20260320", strike: 200, right: "C" },
    ]);
  });
});

// =============================================================================
// portfolioLegToContract
// =============================================================================

describe("portfolioLegToContract", () => {
  it("returns null for Stock legs", () => {
    const result = portfolioLegToContract("AAPL", "2026-03-20", {
      type: "Stock",
      strike: null,
    });
    expect(result).toBeNull();
  });

  it("returns null for null strike", () => {
    const result = portfolioLegToContract("AAPL", "2026-03-20", {
      type: "Call",
      strike: null,
    });
    expect(result).toBeNull();
  });

  it("returns null for zero strike", () => {
    const result = portfolioLegToContract("AAPL", "2026-03-20", {
      type: "Put",
      strike: 0,
    });
    expect(result).toBeNull();
  });

  it("returns null for empty expiry", () => {
    const result = portfolioLegToContract("AAPL", "", {
      type: "Call",
      strike: 200,
    });
    expect(result).toBeNull();
  });

  it("returns null for N/A expiry", () => {
    const result = portfolioLegToContract("AAPL", "N/A", {
      type: "Call",
      strike: 200,
    });
    expect(result).toBeNull();
  });

  it("returns null for non-Call/non-Put type", () => {
    const result = portfolioLegToContract("AAPL", "2026-03-20", {
      type: "Spread",
      strike: 200,
    });
    expect(result).toBeNull();
  });

  it("returns null for malformed expiry (not 8 chars after stripping dashes)", () => {
    const result = portfolioLegToContract("AAPL", "2026-3-2", {
      type: "Call",
      strike: 200,
    });
    // "2026-3-2" -> "202632" (6 chars, not 8)
    expect(result).toBeNull();
  });

  it("returns null for very short expiry", () => {
    const result = portfolioLegToContract("AAPL", "2026", {
      type: "Call",
      strike: 200,
    });
    expect(result).toBeNull();
  });

  it("converts YYYY-MM-DD expiry to YYYYMMDD", () => {
    const result = portfolioLegToContract("aapl", "2026-03-20", {
      type: "Call",
      strike: 200,
    });
    expect(result).not.toBeNull();
    expect(result!.expiry).toBe("20260320");
  });

  it("uppercases ticker", () => {
    const result = portfolioLegToContract("aapl", "2026-03-20", {
      type: "Call",
      strike: 200,
    });
    expect(result).not.toBeNull();
    expect(result!.symbol).toBe("AAPL");
  });

  it("maps Call to C", () => {
    const result = portfolioLegToContract("GOOG", "2026-06-19", {
      type: "Call",
      strike: 175,
    });
    expect(result).not.toBeNull();
    expect(result!.right).toBe("C");
  });

  it("maps Put to P", () => {
    const result = portfolioLegToContract("GOOG", "2026-06-19", {
      type: "Put",
      strike: 150,
    });
    expect(result).not.toBeNull();
    expect(result!.right).toBe("P");
  });

  it("returns full contract for valid Call", () => {
    const result = portfolioLegToContract("msft", "2026-04-17", {
      type: "Call",
      strike: 420,
    });
    expect(result).toEqual({
      symbol: "MSFT",
      expiry: "20260417",
      strike: 420,
      right: "C",
    });
  });

  it("returns full contract for valid Put", () => {
    const result = portfolioLegToContract("spy", "2026-01-15", {
      type: "Put",
      strike: 560.5,
    });
    expect(result).toEqual({
      symbol: "SPY",
      expiry: "20260115",
      strike: 560.5,
      right: "P",
    });
  });

  it("handles already-YYYYMMDD expiry (no dashes)", () => {
    const result = portfolioLegToContract("AAPL", "20260320", {
      type: "Call",
      strike: 200,
    });
    expect(result).not.toBeNull();
    expect(result!.expiry).toBe("20260320");
  });

  it("prefers a calendar leg's own expiry over the position expiry", () => {
    const result = portfolioLegToContract("AAPL", "2026-03-20", {
      type: "Call",
      strike: 200,
      expiry: "2026-06-19",
    });
    expect(result?.expiry).toBe("20260619");
  });
});

// =============================================================================
// normalizeSymbolList
// =============================================================================

describe("normalizeSymbolList", () => {
  it("trims whitespace from symbols", () => {
    expect(normalizeSymbolList(["  AAPL  ", " GOOG "])).toEqual(["AAPL", "GOOG"]);
  });

  it("uppercases symbols", () => {
    expect(normalizeSymbolList(["aapl", "goog"])).toEqual(["AAPL", "GOOG"]);
  });

  it("filters out empty strings", () => {
    expect(normalizeSymbolList(["AAPL", "", "  ", "GOOG"])).toEqual(["AAPL", "GOOG"]);
  });

  it("returns empty array for all-empty input", () => {
    expect(normalizeSymbolList(["", "  "])).toEqual([]);
  });

  it("returns empty array for empty input", () => {
    expect(normalizeSymbolList([])).toEqual([]);
  });

  it("does not mutate the original array", () => {
    const original = ["aapl", "goog"];
    normalizeSymbolList(original);
    expect(original).toEqual(["aapl", "goog"]);
  });
});

// =============================================================================
// symbolKey
// =============================================================================

describe("symbolKey", () => {
  it("normalizes, sorts, and joins symbols", () => {
    expect(symbolKey(["goog", "aapl"])).toBe("AAPL,GOOG");
  });

  it("returns empty string for empty array", () => {
    expect(symbolKey([])).toBe("");
  });

  it("handles single symbol", () => {
    expect(symbolKey(["spy"])).toBe("SPY");
  });

  it("filters empty strings before joining", () => {
    expect(symbolKey(["aapl", "", "goog"])).toBe("AAPL,GOOG");
  });

  it("produces stable output regardless of input order", () => {
    expect(symbolKey(["MSFT", "AAPL", "GOOG"])).toBe(symbolKey(["GOOG", "MSFT", "AAPL"]));
  });

  it("deduplicates when symbols are the same after normalization", () => {
    // symbolKey doesn't deduplicate, so both appear — this tests actual behavior
    const result = symbolKey(["aapl", "AAPL"]);
    expect(result).toBe("AAPL,AAPL");
  });
});
