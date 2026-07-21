"use client";

import { useEffect, useMemo, useState } from "react";

import {
  classifyRatioRegime,
  zipRvRatioEntries,
  type RvRatioStats,
} from "@/lib/rvRatio";
import { useRvRatio } from "@/lib/useRvRatio";
import {
  defaultPresetForLength,
  presetRange,
  type RangePresetSlug,
} from "@/lib/historyRange";

import BrushMinimap from "./BrushMinimap";
import HistoryRangeChips from "./HistoryRangeChips";
import RvRatioChart from "./RvRatioChart";
import SpectralLoader from "./SpectralLoader";
import styles from "./RvRatioPanel.module.css";

interface RvRatioPanelProps {
  symbol: string;
}

const CHART_FOOTNOTE =
  "Band = full-history avg +/- 1 sd. Log returns, ddof 1, annualized sqrt(252). Sources: IB / UW / Yahoo (long history).";
const STATS_FOOTNOTE = "Stats span the full loaded history, not the visible range.";
const INSUFFICIENT_COPY =
  "252-session realized vol needs at least 253 aligned closes on both legs.";

function formatRatio(value: number): string {
  return `${value.toFixed(2)}x`;
}

interface StatTile {
  id: string;
  label: string;
  value: string;
  tone?: string;
}

function buildStatTiles(stats: RvRatioStats): StatTile[] {
  return [
    { id: "high", label: "HIGH", value: formatRatio(stats.high) },
    { id: "low", label: "LOW", value: formatRatio(stats.low) },
    { id: "avg", label: "AVG", value: formatRatio(stats.avg) },
    { id: "last", label: "LAST", value: formatRatio(stats.last), tone: classifyRatioRegime(stats).tone },
    { id: "stddev", label: "STDDEV", value: stats.stddev.toFixed(2) },
    { id: "pctl", label: "PCTL", value: `${Math.round(stats.last_percentile * 100)}%` },
  ];
}

function MeasurementState({
  kind,
  label,
  message,
  onRetry,
}: {
  kind: "error" | "empty";
  label: string;
  message: string;
  onRetry?: () => void;
}) {
  return (
    <section className={styles.panel} data-testid="rv-ratio-panel" aria-label="Relative vol ratio">
      <div className={styles.state} role={kind === "error" ? "alert" : "status"}>
        <span className={styles.stateLabel}>{label}</span>
        <p>{message}</p>
        {onRetry ? (
          <button type="button" className={styles.retry} onClick={onRetry}>Retry measurement</button>
        ) : null}
      </div>
    </section>
  );
}

export default function RvRatioPanel({ symbol }: RvRatioPanelProps) {
  const normalizedSymbol = symbol.trim().toUpperCase();
  const { data, loading, scanning, error, refresh } = useRvRatio(normalizedSymbol);
  const [preset, setPreset] = useState<RangePresetSlug | "custom" | null>(null);
  const [customRange, setCustomRange] = useState<[number, number] | null>(null);

  useEffect(() => {
    setPreset(null);
    setCustomRange(null);
  }, [normalizedSymbol]);

  const entries = useMemo(() => (data ? zipRvRatioEntries(data) : []), [data]);
  const total = entries.length;
  const activePreset: RangePresetSlug | "custom" = preset ?? defaultPresetForLength(total);

  const chartRange = useMemo<[number, number]>(() => {
    if (total === 0) return [0, 0];
    if (activePreset === "custom" && customRange) {
      const max = total - 1;
      const end = Math.min(customRange[1], max);
      const start = Math.max(0, Math.min(customRange[0], end));
      return [start, end];
    }
    return presetRange(activePreset === "custom" ? "all" : activePreset, total);
  }, [activePreset, customRange, total]);

  if ((loading || scanning) && !data) {
    const label = scanning
      ? "First measurement. Fetching 12 years of closes"
      : "Sampling 252-session realized vol";
    return (
      <section className={styles.panel} data-testid="rv-ratio-panel" aria-label="Relative vol ratio">
        <div className={styles.state}>
          <SpectralLoader label={label} />
        </div>
      </section>
    );
  }
  if (error && !data) {
    return (
      <MeasurementState
        kind="error"
        label="MEASUREMENT FAULT"
        message={error}
        onRetry={() => void refresh()}
      />
    );
  }
  if (!data) {
    return (
      <MeasurementState kind="empty" label="INSUFFICIENT HISTORY" message={INSUFFICIENT_COPY} />
    );
  }

  const badge = classifyRatioRegime(data.stats);
  const tiles = buildStatTiles(data.stats);
  const [start, end] = chartRange;
  const slice = entries.slice(start, end + 1);

  return (
    <section
      className={styles.panel}
      data-testid="rv-ratio-panel"
      aria-labelledby="rv-ratio-heading"
    >
      <header className={styles.header}>
        <div>
          <div className={styles.eyebrow}>OPTIONS / RELATIVE VOL</div>
          <div className={styles.titleLine}>
            <h2 id="rv-ratio-heading">Realized Vol Ratio</h2>
            <span className={styles.symbol}>{`${data.symbol} / ${data.benchmark}`}</span>
            <span
              className={styles.badge}
              data-testid="rv-ratio-regime-badge"
              data-tone={badge.tone}
            >
              {badge.label}
            </span>
            {!data.coverage.complete ? <span className={styles.partial}>PARTIAL</span> : null}
          </div>
        </div>
        <div className={styles.telemetry} data-testid="rv-ratio-telemetry">
          <span>{`AS OF ${data.coverage.last_date} CLOSE`}</span>
          <span>{`${data.window_sessions}-SESSION RV`}</span>
          <span>{`${data.coverage.sessions.toLocaleString("en-US")} SESSIONS`}</span>
          <span>{`BENCHMARK ${data.benchmark}`}</span>
        </div>
      </header>

      <div className={styles.stats} data-testid="rv-ratio-stats">
        {tiles.map((tile) => (
          <div
            key={tile.id}
            className={styles.statTile}
            data-testid={`rv-ratio-stat-${tile.id}`}
            data-tone={tile.tone}
          >
            <span className={styles.statLabel}>{tile.label}</span>
            <span className={styles.statValue}>{tile.value}</span>
          </div>
        ))}
      </div>
      <div className={styles.statsFootnote}>{STATS_FOOTNOTE}</div>

      <div className={styles.chartBlock}>
        <HistoryRangeChips
          active={activePreset}
          onChange={(slug) => {
            setCustomRange(null);
            setPreset(slug);
          }}
          maxSessions={total}
          ariaLabel="RV ratio chart range"
          dataTestId="rv-ratio-range-chips"
        />
        <RvRatioChart
          entries={slice}
          stats={data.stats}
          symbol={data.symbol}
          benchmark={data.benchmark}
        />
        {total >= 2 ? (
          <BrushMinimap
            values={data.ratio}
            range={chartRange}
            onRangeChange={(range) => setCustomRange(range)}
            onCustom={() => setPreset("custom")}
            testIdPrefix="rv-ratio-brush"
            ariaLabel="RV ratio history brush"
          />
        ) : null}
        <div className={styles.footnote}>{CHART_FOOTNOTE}</div>
      </div>
    </section>
  );
}
