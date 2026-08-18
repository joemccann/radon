"use client";

import MetricBreakdownModal, {
  type MetricBreakdownColumn,
  type MetricBreakdownRow,
} from "./MetricBreakdownModal";
import { fmtSigned, fmtPct } from "@/lib/format/money";

export type PnlBreakdownRow = {
  id: string | number;
  ticker: string;
  structure: string;
  col1: string;  // e.g. "Entry Cost" or "Close"
  col2: string;  // e.g. "Mkt Value" or "Current"
  pnl: number;
  pnlPct?: number | null;
};

type Props = {
  open: boolean;
  title: string;
  formula: string;
  col1Header: string;
  col2Header: string;
  pctHeader?: string;
  rows: PnlBreakdownRow[];
  total: number;
  totalLabel?: string;
  onClose: () => void;
  className?: string;
};

export default function PnlBreakdownModal({
  open, title, formula, col1Header, col2Header, pctHeader = "%", rows, total, totalLabel = "TOTAL", onClose, className = "",
}: Props) {
  const columns: MetricBreakdownColumn[] = [
    { header: "TICKER" },
    { header: "STRUCTURE" },
    { header: col1Header, className: "text-right" },
    { header: col2Header, className: "text-right" },
    { header: "P&L", className: "text-right" },
    { header: pctHeader, className: "text-right" },
  ];

  const breakdownRows: MetricBreakdownRow[] = rows.map((row) => ({
    id: row.id,
    sortValue: row.pnl,
    values: [row.ticker, row.structure, row.col1, row.col2, row.pnl, row.pnlPct ?? null],
    cells: [
      { content: row.ticker, className: "eb-ticker" },
      { content: row.structure, className: "eb-structure" },
      { content: row.col1, className: "eb-mono" },
      { content: row.col2, className: "eb-mono" },
      { content: fmtSigned(row.pnl, 2), className: "eb-mono", tone: row.pnl >= 0 ? "positive" : "negative" },
      { content: row.pnlPct != null ? fmtPct(row.pnlPct) : "N/A", className: "eb-mono", tone: row.pnl >= 0 ? "positive" : "negative" },
    ],
  }));

  return (
    <MetricBreakdownModal
      open={open}
      onClose={onClose}
      title={title}
      className={`pnl-breakdown-modal ${className}`}
      value={fmtSigned(total, 2)}
      valueTone={total >= 0 ? "positive" : "negative"}
      formula={formula}
      columns={columns}
      rows={breakdownRows}
      footer={[
        { content: totalLabel, className: "pb-total-label", colSpan: 4 },
        { content: fmtSigned(total, 2), className: "eb-mono", tone: total >= 0 ? "positive" : "negative" },
        { content: "" },
      ]}
      emptyMessage="No position data available. Sync portfolio from IB."
    />
  );
}
