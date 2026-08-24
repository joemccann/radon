import { currentIbDailyPnl } from "./ibDailyPnlSession";
import type { PortfolioData } from "./types";
import { assessMargin } from "./marginWarning";
import { fmtMoneySigned } from "./format/money";

export type KpiTone = "core" | "fault" | "neutral";
export type KpiBarTone = "core" | "warn" | "fault";

export type KpiCell = {
  key: "netLiq" | "todayPnl" | "buyingPower" | "marginUsed";
  label: string;
  value: number | null;
  display: string;
  meta: string | null;
  tone: KpiTone;
  /** 0–100 meter fill; null hides the bar. */
  barPct: number | null;
  barTone: KpiBarTone | null;
};

/** Full-digit USD for the KPI strip — the operator reads exact account figures
 *  here, so no millions abbreviation. Typographic minus, placeholder for null. */
export function fmtMoneyExact(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const sign = value < 0 ? "−" : "";
  return `${sign}$${Math.abs(value).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

function pnlTone(value: number | null): KpiTone {
  if (value == null || value === 0) return "neutral";
  return value > 0 ? "core" : "fault";
}

function clampPct(value: number): number {
  return Math.max(0, Math.min(100, value));
}

const EMPTY_CELL = { value: null, display: "—", meta: null, tone: "neutral", barPct: null, barTone: null } as const;

export function deriveKpis(portfolio: PortfolioData | null, realizedPnl = 0, now: Date = new Date()): KpiCell[] {
  const acct = portfolio?.account_summary;

  const netLiq = acct?.net_liquidation ?? null;
  // IB's streamed dailyPnL only describes a session on a trading day; fall
  // back to realized fills otherwise (zero on a weekend, so the cell blanks).
  // R-107: the snapshot's own date decides, not the wall clock.
  const ibDaily = currentIbDailyPnl(acct?.daily_pnl, now, portfolio?.last_sync);
  const todayPnl = ibDaily ?? (realizedPnl !== 0 ? realizedPnl : null);
  const buyingPower = acct?.buying_power ?? null;

  const availableFunds = acct?.available_funds ?? null;
  const equityWithLoan = acct?.equity_with_loan ?? null;
  const capacityPct =
    availableFunds != null && equityWithLoan != null && equityWithLoan > 0
      ? clampPct((availableFunds / equityWithLoan) * 100)
      : null;

  const maintMargin = acct?.maintenance_margin ?? null;
  const marginUsedPct =
    maintMargin != null && netLiq != null && netLiq > 0
      ? (maintMargin / netLiq) * 100
      : null;
  const marginLevel = assessMargin(acct ?? null).level;

  return [
    {
      key: "netLiq",
      label: "Net Liquidation",
      ...(netLiq == null
        ? EMPTY_CELL
        : {
            value: netLiq,
            display: fmtMoneyExact(netLiq),
            meta: null,
            tone: "neutral" as const,
            barPct: null,
            barTone: null,
          }),
    },
    {
      key: "todayPnl",
      label: "Today P&L",
      ...(todayPnl == null
        ? EMPTY_CELL
        : {
            value: todayPnl,
            display: fmtMoneySigned(todayPnl),
            meta: netLiq != null && netLiq > 0 ? `${((todayPnl / netLiq) * 100).toFixed(2)}%` : null,
            tone: pnlTone(todayPnl),
            barPct: null,
            barTone: null,
          }),
    },
    {
      key: "buyingPower",
      label: "Buying Power",
      ...(buyingPower == null
        ? EMPTY_CELL
        : {
            value: buyingPower,
            display: fmtMoneyExact(buyingPower),
            meta: availableFunds != null ? `AVAIL ${fmtMoneyExact(availableFunds)}` : null,
            tone: "neutral" as const,
            barPct: capacityPct,
            barTone: capacityPct != null ? ("core" as const) : null,
          }),
    },
    {
      key: "marginUsed",
      label: "Margin Used",
      ...(marginUsedPct == null
        ? EMPTY_CELL
        : {
            value: marginUsedPct,
            display: `${marginUsedPct.toFixed(1)}%`,
            meta: maintMargin != null ? `MAINT ${fmtMoneyExact(maintMargin)}` : null,
            tone: "neutral" as const,
            barPct: clampPct(marginUsedPct),
            barTone: marginLevel === "none" ? ("warn" as const) : ("fault" as const),
          }),
    },
  ];
}
