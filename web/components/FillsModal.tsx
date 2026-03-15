"use client";

import type { ExecutedOrder } from "@/lib/types";

type Props = {
  open: boolean;
  fills: ExecutedOrder[];
  totalRealizedPnl: number;
  netLiquidation?: number;
  onClose: () => void;
};

const fmtPnl = (n: number | null) => {
  if (n == null) return "---";
  const abs = Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${n >= 0 ? "+" : "-"}$${abs}`;
};

const fmtPrice = (n: number | null) =>
  n == null ? "---" : `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const fmtTime = (iso: string) => {
  try {
    return new Date(iso).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  } catch {
    return iso;
  }
};

const fmtPct = (n: number) => `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;

export default function FillsModal({ open, fills, totalRealizedPnl, netLiquidation, onClose }: Props) {
  if (!open) return null;

  const fillsWithPnl = fills.filter((f) => f.realizedPNL != null);
  const hasFills = fills.length > 0;

  return (
    <div className="fills-modal modal-backdrop" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <span className="modal-title">TODAY&apos;S FILLS</span>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        {!hasFills ? (
          <div className="fills-empty">
            <p>No fills this session.</p>
            <p className="fills-empty-sub">Realized P&L = $0.00</p>
          </div>
        ) : (
          <>
            <table className="fills-table">
              <thead>
                <tr>
                  <th>TIME</th>
                  <th>SYMBOL</th>
                  <th>SIDE</th>
                  <th className="text-right">QTY</th>
                  <th className="text-right">PRICE</th>
                  <th className="text-right">COMMISSION</th>
                  <th className="text-right">REALIZED P&L</th>
                </tr>
              </thead>
              <tbody>
                {fills.map((fill) => (
                  <tr key={fill.execId} className={fill.realizedPNL != null ? (fill.realizedPNL >= 0 ? "fills-row-positive" : "fills-row-negative") : ""}>
                    <td className="mono">{fmtTime(fill.time)}</td>
                    <td className="mono">{fill.symbol}</td>
                    <td className={`mono fills-side fills-side-${fill.side.toLowerCase()}`}>{fill.side}</td>
                    <td className="mono text-right">{fill.quantity}</td>
                    <td className="mono text-right">{fmtPrice(fill.avgPrice)}</td>
                    <td className="mono text-right">{fill.commission != null ? fmtPnl(fill.commission) : "---"}</td>
                    <td className={`mono text-right ${fill.realizedPNL != null ? (fill.realizedPNL >= 0 ? "positive" : "negative") : ""}`}>
                      {fmtPnl(fill.realizedPNL)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div className="fills-summary">
              <div className="fills-summary-formula">
                {fillsWithPnl.map((f, i) => (
                  <span key={f.execId}>
                    {i > 0 && <span className="fills-op">{f.realizedPNL! >= 0 ? " + " : " "}</span>}
                    <span className={f.realizedPNL! >= 0 ? "positive" : "negative"}>
                      {fmtPnl(f.realizedPNL)}
                    </span>
                    <span className="fills-label"> ({f.symbol})</span>
                  </span>
                ))}
                {fillsWithPnl.length === 0 && <span className="fills-label">No closed positions this session</span>}
              </div>
              <div className="fills-summary-total">
                <span className="fills-total-label">REALIZED P&L</span>
                <span className={`fills-total-value ${totalRealizedPnl >= 0 ? "positive" : "negative"}`}>
                  {fmtPnl(totalRealizedPnl)}
                  {netLiquidation != null && netLiquidation > 0 && (
                    <span className="fills-total-pct"> ({fmtPct(totalRealizedPnl / netLiquidation * 100)})</span>
                  )}
                </span>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
