"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, XCircle } from "lucide-react";
import type { PositionFillGroup } from "@/components/WorkspaceSections";
import { fmtPrice } from "@/lib/positionUtils";
import Card from "./Card";

type MobileExecutedListProps = {
  groups: PositionFillGroup[];
};

function fmtPnl(value: number | null | undefined): { text: string; tone: "positive" | "negative" | "muted" } {
  if (value == null || !Number.isFinite(value)) return { text: "—", tone: "muted" };
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return {
    text: `${sign}${fmtPrice(Math.abs(value))}`,
    tone: value > 0 ? "positive" : value < 0 ? "negative" : "muted",
  };
}

export default function MobileExecutedList({ groups }: MobileExecutedListProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (groups.length === 0) {
    return (
      <div className="mobile-empty-state" data-testid="mobile-executed-list-empty">
        <span>No fills this session.</span>
      </div>
    );
  }

  return (
    <div className="mobile-card-list" data-testid="mobile-executed-list">
      {groups.map((group) => {
        const isCancelled = group.fills[0]?.side === "CANCELLED";
        const pnl = fmtPnl(group.totalPnL);
        const cardTone = isCancelled ? "warning" : pnl.tone === "positive" ? "positive" : pnl.tone === "negative" ? "negative" : "default";
        const isExpanded = expandedId === group.id;
        const canExpand = group.fills.length > 1;
        const cleanDescription = group.description.replace(/^(Opened|Closed)\s+\w+\s*/, "");
        const pillLabel = isCancelled ? "CANCELLED" : group.isClosing ? "CLOSE" : "OPEN";
        const pillClass = isCancelled ? "cancelled" : group.isClosing ? "distrib" : "accum";

        return (
          <Card
            key={group.id}
            tone={cardTone}
            testId={`mobile-executed-${group.id}`}
            ariaLabel={`${group.symbol} ${pillLabel}`}
            onClick={canExpand ? () => setExpandedId(isExpanded ? null : group.id) : undefined}
          >
            <div className="mobile-card__title-row">
              <div className="mobile-card__title">
                <span>{group.symbol}</span>
                <span className={`pill ${pillClass}`}>{pillLabel}</span>
                {isCancelled ? <XCircle size={12} className="cancelled-icon" /> : null}
              </div>
              <div className="mobile-card__pnl">
                <div className={`mobile-card__pnl-value mobile-card-row__value--${pnl.tone}`}>{pnl.text}</div>
              </div>
            </div>

            {cleanDescription ? (
              <div className="mobile-card__subtitle">{cleanDescription}</div>
            ) : null}

            <div className="mobile-card__metrics">
              <div className="mobile-card__metric">
                <span className="mobile-card__metric-label">Qty</span>
                <span className="mobile-card__metric-value">{group.totalQuantity}</span>
              </div>
              <div className="mobile-card__metric">
                <span className="mobile-card__metric-label">Net</span>
                <span className="mobile-card__metric-value">{group.netPrice != null ? fmtPrice(group.netPrice) : "—"}</span>
              </div>
              <div className="mobile-card__metric">
                <span className="mobile-card__metric-label">Comm</span>
                <span className="mobile-card__metric-value">{group.totalCommission !== 0 ? fmtPrice(group.totalCommission) : "—"}</span>
              </div>
            </div>

            <div className="mobile-card__chevron-row">
              <span className="mobile-card__subtitle">{new Date(group.time).toLocaleTimeString()}</span>
              {canExpand ? (
                <span className="mobile-card__chevron" aria-hidden>
                  {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                </span>
              ) : null}
            </div>

            {isExpanded ? (
              <div className="mobile-card__detail" data-testid={`mobile-executed-${group.id}-fills`}>
                {group.fills.map((fill, idx) => {
                  const displaySide = fill.side === "BOT" ? "BUY" : fill.side === "SLD" ? "SELL" : fill.side;
                  return (
                    <div key={`${fill.execId}-${idx}`} className="mobile-card__leg-row">
                      <div className="mobile-card__leg-desc">
                        {displaySide} {fill.quantity}x{" "}
                        {fill.contract.secType === "OPT" && fill.contract.strike
                          ? `${fill.contract.right ?? ""} $${fill.contract.strike}`
                          : fill.contract.secType}
                      </div>
                      <div className="mobile-card__leg-metrics">
                        <span className="mobile-card__leg-meta">
                          {fill.avgPrice != null ? fmtPrice(fill.avgPrice) : "—"}
                        </span>
                        <span className="mobile-card__leg-meta">
                          {new Date(fill.time).toLocaleTimeString()}
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : null}
          </Card>
        );
      })}
    </div>
  );
}
