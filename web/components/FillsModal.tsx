"use client";

import { Receipt } from "lucide-react";
import Modal from "./Modal";
import SectionEmptyState from "./SectionEmptyState";
import SortTh from "./SortTh";
import { useSort } from "@/lib/useSort";
import type { ExecutedOrder } from "@/lib/types";

type FillSortKey = "time" | "symbol" | "side" | "quantity" | "avgPrice" | "commission" | "realizedPNL";

function fillExtract(fill: ExecutedOrder, key: FillSortKey): string | number | null {
  switch (key) {
    case "time": return fill.time;
    case "symbol": return fill.symbol;
    case "side": return fill.side;
    case "quantity": return fill.quantity;
    case "avgPrice": return fill.avgPrice;
    case "commission": return fill.commission;
    case "realizedPNL": return fill.realizedPNL;
    default: return null;
  }
}

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
  const date = new Date(iso);
  if (!Number.isFinite(date.getTime())) return iso;
  return date.toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: "America/New_York",
  });
};

const fmtPct = (n: number) => `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;

export default function FillsModal({ open, fills, totalRealizedPnl, netLiquidation, onClose }: Props) {
  const fillsWithPnl = fills.filter((f) => f.realizedPNL != null);
  const hasFills = fills.length > 0;
  const { sorted, sort, toggle } = useSort(fills, fillExtract);

  return (
    <Modal open={open} onClose={onClose} title="TODAY'S FILLS" className="fills-modal">
      {!hasFills ? (
        <SectionEmptyState
          icon={Receipt}
          headline="No fills this session"
          secondary="Realized P&L = $0.00"
          testId="fills-empty"
        />
      ) : (
        <>
          <div className="table-wrap">
            <table className="fills-table">
              <thead>
                <tr>
                  <SortTh<FillSortKey> label="TIME" sortKey="time" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<FillSortKey> label="SYMBOL" sortKey="symbol" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<FillSortKey> label="SIDE" sortKey="side" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<FillSortKey> label="QTY" sortKey="quantity" className="text-right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<FillSortKey> label="PRICE" sortKey="avgPrice" className="text-right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<FillSortKey> label="COMMISSION" sortKey="commission" className="text-right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<FillSortKey> label="REALIZED P&L" sortKey="realizedPNL" className="text-right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                </tr>
              </thead>
              <tbody>
                {sorted.map((fill) => (
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
          </div>

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
    </Modal>
  );
}
