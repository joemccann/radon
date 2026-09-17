"use client";

/** Preserved v1 inference price history. Membership and price direction do not establish scarcity. */

import { useMemo } from "react";
import AiIndustryHistoryChart from "./AiIndustryHistoryChart";
import { historyGroups, type AiHistoryPoint } from "@/lib/aiInfrastructure";
import {
  useLlmTokenIndex,
  type LlmTokenIndexRow,
} from "@/lib/useLlmTokenIndex";

/* ─── Helpers ─────────────────────────────────────────── */

function formatIndex(value: number): string {
  return value.toFixed(2);
}

function formatChange(rows: LlmTokenIndexRow[]): {
  pct: number | null;
  label: string;
} {
  if (rows.length < 2) return { pct: null, label: "---" };
  const first = rows[0].index_value;
  const last = rows[rows.length - 1].index_value;
  if (!Number.isFinite(first) || first === 0 || rows.some(row => row.methodology_version !== rows[0].methodology_version)) return { pct: null, label: "---" };
  const pct = ((last - first) / first) * 100;
  const sign = pct >= 0 ? "+" : "";
  return { pct, label: `${sign}${pct.toFixed(1)}% over ${rows.length}d` };
}

export function legacyIndexHistory(rows: LlmTokenIndexRow[]): AiHistoryPoint[][] {
  return historyGroups(rows.map(row => ({
    date: row.date,
    value: row.index_value,
    unit: "index (first observation = 1)",
    series_id: `legacy-inference-price-v${row.methodology_version}`,
    label: `Legacy inference price basket · methodology v${row.methodology_version}`,
    source_id: "radon-llm-token-index",
  })));
}

/* ─── Component ───────────────────────────────────────── */

const HISTORY_DAYS = 180;

export default function LlmTokenIndexCard() {
  const { data, loading, error } = useLlmTokenIndex(HISTORY_DAYS);

  const rows = data?.rows ?? [];
  const histories = useMemo(() => legacyIndexHistory(rows), [rows]);
  const change = useMemo(() => formatChange(rows), [rows]);
  const latest = rows[rows.length - 1] ?? null;
  const directionColor = "var(--text-secondary)";

  return (
    <div className="regime-panel" data-testid="llm-token-index-card">
      <div className="section-header">
        <div className="section-title">
          <span>Legacy inference price basket</span>
        </div>
        {latest && (
          <span
            className="regime-badge"
            style={{
              background: "var(--chart-live-badge-bg)",
              color: "var(--chart-live-badge-text)",
            }}
            data-testid="llm-token-index-latest-badge"
          >
            {formatIndex(latest.index_value)}
          </span>
        )}
      </div>

      <p
        className="regime-description"
        style={{
          color: "var(--text-secondary)",
          fontSize: 13,
          margin: "4px 0 12px",
        }}
      >
        Preserved methodology v1: median model price using a 70% input and 30% output
        token blend, normalized to the first observation. Available membership can change.
        This series measures quoted inference prices, not compute scarcity or actual spend.
      </p>

      {/* Summary row */}
      <div
        className="regime-hero-meta"
        style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "center", marginBottom: 16 }}
      >
        <div>
          <span style={{ color: "var(--text-muted)", fontSize: "var(--text-meta)" }}>LATEST</span>
          <div
            style={{ fontSize: 24, fontWeight: 600, fontFamily: "var(--font-mono)" }}
            data-testid="llm-token-index-latest-value"
          >
            {latest ? formatIndex(latest.index_value) : "---"}
          </div>
        </div>
        <div>
          <span style={{ color: "var(--text-muted)", fontSize: "var(--text-meta)" }}>
            RAW (USD / Mtok)
          </span>
          <div
            style={{ fontSize: 24, fontWeight: 600, fontFamily: "var(--font-mono)" }}
          >
            {latest ? `$${latest.raw_avg_usd.toFixed(2)}` : "---"}
          </div>
        </div>
        <div>
          <span style={{ color: "var(--text-muted)", fontSize: "var(--text-meta)" }}>WINDOW</span>
          <div
            style={{
              fontSize: 24,
              fontWeight: 600,
              color: directionColor,
              fontFamily: "var(--font-mono)",
            }}
            data-testid="llm-token-index-window-change"
          >
            {change.label}
          </div>
        </div>
      </div>

      {/* Chart */}
      {loading && !data && (
        <div className="regime-empty" data-testid="llm-token-index-loading">
          Loading...
        </div>
      )}

      {error && !data && (
        <div className="regime-empty" data-testid="llm-token-index-error">
          Unable to load LLM Token Index: {error}
        </div>
      )}

      {data && rows.length === 0 && (
        <div className="regime-empty" data-testid="llm-token-index-empty">
          No LLM Token Index data yet. The daily timer publishes one row
          per UTC day at 06:30 UTC.
        </div>
      )}

      {histories.length > 0 && (
        <div data-testid="llm-token-index-chart">
          {histories.map(points => <AiIndustryHistoryChart
            key={points[0].series_id}
            points={points}
            cadence="daily"
            sourceLabel="Radon legacy token index"
          />)}
          <p style={{ color: "var(--text-muted)", fontSize: 12 }}>Normalized index only. Methodology versions remain separate; raw USD per million tokens are not plotted on this axis.</p>
        </div>
      )}
    </div>
  );
}
