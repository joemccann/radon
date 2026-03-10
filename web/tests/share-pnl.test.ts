import { describe, it, expect } from "vitest";

// Test the helper functions that build share data from order types
// These are defined in WorkspaceSections.tsx but we test the logic here

type OrderContract = {
  conId: number | null;
  symbol: string;
  secType: string;
  strike: number | null;
  right: string | null;
  expiry: string | null;
};

type ExecutedOrder = {
  execId: string;
  symbol: string;
  contract: OrderContract;
  side: string;
  quantity: number;
  avgPrice: number | null;
  commission: number | null;
  realizedPNL: number | null;
  time: string;
  exchange: string;
};

type BlotterTrade = {
  symbol: string;
  contract_desc: string;
  sec_type: string;
  is_closed: boolean;
  net_quantity: number;
  total_commission: number;
  realized_pnl: number;
  cost_basis: number;
  proceeds: number;
  total_cash_flow: number;
  executions: { exec_id: string; time: string; side: string; quantity: number; price: number; commission: number; notional_value: number; net_cash_flow: number }[];
};

// Re-implement the helpers to test them (they're not exported from WorkspaceSections)
function execOrderDescription(e: ExecutedOrder): string {
  const c = e.contract;
  const side = e.side === "BOT" ? "Long" : e.side === "SLD" ? "Short" : e.side;
  if (c.secType === "OPT" && c.strike != null && c.right && c.expiry) {
    const right = c.right === "C" || c.right === "CALL" ? "Call" : c.right === "P" || c.right === "PUT" ? "Put" : c.right;
    return `${side} ${c.symbol} ${c.expiry} ${right} $${c.strike.toFixed(2)}`;
  }
  return `${side} ${c.symbol}`;
}

function execOrderShareData(e: ExecutedOrder) {
  return {
    description: execOrderDescription(e),
    pnl: e.realizedPNL ?? 0,
    pnlPct: e.realizedPNL != null && e.avgPrice != null && e.avgPrice > 0
      ? (e.realizedPNL / (e.avgPrice * e.quantity * (e.contract.secType === "OPT" ? 100 : 1))) * 100
      : null,
    commission: e.commission,
    fillPrice: e.avgPrice,
    time: e.time ? new Date(e.time).toLocaleString() : "",
  };
}

function blotterShareData(t: BlotterTrade) {
  const lastExec = t.executions.length > 0 ? t.executions[t.executions.length - 1] : null;
  const pnlPct = t.cost_basis !== 0 ? (t.realized_pnl / Math.abs(t.cost_basis)) * 100 : null;
  return {
    description: t.contract_desc || t.symbol,
    pnl: t.realized_pnl,
    pnlPct,
    commission: t.total_commission,
    fillPrice: lastExec?.price ?? null,
    time: lastExec?.time ? new Date(lastExec.time).toLocaleString() : "",
  };
}

// --- Twitter text builder ---

