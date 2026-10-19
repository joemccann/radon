import type { BlotterTrade } from "@/lib/types";
import { blotterFillPrice } from "@/lib/blotter/fillPrice";

type CsvValue = string | number | null | undefined;

const COLUMNS: ReadonlyArray<[header: string, value: (t: BlotterTrade) => CsvValue]> = [
  ["Date", (t) => t.executions.at(-1)?.time],
  ["Symbol", (t) => t.symbol],
  ["Description", (t) => t.contract_desc],
  ["Type", (t) => t.sec_type],
  ["Status", (t) => (t.is_closed ? "Closed" : "Open")],
  ["Qty", (t) => t.total_quantity ?? t.net_quantity],
  ["Avg Fill", (t) => blotterFillPrice(t).price],
  ["Commission", (t) => t.total_commission],
  ["Realized P&L", (t) => t.realized_pnl],
  ["Cost Basis", (t) => t.cost_basis],
  ["Proceeds", (t) => t.proceeds],
];

const FORMULA_PREFIX = /^[=+\-@\t\r]/;
const NEEDS_QUOTES = /[",\r\n]/;

/** A spreadsheet must read a text cell as text, never as a formula. */
function csvCell(value: CsvValue): string {
  if (value == null || (typeof value === "number" && !Number.isFinite(value))) return "";
  if (typeof value === "number") return String(value);
  const text = FORMULA_PREFIX.test(value) ? `'${value}` : value;
  return NEEDS_QUOTES.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** Raw (unformatted) values in the table's column order; unknown values stay empty. */
export function blotterTradesToCsv(trades: readonly BlotterTrade[]): string {
  const header = COLUMNS.map(([name]) => csvCell(name)).join(",");
  const rows = trades.map((t) => COLUMNS.map(([, value]) => csvCell(value(t))).join(","));
  return [header, ...rows].join("\r\n");
}

export function blotterCsvFilename(asOf: string | null | undefined): string {
  const day = asOf?.slice(0, 10) || new Date().toISOString().slice(0, 10);
  return `radon-historical-trades-${day}.csv`;
}
