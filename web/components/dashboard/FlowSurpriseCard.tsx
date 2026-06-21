"use client";

import Link from "next/link";
import { useFlowSurprise, type FlowSurpriseRow } from "@/lib/useFlowSurprise";

/**
 * FlowSurpriseCard — surfaces the watchlist tickers whose latest flow blew
 * past (EXCESS) or fell far below (DEFICIT) what the forecast model expected.
 * The edge is the residual: surprise_pit near 1.0 = EXCESS, near 0.0 =
 * DEFICIT. Top surprises first (ranked by PIT extremity upstream).
 */

const CHIP_TOKEN: Record<FlowSurpriseRow["flag"], string> = {
  EXCESS: "var(--negative)",
  DEFICIT: "var(--positive)",
  NORMAL: "var(--text-muted)",
};

function chipStyle(flag: FlowSurpriseRow["flag"]): React.CSSProperties {
  const token = CHIP_TOKEN[flag];
  return {
    color: token,
    backgroundColor: `color-mix(in srgb, ${token} 14%, transparent)`,
    borderRadius: 4,
    padding: "1px 6px",
    fontFamily: "var(--font-mono)",
    fontSize: 10,
    letterSpacing: "0.1em",
    textTransform: "uppercase",
  };
}

function asPercentile(pit: number): string {
  const clamped = Math.max(0, Math.min(1, pit));
  return `${Math.round(clamped * 100)}`;
}

function formatNum(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "---";
  return value.toFixed(1);
}

export function FlowSurpriseCard() {
  const { data, isLoading, error } = useFlowSurprise(true);

  const rows = (data?.results ?? []).slice(0, 5);
  const lastSync = data?.scan_time || null;

  return (
    <section className="snapshot-card">
      <span className="panel-edge-trace" aria-hidden />
      <header className="snapshot-card__header">
        <p className="panel-eyebrow">Flow Surprise / 05</p>
        <h3 className="panel-title">Flow Surprise</h3>
        <span className="snapshot-tabs__meta">
          {lastSync
            ? `Last sample ${new Date(lastSync).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}`
            : "---"}
        </span>
      </header>

      {isLoading ? (
        <div className="snapshot-card__empty">Sampling…</div>
      ) : error ? (
        <div className="snapshot-card__error">{error}</div>
      ) : rows.length === 0 ? (
        <div className="snapshot-card__empty">No flow surprises yet</div>
      ) : (
        <ul className="snapshot-rows">
          {rows.map((r) => (
            <li key={`fs-${r.ticker}`} className="snapshot-row">
              <Link
                href={`/${encodeURIComponent(r.ticker)}`}
                className="snapshot-row__ticker"
              >
                {r.ticker}
              </Link>
              <span className="snapshot-row__signal">
                {formatNum(r.actual)} vs {formatNum(r.expected_median)}
              </span>
              <span style={chipStyle(r.flag)}>{r.flag}</span>
              <span className="snapshot-row__score">{asPercentile(r.surprise_pit)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
