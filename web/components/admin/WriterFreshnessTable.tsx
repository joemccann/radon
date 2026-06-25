"use client";

import { getMarketStateFromDate, isStale, type MarketState } from "@/lib/serviceHealthWindows";
import { humanizeDetail } from "@/lib/adminFormat";
import type { ServiceHealthRow } from "@/lib/adminTypes";
import { useSort } from "@/lib/useSort";
import SortTh from "../SortTh";

type WriterSortKey = "service" | "state" | "freshness" | "lastRun" | "detail";

/**
 * Per-writer freshness from the Turso service_health table (via /edge-health).
 * Freshness is market-hours aware: a market-hours-only writer quiet overnight is
 * NOT stale. This is a freshness SLI, distinct from liveness.
 */
export default function WriterFreshnessTable({
  rows,
  reachable,
  loading = false,
}: {
  rows: ServiceHealthRow[];
  reachable: boolean;
  loading?: boolean;
}) {
  const market = getMarketStateFromDate();
  const { sorted, sort, toggle } = useSort<ServiceHealthRow, WriterSortKey>(
    rows,
    (row, key) => writerSortValue(row, key, market),
    "service",
    "asc",
  );

  return (
    <section className="admin-card" data-testid="writer-freshness">
      <header className="admin-card-header">
        <span className="admin-card-title">Writer Freshness</span>
      </header>
      <p className="admin-card-subhead">
        Background data writers (service_health). Stale windows are market-hours aware.
      </p>

      {loading ? (
        <div className="admin-table-scroll">
        <table className="admin-services-table">
          <tbody>
            {Array.from({ length: 5 }).map((_, i) => (
              <tr key={i} className="admin-skeleton-row">
                <td><span className="admin-skeleton admin-skeleton-line" style={{ width: 130 }} /></td>
                <td><span className="admin-skeleton admin-skeleton-line" style={{ width: 50 }} /></td>
                <td><span className="admin-skeleton admin-skeleton-line" style={{ width: 60 }} /></td>
                <td><span className="admin-skeleton admin-skeleton-line" style={{ width: 70 }} /></td>
                <td><span className="admin-skeleton admin-skeleton-line" style={{ width: 40 }} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      ) : !reachable ? (
        <p className="admin-card-empty">Edge health unreachable. Writer freshness unavailable.</p>
      ) : rows.length === 0 ? (
        <p className="admin-card-empty">No writer health rows reported.</p>
      ) : (
        <div className="admin-table-scroll">
        <table className="admin-services-table">
          <thead>
            <tr>
              <SortTh<WriterSortKey> label="Writer" sortKey="service" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
              <SortTh<WriterSortKey> label="State" sortKey="state" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
              <SortTh<WriterSortKey> label="Freshness" sortKey="freshness" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
              <SortTh<WriterSortKey> label="Last run" sortKey="lastRun" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
              <SortTh<WriterSortKey> label="Detail" sortKey="detail" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => <WriterRow key={r.service} row={r} />)}
          </tbody>
        </table>
        </div>
      )}
    </section>
  );
}

function writerSortValue(row: ServiceHealthRow, key: WriterSortKey, market: MarketState): string | number | null {
  switch (key) {
    case "service":
      return row.service;
    case "state":
      return row.state;
    case "freshness":
      return isStale(row.service, row.updated_at ?? null, market) ? 1 : 0;
    case "lastRun": {
      const lastRun = row.last_attempt_finished_at ?? row.updated_at ?? null;
      return lastRun ? Date.parse(lastRun) : null;
    }
    case "detail":
      return humanizeDetail(row.last_error);
  }
}

function WriterRow({ row }: { row: ServiceHealthRow }) {
  const market = getMarketStateFromDate();
  const stale = isStale(row.service, row.updated_at ?? null, market);
  const stateTone =
    row.state === "ok" ? "positive" : row.state === "error" ? "negative" : "neutral";
  const lastRun = row.last_attempt_finished_at ?? row.updated_at ?? null;
  return (
    <tr data-testid={`writer-row-${row.service}`}>
      <td className="admin-unit-name">{row.service}</td>
      <td>
        <span className={`admin-pill admin-pill-${stateTone}`}>{row.state}</span>
      </td>
      <td>
        <div className="admin-verdict-cell">
          <span
            className={`admin-status-dot admin-status-dot-${stale ? "warning" : "positive"}`}
            aria-hidden
          />
          {stale ? "STALE" : "fresh"}
        </div>
      </td>
      <td className="admin-unit-activity">{relAge(lastRun)}</td>
      <td className="admin-unit-desc" title={row.last_error || undefined}>
        {humanizeDetail(row.last_error) || "--"}
      </td>
    </tr>
  );
}

function relAge(iso: string | null | undefined): string {
  if (!iso) return "never";
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return "unknown";
  const secs = Math.max(0, Math.floor((Date.now() - ts) / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86_400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86_400)}d ago`;
}
