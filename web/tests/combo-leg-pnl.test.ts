import { afterEach, describe, expect, it, vi } from "vitest";
import { getTodayPnlDollars, getOptionDailyChg, getPnlDollars, resolveLegPnl } from "@/lib/positionUtils";
import type { PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";

function position(): PortfolioPosition {
  return {
    id: 1, ticker: "SNDK", structure: "Combo (3 legs)", structure_type: "Combo",
    risk_profile: "undefined", expiry: "2026-10-23", contracts: 10, direction: "DEBIT",
    entry_cost: 15275, max_risk: null, market_value: 28780,
    kelly_optimal: null, target: null, stop: null, entry_date: "2026-10-01",
    legs: [
      {direction:"LONG",contracts:10,type:"Call",strike:1450,entry_cost:52766,avg_cost:5276.6,market_price:65,market_value:65000},
      {direction:"SHORT",contracts:10,type:"Call",strike:1600,entry_cost:13016,avg_cost:1301.6,market_price:17.72,market_value:-17720},
      {direction:"SHORT",contracts:5,type:"Put",strike:1400,entry_cost:24475,avg_cost:4895,market_price:37,market_value:-18500},
    ],
  };
}
function prices(): Record<string, PriceData> {
  return Object.fromEntries(position().legs.map((leg, i) => {
    const symbol = `SNDK_20261023_${leg.strike}_${leg.type === "Call" ? "C" : "P"}`;
    return [symbol, {symbol,last:leg.market_price,bid:null,ask:null,close:[60,20,40][i]} as PriceData];
  }));
}
afterEach(() => vi.useRealTimers());

describe("complex combo per-leg P&L", () => {
  it("reconciles the actual 10/10/5 SNDK holding without a shared ratio denominator", () => {
    const pos = position();
    const legs = pos.legs.map(leg => resolveLegPnl(pos, leg, prices()));
    expect(legs.map(leg => leg.pnl)).toEqual([12234,-4704,5975]);
    expect(legs.map(leg => leg.entryValue)).toEqual([52766,-13016,-24475]);
    expect(legs.map(leg => leg.marketValue)).toEqual([65000,-17720,-18500]);
    expect(legs.reduce((sum,leg) => sum + leg.pnl!,0)).toBe(getPnlDollars(pos));
    expect(legs.map(leg => leg.todayPnl)).toEqual([5000,2280,1500]);
    expect(getTodayPnlDollars(pos,prices())).toBe(8780);
  });
  it("uses the same resolved midpoint for total and daily P&L", () => {
    const pos=position(), quotes=prices();
    quotes.SNDK_20261023_1450_C={...quotes.SNDK_20261023_1450_C,last:90,bid:64,ask:66};
    const leg=resolveLegPnl(pos,pos.legs[0],quotes);
    expect(leg.pnl).toBe(12234);
    expect(leg.todayPnl).toBe(5000);
    expect(getTodayPnlDollars(pos,quotes)).toBe(8780);
  });
  it("never publishes a partial overnight total or daily percentage", () => {
    const pos=position(), quotes=prices();
    quotes.SNDK_20261023_1400_P.close=null;
    expect(resolveLegPnl(pos,pos.legs[2],quotes).todayPnl).toBeNull();
    expect(getTodayPnlDollars(pos,quotes)).toBeNull();
    expect(getOptionDailyChg(pos,quotes)).toBeNull();
  });
  it("same-day legs use fill basis and ignore an aggregate broker daily reading", () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-13T16:00:00Z"));
    const pos={...position(),entry_date:"2026-10-13",ib_daily_pnl:1};
    expect(pos.legs.map(leg=>resolveLegPnl(pos,leg,prices()).todayPnl)).toEqual([12234,-4704,5975]);
    expect(getTodayPnlDollars(pos,prices())).toBe(13505);
  });
  it("mixed-age legs preserve separate overnight and session-fill baselines", () => {
    const pos={...position(),basis_source:"mixed" as const,entry_cost:null};
    pos.legs[2].basis_source="session_fills";
    expect(pos.legs.map(leg=>resolveLegPnl(pos,leg,prices()).todayPnl)).toEqual([5000,2280,5975]);
    expect(getTodayPnlDollars(pos,prices())).toBe(13255);
  });
  it("does not allocate a broker-only aggregate to individual legs", () => {
    const pos={...position(),ib_daily_pnl:456};
    expect(getTodayPnlDollars(pos,{})).toBe(456);
    expect(resolveLegPnl(pos,pos.legs[0],{}).todayPnl).toBeNull();
  });
  it("uses stock and option multipliers separately in a covered-call combo", () => {
    const pos=position();
    pos.legs=[{direction:"LONG",contracts:100,type:"Stock",strike:null,entry_cost:10000,avg_cost:100,market_price:110,market_value:11000},
      {direction:"SHORT",contracts:1,type:"Call",strike:120,entry_cost:200,avg_cost:200,market_price:3,market_value:-300}];
    const quotes={SNDK:{symbol:"SNDK",last:110,close:105},SNDK_20261023_120_C:{symbol:"SNDK_20261023_120_C",last:3,close:2.5}} as Record<string,PriceData>;
    expect(pos.legs.map(leg=>resolveLegPnl(pos,leg,quotes).pnl)).toEqual([1000,-100]);
    expect(pos.legs.map(leg=>resolveLegPnl(pos,leg,quotes).todayPnl)).toEqual([500,-50]);
    expect(getTodayPnlDollars(pos,quotes)).toBe(450);
  });
  it("accepts an explicit contract multiplier without multiplying avg_cost again", () => {
    const pos=position();
    const leg={...pos.legs[0],contracts:2,multiplier:10,market_price:65,market_value:1300,entry_cost:1000,avg_cost:500};
    expect(resolveLegPnl(pos,leg,{}).pnl).toBe(300);
  });
  it.each([NaN,Infinity])("withholds invalid basis %s rather than coercing it to zero", entry_cost => {
    const pos=position(); pos.legs[0].entry_cost=entry_cost;
    expect(resolveLegPnl(pos,pos.legs[0],prices()).pnl).toBeNull();
    expect(getPnlDollars(pos)).toBeNull();
  });
  it("withholds total when no current or synced leg value is available", () => {
    const pos=position(); pos.legs[0].market_price=null; pos.legs[0].market_value=null;
    expect(resolveLegPnl(pos,pos.legs[0],{}).pnl).toBeNull();
    expect(getPnlDollars(pos)).toBeNull();
  });
});
