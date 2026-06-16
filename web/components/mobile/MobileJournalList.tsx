"use client";

import { useState } from "react";
import { ChevronRight } from "lucide-react";
import type { TradeEntry } from "@/lib/types";
import { fmtPrice } from "@/lib/positionUtils";
import Card from "./Card";
import MetricCell from "./MetricCell";

type SortKey = "date" | "pnl" | "ror";

type MobileJournalListProps = {
  trades: TradeEntry[];
};

function fmtPnl(value: number | null | undefined): { text: string; tone: "pos" | "neg" | "mut" } {
  if (value == null || !Number.isFinite(value)) return { text: "—", tone: "mut" };
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return {
    text: `${sign}${fmtPrice(Math.abs(value))}`,
    tone: value > 0 ? "pos" : value < 0 ? "neg" : "mut",
  };
}

function fmtPctValue(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}${Math.abs(value).toFixed(1)}%`;
}

function pillTone(decision: string): "pos" | "neg" | "warn" | null {
  if (decision === "EXECUTED" || decision === "OPEN") return "pos";
  if (decision === "CLOSED") return null;
  if (decision === "FREED" || decision === "CONVERTED") return "warn";
  if (decision === "IB_AUTO_IMPORT") return "warn";
  return "neg";
}

function pillLabel(decision: string): string {
  if (decision === "IB_AUTO_IMPORT") return "IB IMPORT";
  return decision;
}

function sortTrades(trades: TradeEntry[], key: SortKey): TradeEntry[] {
  return [...trades].sort((a, b) => {
    if (key === "date") {
      return (b.date ?? "").localeCompare(a.date ?? "");
    }
    if (key === "pnl") {
      return (b.realized_pnl ?? -Infinity) - (a.realized_pnl ?? -Infinity);
    }
    if (key === "ror") {
      const aRor = a.return_on_risk ?? -Infinity;
      const bRor = b.return_on_risk ?? -Infinity;
      return bRor - aRor;
    }
    return 0;
  });
}

export default function MobileJournalList({ trades }: MobileJournalListProps) {
  const [sortKey, setSortKey] = useState<SortKey>("date");

  if (trades.length === 0) {
    return (
      <div className="mobile-empty-state" data-testid="mobile-journal-list-empty">
        <span>No trades in journal.</span>
      </div>
    );
  }

  const sorted = sortTrades(trades, sortKey);

  return (
    <div data-testid="mobile-journal-list">
      <div className="m-sortbar" role="toolbar" aria-label="Sort trades">
        {(["date", "pnl", "ror"] as SortKey[]).map((k) => (
          <button
            key={k}
            className={`m-chip${sortKey === k ? " m-chip--active" : ""}`}
            onClick={() => setSortKey(k)}
            type="button"
          >
            {k === "date" ? "Date" : k === "pnl" ? "P&L" : "RoR"}
          </button>
        ))}
      </div>

      <div className="mobile-card-list">
        {sorted.map((t) => {
          const pnl = fmtPnl(t.realized_pnl);
          const cardTone = pnl.tone === "pos" ? "positive" : pnl.tone === "neg" ? "negative" : "default";
          const tone = pillTone(t.decision);
          const label = pillLabel(t.decision);
          const qty = t.contracts ?? t.shares ?? t.quantity ?? null;
          const ror = t.return_on_risk != null ? t.return_on_risk * 100 : null;
          const dateRange = t.close_date ? `${t.date} – ${t.close_date}` : t.date;

          return (
            <div key={t.id} className="m-card-press" data-testid={`mobile-journal-${t.id}`}>
              <Card tone={cardTone} ariaLabel={`${t.ticker} ${t.structure}`}>
                {/* Title row: ticker + decision pill + chevron */}
                <div className="mobile-card__title-row">
                  <div className="mobile-card__title" style={{ gap: 6 }}>
                    <span style={{ fontFamily: "var(--font-mono)", fontWeight: 700, fontSize: 15 }}>{t.ticker}</span>
                    {tone ? (
                      <span className={`m-pill m-pill--${tone}`} style={{ minHeight: "auto", width: "auto", padding: "2px 8px", fontSize: 11 }}>{label}</span>
                    ) : (
                      <span className="m-pill" style={{ minHeight: "auto", width: "auto", padding: "2px 8px", fontSize: 11, color: "var(--text-muted)", borderColor: "var(--line-grid)" }}>{label}</span>
                    )}
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                    <div style={{ textAlign: "right" }}>
                      <div style={{ fontFamily: "var(--font-mono)", fontVariantNumeric: "tabular-nums", fontWeight: 600, fontSize: 14, color: pnl.tone === "pos" ? "var(--positive)" : pnl.tone === "neg" ? "var(--negative)" : "var(--text-muted)" }}>
                        {pnl.text}
                      </div>
                    </div>
                    <ChevronRight size={14} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
                  </div>
                </div>

                {/* Structure subtitle */}
                <div className="mobile-card__subtitle" style={{ marginBottom: 8 }}>{t.structure}</div>

                {/* 2x2 MetricCell grid */}
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px 12px", marginBottom: 6 }}>
                  <MetricCell label="Qty" value={qty != null ? String(qty) : "—"} />
                  <MetricCell label="Entry" value={t.entry_cost != null ? fmtPrice(Math.abs(t.entry_cost)) : "—"} />
                  <MetricCell label="Risk" value={t.max_risk != null ? fmtPrice(Math.abs(t.max_risk)) : "—"} />
                  <MetricCell
                    label="RoR"
                    value={fmtPctValue(ror)}
                    tone={ror != null ? (ror > 0 ? "pos" : ror < 0 ? "neg" : "mut") : "mut"}
                  />
                </div>

                {/* Footer: date range + optional edge type */}
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 2 }}>
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-muted)" }}>{dateRange}</span>
                  {t.edge_analysis?.edge_type ? (
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-muted)" }}>{t.edge_analysis.edge_type}</span>
                  ) : null}
                </div>
              </Card>
            </div>
          );
        })}
      </div>
    </div>
  );
}
