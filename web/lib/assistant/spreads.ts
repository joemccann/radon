export type SpreadKind = "bull_call" | "bear_call" | "bull_put" | "bear_put";

export type ChainContract = {
  strike: number;
  right: "C" | "P";
  bid: number | null;
  ask: number | null;
  mid: number | null;
  iv?: number | null;
  oi?: number | null;
  volume?: number | null;
};

export type RankedSpread = {
  kind: SpreadKind;
  buyStrike: number;
  sellStrike: number;
  right: "C" | "P";
  debit: number;
  credit: number;
  width: number;
  maxProfit: number;
  maxLoss: number;
  maxPayoutDollars: number;
  riskDollars: number;
  rewardToRisk: number;
  convex: boolean;
  buyMid: number;
  sellMid: number;
};

export function midOf(contract: ChainContract): number | null {
  if (typeof contract.mid === "number" && Number.isFinite(contract.mid) && contract.mid > 0) {
    return contract.mid;
  }
  const bid = contract.bid;
  const ask = contract.ask;
  if (typeof bid === "number" && typeof ask === "number" && bid > 0 && ask > 0) {
    return (bid + ask) / 2;
  }
  return null;
}

function rightFor(kind: SpreadKind): "C" | "P" {
  return kind.endsWith("call") ? "C" : "P";
}

function isDebitSpread(kind: SpreadKind): boolean {
  return kind === "bull_call" || kind === "bear_put";
}

function pairMatches(kind: SpreadKind, buyStrike: number, sellStrike: number): boolean {
  if (kind === "bull_call" || kind === "bear_put") return buyStrike < sellStrike;
  return buyStrike > sellStrike;
}

export function rankVerticalSpreads(args: {
  spot: number;
  contracts: ChainContract[];
  kind: SpreadKind;
  quantity?: number;
  limit?: number;
}): RankedSpread[] {
  const quantity = Math.max(1, Math.floor(args.quantity ?? 1));
  const limit = Math.max(1, Math.min(args.limit ?? 8, 20));
  const right = rightFor(args.kind);
  const quoted = args.contracts
    .filter((row) => row.right === right)
    .map((row) => ({ strike: row.strike, mid: midOf(row) }))
    .filter((row): row is { strike: number; mid: number } => row.mid != null)
    .sort((a, b) => a.strike - b.strike);

  const ranked: RankedSpread[] = [];
  for (let i = 0; i < quoted.length; i += 1) {
    for (let j = 0; j < quoted.length; j += 1) {
      if (i === j) continue;
      const buy = quoted[i];
      const sell = quoted[j];
      if (!pairMatches(args.kind, buy.strike, sell.strike)) continue;
      const width = Math.abs(sell.strike - buy.strike);
      if (width <= 0) continue;
      if (isDebitSpread(args.kind)) {
        const debit = buy.mid - sell.mid;
        if (!(debit > 0) || debit >= width) continue;
        const maxProfit = width - debit;
        const rewardToRisk = maxProfit / debit;
        ranked.push({
          kind: args.kind,
          buyStrike: buy.strike,
          sellStrike: sell.strike,
          right,
          debit: round4(debit),
          credit: 0,
          width,
          maxProfit: round4(maxProfit),
          maxLoss: round4(debit),
          maxPayoutDollars: round2(maxProfit * 100 * quantity),
          riskDollars: round2(debit * 100 * quantity),
          rewardToRisk: round4(rewardToRisk),
          convex: maxProfit >= 2 * debit,
          buyMid: round4(buy.mid),
          sellMid: round4(sell.mid),
        });
      } else {
        const credit = sell.mid - buy.mid;
        if (!(credit > 0) || credit >= width) continue;
        const maxLoss = width - credit;
        const rewardToRisk = maxLoss > 0 ? credit / maxLoss : 0;
        ranked.push({
          kind: args.kind,
          buyStrike: buy.strike,
          sellStrike: sell.strike,
          right,
          debit: 0,
          credit: round4(credit),
          width,
          maxProfit: round4(credit),
          maxLoss: round4(maxLoss),
          maxPayoutDollars: round2(credit * 100 * quantity),
          riskDollars: round2(maxLoss * 100 * quantity),
          rewardToRisk: round4(rewardToRisk),
          convex: credit >= 2 * maxLoss,
          buyMid: round4(buy.mid),
          sellMid: round4(sell.mid),
        });
      }
    }
  }

  ranked.sort((a, b) => {
    if (a.convex !== b.convex) return a.convex ? -1 : 1;
    return b.maxPayoutDollars - a.maxPayoutDollars;
  });
  return ranked.slice(0, limit);
}

function round4(value: number): number {
  return Math.round(value * 10_000) / 10_000;
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}
