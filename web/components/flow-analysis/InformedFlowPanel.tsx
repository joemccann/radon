"use client";

import {
  useInformedFlow,
  type CongressTrade,
  type InsiderTrade,
} from "@/lib/useInformedFlow";
import { informedFlowBadge, INFORMED_FLOW_BIAS_TOKEN } from "@/lib/informedFlowBadge";

/**
 * InformedFlowPanel — congress + insider activity for a single ticker, ranked
 * most-recent-first, with an institutional-ownership footer. Surfaces the
 * "informed money" context behind a flow signal (a dark-pool accumulation that
 * lines up with insider buying and congress purchases is a stronger edge).
 * Data from `scripts/fetch_informed_flow.py` via `/api/informed-flow/{ticker}`.
 */

const SIDE_LABEL: Record<string, string> = {
  BUY: "BUY",
  SELL: "SELL",
  OTHER: "—",
};

function sideStyle(side: string): React.CSSProperties {
  const token =
    side === "BUY" ? "var(--positive)" : side === "SELL" ? "var(--negative)" : "var(--text-muted)";
  return {
    color: token,
    backgroundColor: `color-mix(in srgb, ${token} 14%, transparent)`,
    borderRadius: 4,
    padding: "1px 6px",
    fontFamily: "var(--font-mono)",
    fontSize: "var(--text-meta)",
    letterSpacing: "0.06em",
  };
}

function biasStyle(bias: ReturnType<typeof informedFlowBadge>["bias"]): React.CSSProperties {
  const token = INFORMED_FLOW_BIAS_TOKEN[bias];
  return {
    color: token,
    backgroundColor: `color-mix(in srgb, ${token} 14%, transparent)`,
    borderRadius: 4,
    padding: "1px 8px",
    fontFamily: "var(--font-mono)",
    fontSize: "var(--text-meta)",
    letterSpacing: "0.06em",
    textTransform: "uppercase",
  };
}

function agoStyle(): React.CSSProperties {
  return { color: "var(--text-muted)", fontFamily: "var(--font-mono)", fontSize: "var(--text-meta)" };
}

function CongressRow({ row }: { row: CongressTrade }) {
  return (
    <li className="snapshot-row">
      <span style={sideStyle(row.side)}>{SIDE_LABEL[row.side] ?? "—"}</span>
      <span className="snapshot-row__signal">{row.person}</span>
      {row.amount ? <span style={agoStyle()}>{row.amount}</span> : null}
      <span style={agoStyle()}>{row.days_ago}d ago</span>
    </li>
  );
}

function InsiderRow({ row }: { row: InsiderTrade }) {
  return (
    <li className="snapshot-row">
      <span style={sideStyle(row.side)}>{SIDE_LABEL[row.side] ?? "—"}</span>
      <span className="snapshot-row__signal">{row.person}</span>
      {row.title ? <span style={agoStyle()}>{row.title}</span> : null}
      {typeof row.shares === "number" ? (
        <span style={agoStyle()}>{row.shares.toLocaleString()} sh</span>
      ) : null}
      <span style={agoStyle()}>{row.days_ago}d ago</span>
    </li>
  );
}

export function InformedFlowPanel({ ticker }: { ticker: string }) {
  const { data, isLoading, error } = useInformedFlow(ticker, true);

  const congress = (data?.congress_trades ?? []).slice(0, 6);
  const insider = (data?.insider_trades ?? []).slice(0, 6);
  const inst = data?.institutional_summary ?? null;
  const badge = informedFlowBadge(data);
  const lastSync = data?.scan_time || null;
  const hasActivity = congress.length > 0 || insider.length > 0;

  return (
    <section className="snapshot-card">
      <header className="snapshot-card__header">
        <p className="panel-eyebrow">Informed Flow</p>
        <h3 className="panel-title">Congress &amp; Insider Activity</h3>
        {badge.count > 0 ? <span style={biasStyle(badge.bias)}>{badge.label}</span> : null}
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
      ) : !hasActivity ? (
        <div className="snapshot-card__empty">No congress or insider activity</div>
      ) : (
        <>
          {congress.length > 0 ? (
            <>
              <p className="panel-eyebrow" style={{ marginTop: 8 }}>
                Congress
              </p>
              <ul className="snapshot-rows">
                {congress.map((r, i) => (
                  <CongressRow key={`cg-${r.person}-${r.date}-${i}`} row={r} />
                ))}
              </ul>
            </>
          ) : null}

          {insider.length > 0 ? (
            <>
              <p className="panel-eyebrow" style={{ marginTop: 8 }}>
                Insider
              </p>
              <ul className="snapshot-rows">
                {insider.map((r, i) => (
                  <InsiderRow key={`in-${r.person}-${r.date}-${i}`} row={r} />
                ))}
              </ul>
            </>
          ) : null}
        </>
      )}

      {inst && (inst.ownership_pct != null || inst.total_institutions != null) ? (
        <footer className="snapshot-card__footer" style={agoStyle()}>
          Institutional ownership{" "}
          {inst.ownership_pct != null ? `${inst.ownership_pct.toFixed(1)}%` : "—"}
          {inst.total_institutions != null
            ? ` across ${inst.total_institutions.toLocaleString()} institutions`
            : ""}
        </footer>
      ) : null}
    </section>
  );
}
