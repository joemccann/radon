"use client";

import Link from "next/link";

import { useCatalysts, type CatalystRow } from "@/lib/useCatalysts";
import { catalystBadge, CATALYST_URGENCY_TOKEN } from "@/lib/catalystBadge";

/**
 * CatalystCard — upcoming earnings / FDA / economic events ranked nearest
 * first, with a days-to-catalyst chip. Surfaces the time pressure a flow
 * signal is racing against (an edge that resolves after a known catalyst is
 * worth less). Data from `scripts/fetch_catalysts.py` via `/api/catalysts`.
 */

const TYPE_LABEL: Record<CatalystRow["type"], string> = {
  earnings: "EARN",
  fda: "FDA",
  economic: "ECON",
};

function badgeStyle(daysUntil: number): React.CSSProperties {
  const token = CATALYST_URGENCY_TOKEN[catalystBadge(daysUntil).urgency];
  return {
    color: token,
    backgroundColor: `color-mix(in srgb, ${token} 14%, transparent)`,
    borderRadius: 4,
    padding: "1px 6px",
    fontFamily: "var(--font-mono)",
    fontSize: 10,
    letterSpacing: "0.06em",
    textTransform: "uppercase",
  };
}

function typeStyle(): React.CSSProperties {
  return {
    color: "var(--text-muted)",
    fontFamily: "var(--font-mono)",
    fontSize: 10,
    letterSpacing: "0.1em",
  };
}

export function CatalystCard() {
  const { data, isLoading, error } = useCatalysts(true);

  const rows = (data?.catalysts ?? []).slice(0, 6);
  const lastSync = data?.scan_time || null;

  return (
    <section className="snapshot-card">
      <span className="panel-edge-trace" aria-hidden />
      <header className="snapshot-card__header">
        <p className="panel-eyebrow">Catalysts</p>
        <h3 className="panel-title">Upcoming Catalysts</h3>
        <span className="snapshot-tabs__meta">
          {lastSync
            ? `Updated ${new Date(lastSync).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}`
            : "---"}
        </span>
      </header>

      {isLoading ? (
        <div className="snapshot-card__empty">Loading…</div>
      ) : error ? (
        <div className="snapshot-card__error">{error}</div>
      ) : rows.length === 0 ? (
        <div className="snapshot-card__empty">No upcoming catalysts</div>
      ) : (
        <ul className="snapshot-rows">
          {rows.map((r, i) => (
            <li key={`cat-${r.type}-${r.ticker ?? "mkt"}-${r.date}-${i}`} className="snapshot-row">
              <span style={typeStyle()}>{TYPE_LABEL[r.type]}</span>
              {r.ticker ? (
                <Link href={`/${encodeURIComponent(r.ticker)}`} className="snapshot-row__ticker">
                  {r.ticker}
                </Link>
              ) : (
                <span className="snapshot-row__ticker">MKT</span>
              )}
              <span className="snapshot-row__signal">{r.title}</span>
              <span style={badgeStyle(r.days_until)}>{catalystBadge(r.days_until).label}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
