"use client";

/**
 * LLM Compute Premium card — Regime tab.
 *
 * Renders the Radon LLM Token Expenditure Index as a line chart matching
 * the CRI/VCG visual treatment. The index is normalised to 1.0 on the
 * first persisted day so the chart reads like Silicon Data's compute-cost
 * series (1.0 base → climbs/falls thereafter as the basket of frontier
 * model prices moves).
 *
 * Data source: GET /api/llm-token-index → /llm-token-index FastAPI route
 *              → Turso llm_token_index table → daily systemd timer
 *              → Artificial Analysis API.
 *
 * Brand tokens only — no raw hex, 4px max border-radius (CLAUDE.md).
 */

import { useMemo } from "react";
import CriHistoryChart, { type ChartSeries } from "./CriHistoryChart";
import { chartSeriesColor } from "@/lib/chartSystem";
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
  if (!Number.isFinite(first) || first === 0) return { pct: null, label: "---" };
  const pct = ((last - first) / first) * 100;
  const sign = pct >= 0 ? "+" : "";
  return { pct, label: `${sign}${pct.toFixed(1)}% over ${rows.length}d` };
}

/* ─── Component ───────────────────────────────────────── */

const HISTORY_DAYS = 180;

const SERIES: [ChartSeries<LlmTokenIndexRow>, ChartSeries<LlmTokenIndexRow>] = [
  {
    key: "index_value",
    label: "Index",
    color: chartSeriesColor("primary"),
    axis: "left",
    format: (v: number) => v.toFixed(2),
  },
  {
    key: "raw_avg_usd",
    label: "USD / Mtok",
    color: chartSeriesColor("extreme"),
    axis: "right",
    format: (v: number) => `$${v.toFixed(2)}`,
  },
];

export default function LlmTokenIndexCard() {
  const { data, loading, error } = useLlmTokenIndex(HISTORY_DAYS);

  const rows = data?.rows ?? [];
  const change = useMemo(() => formatChange(rows), [rows]);
  const latest = rows[rows.length - 1] ?? null;
  const direction =
    change.pct == null
      ? "neutral"
      : change.pct > 0
        ? "negative" // rising compute cost reads as risk-on / supply constraint
        : "positive";
  const directionColor =
    direction === "positive"
      ? "var(--positive)"
      : direction === "negative"
        ? "var(--negative)"
        : "var(--text-secondary)";

  return (
    <div className="regime-panel" data-testid="llm-token-index-card">
      <div className="section-header">
        <div className="section-title">
          <span>LLM COMPUTE PREMIUM</span>
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
        Weighted median price per million tokens across a basket of frontier
        models (Claude, GPT-4o, Gemini 2.5 Pro, DeepSeek V3, Llama 405B,
        Mistral Large). Normalised to 1.0 at the series base date. Rising
        index means inference is getting more expensive.
      </p>

      {/* Summary row */}
      <div
        className="regime-hero-meta"
        style={{ display: "flex", gap: 16, alignItems: "center", marginBottom: 16 }}
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

      {rows.length >= 2 && (
        <div data-testid="llm-token-index-chart">
          <CriHistoryChart<LlmTokenIndexRow>
            history={rows}
            series={SERIES}
            title="LLM Compute Premium (180d)"
          />
        </div>
      )}
    </div>
  );
}
