"use client";

import type { BlotterTrade } from "@/lib/types";
import { fmtPrice, fmtUsd } from "@/lib/positionUtils";
import Card from "./Card";

type MobileBlotterListProps = {
  trades: BlotterTrade[];
};

function getTradeDate(t: BlotterTrade): string | null {
  if (!t.executions || t.executions.length === 0) return null;
  return t.executions[t.executions.length - 1].time;
}

function fmtRealized(t: BlotterTrade): { text: string; tone: "positive" | "negative" | "muted" } {
  if (t.realized_pnl == null) return { text: "—", tone: "muted" };
  const sign = t.realized_pnl > 0 ? "+" : t.realized_pnl < 0 ? "-" : "";
  const value = `${sign}${fmtPrice(Math.abs(t.realized_pnl))}`;
  const realizedBasis = t.realized_cost_basis != null ? Math.abs(t.realized_cost_basis) : Math.abs(t.cost_basis ?? 0);
  const pct = realizedBasis > 0 ? (t.realized_pnl / realizedBasis) * 100 : null;
  const pctText = pct != null ? ` (${pct >= 0 ? "+" : "-"}${Math.abs(pct).toFixed(1)}%)` : "";
  return {
    text: `${value}${pctText}`,
    tone: t.realized_pnl >= 0 ? "positive" : "negative",
  };
}

export default function MobileBlotterList({ trades }: MobileBlotterListProps) {
  if (trades.length === 0) {
    return (
      <div className="mobile-empty-state" data-testid="mobile-blotter-list-empty">
        <span>No historical trades.</span>
      </div>
    );
  }

  return (
    <div className="mobile-card-list" data-testid="mobile-blotter-list">
      {trades.map((t, i) => {
        const realized = fmtRealized(t);
        const tradeDate = getTradeDate(t);
        const cardTone = realized.tone === "positive" ? "positive" : realized.tone === "negative" ? "negative" : "default";
        const id = `${t.symbol}-${i}`;

        return (
          <Card key={id} tone={cardTone} testId={`mobile-blotter-${id}`} ariaLabel={`${t.symbol} ${t.contract_desc}`}>
            <div className="mobile-card__title-row">
              <div className="mobile-card__title">
                <span>{t.symbol}</span>
                <span className={`pill ${t.is_closed ? "neutral" : "defined"}`}>{t.is_closed ? "Closed" : "Open"}</span>
              </div>
              <div className="mobile-card__pnl">
                <div className={`mobile-card__pnl-value mobile-card-row__value--${realized.tone}`}>{realized.text}</div>
              </div>
            </div>

            <div className="mobile-card__subtitle">{t.contract_desc}</div>

            <div className="mobile-card__metrics">
              <div className="mobile-card__metric">
                <span className="mobile-card__metric-label">Qty</span>
                <span className="mobile-card__metric-value">{t.total_quantity ?? t.net_quantity}</span>
              </div>
              <div className="mobile-card__metric">
                <span className="mobile-card__metric-label">Cost</span>
                <span className="mobile-card__metric-value">{t.cost_basis != null ? fmtUsd(t.cost_basis) : "—"}</span>
              </div>
              <div className="mobile-card__metric">
                <span className="mobile-card__metric-label">Proceeds</span>
                <span className="mobile-card__metric-value">{t.proceeds != null ? fmtUsd(t.proceeds) : "—"}</span>
              </div>
            </div>

            <div className="mobile-card__chevron-row">
              <span className="mobile-card__subtitle">{t.sec_type}{tradeDate ? ` · ${new Date(tradeDate).toLocaleDateString()}` : ""}</span>
              <span className="mobile-card__subtitle">Comm {t.total_commission != null ? fmtPrice(t.total_commission) : "—"}</span>
            </div>
          </Card>
        );
      })}
    </div>
  );
}
