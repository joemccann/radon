"use client";

import Modal from "./Modal";
import { fmtSignedUsdExact, fmtPct as fmtPctShared } from "@/lib/format";

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
  rows: PnlBreakdownRow[];
  total: number;
  totalLabel?: string;
  onClose: () => void;
  className?: string;
};

const fmtSigned = (_n: number, _d?: number) => fmtSignedUsdExact(_n);
const fmtPct = (n: number) => fmtPctShared(n, 1, true);

export default function PnlBreakdownModal({
  open, title, formula, col1Header, col2Header, rows, total, totalLabel = "TOTAL", onClose, className = "",
}: Props) {
  if (!open) return null;

  const sorted = [...rows].sort((a, b) => Math.abs(b.pnl) - Math.abs(a.pnl));

  return (
    <Modal open onClose={onClose} title={title} className={`pnl-breakdown-modal ${className}`}>
      {/* Total */}
      <div className="eb-total">
        <span className={`eb-total-value ${total >= 0 ? "positive" : "negative"}`}>
          {fmtSigned(total, 2)}
        </span>
      </div>

      {/* Formula proof */}
      <div className="eb-formula">
        <code>{formula}</code>
      </div>

      {rows.length === 0 ? (
        <div className="eb-empty">No position data available — sync portfolio from IB</div>
      ) : (
        <table className="eb-table">
          <thead>
            <tr>
              <th>TICKER</th>
              <th>STRUCTURE</th>
              <th className="text-right">{col1Header}</th>
              <th className="text-right">{col2Header}</th>
              <th className="text-right">P&L</th>
              <th className="text-right">%</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((row) => (
              <tr key={row.id} className="eb-row">
                <td className="eb-ticker">{row.ticker}</td>
                <td className="eb-structure">{row.structure}</td>
                <td className="eb-mono">{row.col1}</td>
                <td className="eb-mono">{row.col2}</td>
                <td className={`eb-mono ${row.pnl >= 0 ? "positive" : "negative"}`}>
                  {fmtSigned(row.pnl, 2)}
                </td>
                <td className={`eb-mono ${row.pnl >= 0 ? "positive" : "negative"}`}>
                  {row.pnlPct != null ? fmtPct(row.pnlPct) : "---"}
                </td>
              </tr>
            ))}
            {/* Total row */}
            <tr className="pb-total-row">
              <td colSpan={4} className="pb-total-label">{totalLabel}</td>
              <td className={`eb-mono ${total >= 0 ? "positive" : "negative"}`}>
                {fmtSigned(total, 2)}
              </td>
              <td></td>
            </tr>
          </tbody>
        </table>
      )}
    </Modal>
  );
}
