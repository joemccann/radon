/**
 * Informed-flow badge helper.
 *
 * Summarises a ticker's congress + insider activity into a compact count + a
 * bias bucket so the scanner row and the flow-analysis panel render a
 * consistent "informed activity" chip. The bias drives the brand token
 * (buy-skew = positive, sell-skew = negative, mixed = signal-core, none =
 * muted); colour selection lives with the consumer so this stays pure.
 */

export type InformedFlowBias = "buy-skew" | "sell-skew" | "mixed" | "none";

export interface InformedFlowBadge {
  /** Total congress + insider rows. */
  count: number;
  bias: InformedFlowBias;
  /** Compact label, e.g. "3 trades" / "No activity". */
  label: string;
}

interface SidedRow {
  side?: string | null;
}

interface InformedFlowLike {
  congress_trades?: SidedRow[] | null;
  insider_trades?: SidedRow[] | null;
  institutional_summary?: unknown;
}

function countSide(rows: SidedRow[], side: string): number {
  return rows.filter((r) => (r.side ?? "").toUpperCase() === side).length;
}

function biasFor(buys: number, sells: number): InformedFlowBias {
  if (buys === 0 && sells === 0) return "none";
  if (buys > sells) return "buy-skew";
  if (sells > buys) return "sell-skew";
  return "mixed";
}

export function informedFlowBadge(payload: InformedFlowLike | null | undefined): InformedFlowBadge {
  const congress = payload?.congress_trades ?? [];
  const insider = payload?.insider_trades ?? [];
  const rows = [...congress, ...insider];
  const count = rows.length;

  if (count === 0) {
    return { count: 0, bias: "none", label: "No activity" };
  }

  const buys = countSide(rows, "BUY");
  const sells = countSide(rows, "SELL");
  return {
    count,
    bias: biasFor(buys, sells),
    label: `${count} ${count === 1 ? "trade" : "trades"}`,
  };
}

export const INFORMED_FLOW_BIAS_TOKEN: Record<InformedFlowBias, string> = {
  "buy-skew": "var(--positive)",
  "sell-skew": "var(--negative)",
  mixed: "var(--signal-core)",
  none: "var(--text-muted)",
};
