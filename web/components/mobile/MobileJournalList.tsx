"use client";

import type { TradeEntry } from "@/lib/types";
import { fmtPrice } from "@/lib/positionUtils";
import Card from "./Card";

type MobileJournalListProps = {
  trades: TradeEntry[];
};

function fmtPnl(value: number | null | undefined): { text: string; tone: "positive" | "negative" | "muted" } {
  if (value == null || !Number.isFinite(value)) return { text: "—", tone: "muted" };
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return {
    text: `${sign}${fmtPrice(Math.abs(value))}`,
    tone: value > 0 ? "positive" : value < 0 ? "negative" : "muted",
  };
}

function fmtPctValue(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}${Math.abs(value).toFixed(1)}%`;
}

function decisionPill(decision: string): { className: string; label: string } {
  if (decision === "EXECUTED" || decision === "OPEN") return { className: "pill bullish", label: decision };
  if (decision === "CLOSED") return { className: "pill neutral", label: "CLOSED" };
  if (decision === "FREED" || decision === "CONVERTED") return { className: "pill lean-bullish", label: decision };
  if (decision === "IB_AUTO_IMPORT") return { className: "pill ib-import", label: "IB IMPORT" };
  return { className: "pill bearish", label: decision };
}

export default function MobileJournalList({ trades }: MobileJournalListProps) {
  if (trades.length === 0) {
    return (
      <div className="mobile-empty-state" data-testid="mobile-journal-list-empty">
        <span>No trades in journal.</span>
      </div>
    );
  }

  return (
    <div className="mobile-card-list" data-testid="mobile-journal-list">
      {trades.map((t) => {
        const pnl = fmtPnl(t.realized_pnl);
        const cardTone = pnl.tone === "positive" ? "positive" : pnl.tone === "negative" ? "negative" : "default";
        const pill = decisionPill(t.decision);
        const qty = t.contracts ?? t.shares ?? t.quantity ?? null;
        const ror = t.return_on_risk != null ? t.return_on_risk * 100 : null;

        return (
          <Card key={t.id} tone={cardTone} testId={`mobile-journal-${t.id}`} ariaLabel={`${t.ticker} ${t.structure}`}>
            <div className="mobile-card__title-row">
              <div className="mobile-card__title">
                <span>{t.ticker}</span>
                <span className={pill.className}>{pill.label}</span>
              </div>
              <div className="mobile-card__pnl">
                <div className={`mobile-card__pnl-value mobile-card-row__value--${pnl.tone}`}>{pnl.text}</div>
                {ror != null ? (
                  <div className={`mobile-card__pnl-pct mobile-card-row__value--${pnl.tone}`}>{fmtPctValue(ror)}</div>
                ) : null}
              </div>
            </div>

            <div className="mobile-card__subtitle">{t.structure}</div>

            <div className="mobile-card__metrics">
              <div className="mobile-card__metric">
                <span className="mobile-card__metric-label">Qty</span>
                <span className="mobile-card__metric-value">{qty ?? "—"}</span>
              </div>
              <div className="mobile-card__metric">
                <span className="mobile-card__metric-label">Entry</span>
                <span className="mobile-card__metric-value">{t.entry_cost != null ? fmtPrice(Math.abs(t.entry_cost)) : "—"}</span>
              </div>
              <div className="mobile-card__metric">
                <span className="mobile-card__metric-label">Risk</span>
                <span className="mobile-card__metric-value">{t.max_risk != null ? fmtPrice(Math.abs(t.max_risk)) : "—"}</span>
              </div>
            </div>

            <div className="mobile-card__chevron-row">
              <span className="mobile-card__subtitle">{t.date}{t.close_date ? ` → ${t.close_date}` : ""}</span>
              {t.edge_analysis?.edge_type ? (
                <span className="mobile-card__subtitle">{t.edge_analysis.edge_type}</span>
              ) : null}
            </div>
          </Card>
        );
      })}
    </div>
  );
}
