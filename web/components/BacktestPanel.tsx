"use client";

import { useEffect, useMemo, useState } from "react";
import SignalAreaChart, { type SignalAreaPoint } from "./SignalAreaChart";
import HistoryRangeChips from "./HistoryRangeChips";
import BrushMinimap from "./BrushMinimap";
import SpectralLoader from "./SpectralLoader";
import {
  defaultPresetForLength,
  presetRange,
  type RangePresetSlug,
} from "@/lib/historyRange";

/* ─── Types (mirror scripts/backtest result JSON) ─────── */

interface BacktestTrade {
  date: string;
  position: number;
  forward_return: number;
  gross_return: number;
  net_return: number;
}

interface BacktestMetrics {
  n_trades: number;
  sharpe: number;
  sortino: number;
  calmar: number;
  max_drawdown: number;
  hit_rate: number;
  expectancy: number;
  annualized_return: number;
  equity_curve: number[];
}

interface BacktestResult {
  strategy?: string;
  label?: string;
  status?: string;
  horizon?: number;
  wired?: boolean;
  entry_doc?: string;
  trades?: BacktestTrade[];
  metrics?: BacktestMetrics | null;
}

/* ─── Helpers ─────────────────────────────────────────── */

function fmtRatio(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(2);
}

function fmtPct(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
}

/** Equity points keyed by each trade's date; index space if dates are sparse. */
function buildEquityPoints(result: BacktestResult): SignalAreaPoint[] {
  const curve = result.metrics?.equity_curve ?? [];
  const trades = result.trades ?? [];
  return curve.map((value, i) => ({
    date: trades[i]?.date ?? String(i + 1),
    value,
  }));
}

/* ─── Metric Card ─────────────────────────────────────── */

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="backtest-metric-card">
      <div className="backtest-metric-label">{label}</div>
      <div className="backtest-metric-value">{value}</div>
    </div>
  );
}

/* ─── Panel ───────────────────────────────────────────── */

const STRATEGY = "cri";

export default function BacktestPanel() {
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [range, setRange] = useState<[number, number]>([0, 0]);
  const [activePreset, setActivePreset] = useState<RangePresetSlug | "custom">("all");

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetch(`/api/backtest/${STRATEGY}`, { cache: "no-store" })
      .then((r) => r.json())
      .then((data: BacktestResult) => {
        if (!alive) return;
        setResult(data);
      })
      .catch(() => {
        if (alive) setResult({ status: "unavailable" });
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  const equityPoints = useMemo(
    () => (result ? buildEquityPoints(result) : []),
    [result],
  );

  const equityValues = useMemo(
    () => equityPoints.map((p) => p.value ?? 1),
    [equityPoints],
  );

  useEffect(() => {
    if (equityPoints.length === 0) return;
    const preset = defaultPresetForLength(equityPoints.length);
    setActivePreset(preset);
    setRange(presetRange(preset, equityPoints.length));
  }, [equityPoints.length]);

  const visiblePoints = useMemo(() => {
    if (equityPoints.length === 0) return [];
    const [start, end] = range;
    return equityPoints.slice(start, end + 1);
  }, [equityPoints, range]);

  if (loading) {
    return <SpectralLoader label="Loading strategy backtest" />;
  }

  const metrics = result?.metrics ?? null;
  const notWired = result?.status === "not_wired";
  const unavailable = result?.status === "unavailable" || result?.status === "unknown_strategy";

  return (
    <div className="backtest-panel">
      <div className="backtest-header">
        <h2 className="backtest-title">Strategy Backtest: {result?.label ?? "Crash Risk Index (CRI)"}</h2>
        {result?.entry_doc && <p className="backtest-entry-doc">{result.entry_doc}</p>}
      </div>

      {notWired && (
        <div className="backtest-empty">
          This strategy is registered but not yet wired for backtesting.
        </div>
      )}

      {unavailable && (
        <div className="backtest-empty">
          Backtest service is unavailable. Try again shortly.
        </div>
      )}

      {metrics && (
        <>
          <div className="backtest-metric-grid">
            <MetricCard label="Sharpe" value={fmtRatio(metrics.sharpe)} />
            <MetricCard label="Sortino" value={fmtRatio(metrics.sortino)} />
            <MetricCard label="Calmar" value={fmtRatio(metrics.calmar)} />
            <MetricCard label="Max Drawdown" value={fmtPct(metrics.max_drawdown)} />
            <MetricCard label="Hit Rate" value={fmtPct(metrics.hit_rate)} />
            <MetricCard label="Expectancy" value={fmtPct(metrics.expectancy)} />
            <MetricCard label="Ann. Return" value={fmtPct(metrics.annualized_return)} />
            <MetricCard label="Trades" value={String(metrics.n_trades)} />
          </div>

          {metrics.n_trades === 0 ? (
            <div className="backtest-empty">
              No entry signals fired over the available history. The documented
              entry rule never triggered. This is a valid result, not an error.
            </div>
          ) : (
            <div className="backtest-chart">
              <HistoryRangeChips
                active={activePreset}
                onChange={(slug) => {
                  setActivePreset(slug);
                  setRange(presetRange(slug, equityPoints.length));
                }}
                maxSessions={equityPoints.length}
              />
              <SignalAreaChart
                data={visiblePoints}
                formatValue={(v) => v.toFixed(2)}
                formatTooltipValue={(v) => v.toFixed(3)}
                height={360}
                dataTestId="backtest-equity-chart"
              />
              <BrushMinimap
                values={equityValues}
                range={range}
                onRangeChange={setRange}
                onCustom={() => setActivePreset("custom")}
                ariaLabel="Backtest equity-curve range brush"
                testIdPrefix="backtest-brush"
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}
