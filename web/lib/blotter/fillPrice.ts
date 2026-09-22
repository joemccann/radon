import type { BlotterTrade } from "../types";

export const FILL_PRICE_HELP = "Quantity-weighted recorded execution price, excluding commissions. Options use quoted premium, not contract notional.";
export const AGGREGATE_FILL_PRICE_HELP = "Recorded aggregate price. This journal row can combine buy and sell executions; it is not a separate entry or exit price.";

export function blotterFillPrice(trade: Pick<BlotterTrade, "executions">): {
  price: number | null;
  aggregated: boolean;
} {
  const executions = trade.executions;
  const sides = new Set(executions.map((e) => e.side));
  const aggregated = sides.size > 1 || executions.some((e) => e.price_is_aggregate);
  let quantity = 0;
  let value = 0;
  for (const e of executions) {
    // Never publish a partial average or turn missing prices into zero.
    if (e.price == null || !Number.isFinite(e.price)
      || !Number.isFinite(e.quantity) || e.quantity <= 0) {
      return { price: null, aggregated };
    }
    quantity += e.quantity;
    value += e.price * e.quantity;
  }
  const price = value / quantity;
  return { price: Number.isFinite(price) ? price : null, aggregated };
}

export function formatBlotterFillPrice(price: number | null): string {
  return price == null ? "---" : price.toLocaleString("en-US", {
    style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 4,
  });
}
