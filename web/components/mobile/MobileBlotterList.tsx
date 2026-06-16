"use client";

import type { BlotterTrade } from "@/lib/types";
import { fmtPrice } from "@/lib/positionUtils";
import { formatTradeDate } from "@/lib/blotter/formatTradeDate";
import Card from "./Card";
import MetricCell from "./MetricCell";

type MobileBlotterListProps = {
  trades: BlotterTrade[];
};

function getTradeDate(t: BlotterTrade): string | null {
  if (!t.executions || t.executions.length === 0) return null;
  return t.executions[t.executions.length - 1].time;
}

type PnlResult = {
  dollar: string;
  pct: string | null;
  tone: "pos" | "neg" | "mut";
  cardTone: "positive" | "negative" | "default";
};

function fmtRealized(t: BlotterTrade): PnlResult {
  if (t.realized_pnl == null) {
    return { dollar: "--", pct: null, tone: "mut", cardTone: "default" };
  }
  const sign = t.realized_pnl > 0 ? "+" : t.realized_pnl < 0 ? "-" : "";
  const dollar = `${sign}${fmtPrice(Math.abs(t.realized_pnl))}`;
  const realizedBasis =
    t.realized_cost_basis != null
      ? Math.abs(t.realized_cost_basis)
      : Math.abs(t.cost_basis ?? 0);
  const rawPct = realizedBasis > 0 ? (t.realized_pnl / realizedBasis) * 100 : null;
  const pct =
    rawPct != null
      ? `${rawPct >= 0 ? "+" : "-"}${Math.abs(rawPct).toFixed(1)}%`
      : null;
  const tone: PnlResult["tone"] =
    t.realized_pnl > 0 ? "pos" : t.realized_pnl < 0 ? "neg" : "mut";
  const cardTone: PnlResult["cardTone"] =
    t.realized_pnl >= 0 ? "positive" : "negative";
  return { dollar, pct, tone, cardTone };
}

function fmtQty(t: BlotterTrade): string {
  const qty = t.total_quantity ?? t.net_quantity;
  return qty != null ? String(qty) : "--";
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
        const id = `${t.symbol}-${i}`;
        const hasCommission =
          t.total_commission != null && t.total_commission !== 0;
        const toneClass = `m-metric__value--${realized.tone}`;

        return (
          <Card
            key={id}
            tone={realized.cardTone}
            testId={`mobile-blotter-${id}`}
            ariaLabel={`${t.symbol} ${t.contract_desc}`}
          >
            <div className="mobile-card__title-row">
              <div className="mobile-card__title">
                <span>{t.symbol}</span>
                <span className={`pill ${t.is_closed ? "neutral" : "defined"}`}>
                  {t.is_closed ? "Closed" : "Open"}
                </span>
              </div>
            </div>

            <div className="mobile-card__subtitle">{t.contract_desc}</div>

            {/* Primary metrics: Qty + Net P&L */}
            <div className="m-blotter-metrics">
              <MetricCell label="Qty" value={fmtQty(t)} size="secondary" />
              {/* Two-line Net P&L: dollar on top, pct below */}
              <div className="m-blotter-pnl-cell">
                <span className="m-metric__label">Net P&amp;L</span>
                <span className={`m-metric__value m-metric__value--secondary ${toneClass}`}>
                  {realized.dollar}
                </span>
                {realized.pct != null ? (
                  <span className={`m-blotter-pnl-pct ${toneClass}`}>{realized.pct}</span>
                ) : null}
              </div>
            </div>

            {/* Secondary row: type + date + commission (only when non-zero) */}
            <div className="mobile-card__chevron-row">
              <span className="mobile-card__subtitle">
                {t.sec_type}
                {tradeDate ? ` · ${formatTradeDate(tradeDate)}` : ""}
              </span>
              {hasCommission ? (
                <span className="mobile-card__subtitle">
                  Comm {fmtPrice(t.total_commission!)}
                </span>
              ) : null}
            </div>
          </Card>
        );
      })}
    </div>
  );
}