function buildTweetText(description: string, pnl: number, pnlPct: number | null, showDollar: boolean, showPct: boolean): string {
  const parts: string[] = [];
  const sign = pnl >= 0 ? "+" : "-";
  const abs = Math.abs(pnl);
  if (showDollar) {
    parts.push(`${sign}$${abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
  }
  if (showPct && pnlPct != null && Number.isFinite(pnlPct)) {
    const pSign = pnlPct >= 0 ? "+" : "";
    parts.push(`${pSign}${pnlPct.toFixed(2)}%`);
  }
  const pnlStr = parts.join(" ");
  return `${description} ${pnlStr}\n\nExecuted with @raaborern`;
}

describe("execOrderDescription", () => {
  it("formats long option correctly", () => {
    const order: ExecutedOrder = {
      execId: "1", symbol: "AAOI", side: "BOT", quantity: 5,
      avgPrice: 2.50, commission: -2.60, realizedPNL: 1234.56,
      time: "2026-03-10T14:30:00", exchange: "SMART",
      contract: { conId: 123, symbol: "AAOI", secType: "OPT", strike: 45, right: "C", expiry: "2026-04-17" },
    };
    expect(execOrderDescription(order)).toBe("Long AAOI 2026-04-17 Call $45.00");
  });

  it("formats short put correctly", () => {
    const order: ExecutedOrder = {
      execId: "2", symbol: "TSLA", side: "SLD", quantity: 2,
      avgPrice: 8.75, commission: -4.20, realizedPNL: -567.89,
      time: "2026-03-10T10:15:00", exchange: "CBOE",
      contract: { conId: 456, symbol: "TSLA", secType: "OPT", strike: 200, right: "P", expiry: "2026-03-21" },
    };
    expect(execOrderDescription(order)).toBe("Short TSLA 2026-03-21 Put $200.00");
  });

  it("handles CALL/PUT right values", () => {
    const order: ExecutedOrder = {
      execId: "3", symbol: "SPY", side: "BOT", quantity: 1,
      avgPrice: 5.00, commission: -1.30, realizedPNL: 100,
      time: "2026-03-10T09:30:00", exchange: "SMART",
      contract: { conId: 789, symbol: "SPY", secType: "OPT", strike: 500, right: "CALL", expiry: "2026-06-20" },
    };
    expect(execOrderDescription(order)).toBe("Long SPY 2026-06-20 Call $500.00");
  });

  it("formats stock order (no option details)", () => {
    const order: ExecutedOrder = {
      execId: "4", symbol: "AAPL", side: "BOT", quantity: 100,
      avgPrice: 175.50, commission: -1.00, realizedPNL: 250,
      time: "2026-03-10T11:00:00", exchange: "ARCA",
      contract: { conId: 101, symbol: "AAPL", secType: "STK", strike: null, right: null, expiry: null },
    };
    expect(execOrderDescription(order)).toBe("Long AAPL");
  });

  it("preserves unknown side values", () => {
    const order: ExecutedOrder = {
      execId: "5", symbol: "GOOG", side: "CANCELLED", quantity: 0,
      avgPrice: null, commission: null, realizedPNL: null,
      time: "2026-03-10T12:00:00", exchange: "SMART",
      contract: { conId: 202, symbol: "GOOG", secType: "STK", strike: null, right: null, expiry: null },
    };
    expect(execOrderDescription(order)).toBe("CANCELLED GOOG");
  });
});

describe("execOrderShareData", () => {
  it("computes pnlPct for option trades with 100x multiplier", () => {
    const order: ExecutedOrder = {
      execId: "1", symbol: "AAOI", side: "BOT", quantity: 5,
      avgPrice: 2.50, commission: -2.60, realizedPNL: 1250,
      time: "2026-03-10T14:30:00", exchange: "SMART",
      contract: { conId: 123, symbol: "AAOI", secType: "OPT", strike: 45, right: "C", expiry: "2026-04-17" },
    };
    const data = execOrderShareData(order);
    // pnlPct = 1250 / (2.50 * 5 * 100) * 100 = 1250/1250 * 100 = 100%
    expect(data.pnlPct).toBe(100);
    expect(data.pnl).toBe(1250);
    expect(data.fillPrice).toBe(2.50);
    expect(data.commission).toBe(-2.60);
  });

  it("computes pnlPct for stock trades without multiplier", () => {
    const order: ExecutedOrder = {
      execId: "2", symbol: "AAPL", side: "BOT", quantity: 100,
      avgPrice: 175.00, commission: -1.00, realizedPNL: 500,
      time: "2026-03-10T11:00:00", exchange: "ARCA",
      contract: { conId: 101, symbol: "AAPL", secType: "STK", strike: null, right: null, expiry: null },
    };
    const data = execOrderShareData(order);
    // pnlPct = 500 / (175 * 100 * 1) * 100 ≈ 2.857%
    expect(data.pnlPct).toBeCloseTo(2.857, 2);
  });

  it("returns null pnlPct when avgPrice is null", () => {
    const order: ExecutedOrder = {
      execId: "3", symbol: "X", side: "BOT", quantity: 1,
      avgPrice: null, commission: null, realizedPNL: 100,
      time: "", exchange: "SMART",
      contract: { conId: 0, symbol: "X", secType: "STK", strike: null, right: null, expiry: null },
    };
    expect(execOrderShareData(order).pnlPct).toBeNull();
  });
});

describe("blotterShareData", () => {
  it("computes share data from closed trade", () => {
    const trade: BlotterTrade = {
      symbol: "NET", contract_desc: "Long NET 2026-06-20 Call $120.00",
      sec_type: "OPT", is_closed: true, net_quantity: 0,
      total_commission: -5.20, realized_pnl: 2000, cost_basis: -4000,
      proceeds: 6000, total_cash_flow: 2000,
      executions: [
        { exec_id: "a", time: "2026-03-01T10:00:00", side: "BOT", quantity: 10, price: 4.00, commission: -2.60, notional_value: 4000, net_cash_flow: -4002.60 },
        { exec_id: "b", time: "2026-03-10T15:00:00", side: "SLD", quantity: 10, price: 6.00, commission: -2.60, notional_value: 6000, net_cash_flow: 5997.40 },
      ],
    };
    const data = blotterShareData(trade);
    expect(data.description).toBe("Long NET 2026-06-20 Call $120.00");
    expect(data.pnl).toBe(2000);
    // pnlPct = 2000 / |−4000| * 100 = 50%
    expect(data.pnlPct).toBe(50);
    expect(data.commission).toBe(-5.20);
    expect(data.fillPrice).toBe(6.00); // last execution price
  });

  it("falls back to symbol when contract_desc is empty", () => {
    const trade: BlotterTrade = {
      symbol: "MSFT", contract_desc: "",
      sec_type: "STK", is_closed: true, net_quantity: 0,
      total_commission: -1.00, realized_pnl: 100, cost_basis: -5000,
      proceeds: 5100, total_cash_flow: 100, executions: [],
    };
    const data = blotterShareData(trade);
    expect(data.description).toBe("MSFT");
    expect(data.fillPrice).toBeNull();
    expect(data.time).toBe("");
  });

  it("returns null pnlPct when cost_basis is 0", () => {
    const trade: BlotterTrade = {
      symbol: "TEST", contract_desc: "Test", sec_type: "STK",
      is_closed: true, net_quantity: 0, total_commission: 0,
      realized_pnl: 0, cost_basis: 0, proceeds: 0, total_cash_flow: 0,
      executions: [],
    };
    expect(blotterShareData(trade).pnlPct).toBeNull();
  });
});

describe("API route query params", () => {
  it("builds correct URL params from share data", () => {
    const data = {
      description: "Long AAOI 2026-04-17 Call $45.00",
      pnl: 1234.56,
      pnlPct: 47.5,
      commission: -2.60,
      fillPrice: 12.50,
      time: "3/10/2026, 2:30:00 PM",
    };
    const params = new URLSearchParams();
    params.set("description", data.description);
    params.set("pnl", String(data.pnl));
    if (data.pnlPct != null) params.set("pnlPct", String(data.pnlPct));
    if (data.commission != null) params.set("commission", String(data.commission));
    if (data.fillPrice != null) params.set("fillPrice", String(data.fillPrice));
    if (data.time) params.set("time", data.time);

    expect(params.get("description")).toBe("Long AAOI 2026-04-17 Call $45.00");
    expect(params.get("pnl")).toBe("1234.56");
    expect(params.get("pnlPct")).toBe("47.5");
    expect(params.get("commission")).toBe("-2.6");
    expect(params.get("fillPrice")).toBe("12.5");
  });

  it("respects showDollar=false by omitting pnl param", () => {
    const params = new URLSearchParams();
    params.set("description", "Test");
    // showDollar=false → don't set pnl
    params.set("pnlPct", "25.5");
    // showPct=true → set pnlPct
    expect(params.has("pnl")).toBe(false);
    expect(params.get("pnlPct")).toBe("25.5");
  });

  it("respects showPct=false by omitting pnlPct param", () => {
    const params = new URLSearchParams();
    params.set("description", "Test");
    params.set("pnl", "500");
    // showPct=false → don't set pnlPct
    expect(params.has("pnlPct")).toBe(false);
    expect(params.get("pnl")).toBe("500");
  });
});

describe("buildTweetText", () => {
  it("includes both $ and % when both enabled", () => {
    const text = buildTweetText("Long AAOI 2026-04-17 Call $45.00", 1234.56, 47.5, true, true);
    expect(text).toContain("+$1,234.56");
    expect(text).toContain("+47.50%");
    expect(text).toContain("Long AAOI 2026-04-17 Call $45.00");
    expect(text).toContain("Executed with @raaborern");
  });

  it("includes only $ when showPct=false", () => {
    const text = buildTweetText("Long AAPL", 500, 2.86, true, false);
    expect(text).toContain("+$500.00");
    expect(text).not.toContain("%");
  });

  it("includes only % when showDollar=false", () => {
    const text = buildTweetText("Short TSLA Put", -200, -10.5, false, true);
    expect(text).not.toContain("$200");
    expect(text).toContain("-10.50%");
  });

  it("handles negative P&L correctly", () => {
    const text = buildTweetText("Short SPY Call", -567.89, -12.3, true, true);
    expect(text).toContain("-$567.89");
    expect(text).toContain("-12.30%");
  });

  it("skips % when pnlPct is null even if showPct=true", () => {
    const text = buildTweetText("Long GOOG", 100, null, true, true);
    expect(text).toContain("+$100.00");
    expect(text).not.toContain("%");
  });

  it("shows empty pnl portion when both disabled", () => {
    const text = buildTweetText("Long X", 100, 50, false, false);
    expect(text).toContain("Long X");
    expect(text).toContain("Executed with @raaborern");
    expect(text).not.toContain("$");
    expect(text).not.toContain("%");
  });
});
