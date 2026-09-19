"use client";

import { useMemo, useState } from "react";
import { Activity } from "lucide-react";
import BrushMinimap from "./BrushMinimap";
import CriHistoryChart, { type ChartSeries, type ReferenceLevel } from "./CriHistoryChart";
import FreshnessRail from "./FreshnessRail";
import HistoryRangeChips from "./HistoryRangeChips";
import InfoTooltip from "./InfoTooltip";
import MetricCell from "./mobile/MetricCell";
import SectionEmptyState from "./SectionEmptyState";
import SpectralLoader from "./SpectralLoader";
import { RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import { PANIC_INDEX_REFRESH } from "@/lib/refreshSchedule";
import { chartSeriesColor } from "@/lib/chartSystem";
import { presetRange, type RangePresetSlug } from "@/lib/historyRange";
import {
  DISCLAIMER,
  INFO_TOOLTIP,
  RANK_MIN_ROWS,
  SOURCE_FOOTNOTE,
  deltaTone,
  formatClose,
  formatDelta,
  formatLevel,
  formatRank,
  formatTs,
  formatZ,
  rankKind,
  type PanicIndexData,
  type PanicIndexPoint,
} from "@/lib/panicIndex";
import { usePanicIndex } from "@/lib/usePanicIndex";
import { useViewport } from "@/lib/useViewport";

/**
 * Panic Proxy regime tab. Equal-weight mean of 252-session z-scores of four
 * Cboe series. Descriptive regime read only: no forward-return claim.
 *
 * Spec: docs/indicators/panic-index.md section H.
 */

const EMPTY_SECONDARY =
  "The four Cboe series have not been joined by the panic-index refresh timer.";

type ViewMode = "change" | "level";

interface PanicIndexChartRow {
  date: string;
  delta_1d: number | null;
  level: number | null;
}

function finiteOrNull(v: number | null | undefined): number | null {
  return v != null && Number.isFinite(v) ? v : null;
}

function toneMobile(color: string): "pos" | "neg" | "warn" | "mut" {
  if (color === "var(--negative)") return "neg";
  if (color === "var(--warning)") return "warn";
  return "mut";
}

function formatDayTick(d: Date): string {
  return d.toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "2-digit",
    timeZone: "UTC",
  });
}

function rankValue(data: PanicIndexData): { text: string; sub: string } {
  const current = data.current;
  if (!current || current.rank_n == null || current.rank_n < RANK_MIN_ROWS) {
    return { text: "---", sub: "most negative 1d change" };
  }
  const kind = rankKind(current.delta_1d);
  if (kind === "surge") {
    return {
      text: formatRank(current.rank_surge_10y, current.rank_n),
      sub: "most positive 1d change",
    };
  }
  return {
    text: formatRank(current.rank_decline_10y, current.rank_n),
    sub: "most negative 1d change",
  };
}

export default function PanicIndexPanel() {
  const { data, loading, syncing } = usePanicIndex();
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;

  const [view, setView] = useState<ViewMode>("change");
  const [activeRange, setActiveRange] = useState<RangePresetSlug | "custom">("all");
  const [customRange, setCustomRange] = useState<[number, number] | null>(null);

  const series = data?.series ?? [];
  const total = series.length;

  const chartRange = useMemo<[number, number]>(() => {
    if (total < 2) return [0, Math.max(total - 1, 0)];
    if (activeRange === "custom" && customRange) {
      const max = total - 1;
      const end = Math.min(customRange[1], max);
      const start = Math.max(0, Math.min(customRange[0], end));
      return [start, end];
    }
    return presetRange(activeRange === "custom" ? "all" : activeRange, total);
  }, [activeRange, customRange, total]);

  if ((loading || syncing) && !data) {
    return <SpectralLoader label="Loading Cboe panic proxy series" />;
  }

  if (!data || data.missing || !data.current) {
    return (
      <SectionEmptyState
        icon={Activity}
        headline="No panic proxy reading yet"
        secondary={EMPTY_SECONDARY}
      />
    );
  }

  const current = data.current;
  const stddev = data.stats?.stddev ?? current.delta_std_10y;
  const tone = deltaTone(current.delta_1d, stddev);
  const sourceSession = data.data_date ?? current.date ?? "---";
  const rank = rankValue(data);
  const overlay = current.legs.skew25d;

  const rows: PanicIndexChartRow[] = series.map((p: PanicIndexPoint) => ({
    date: p.date,
    delta_1d: finiteOrNull(p.delta_1d),
    level: finiteOrNull(p.level),
  }));
  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);

  const seriesKey = view === "change" ? "delta_1d" : "level";
  const chartTitle = view === "change" ? "PANIC PROXY - 1D CHANGE" : "PANIC PROXY - LEVEL";
  const chartSeries: [ChartSeries<PanicIndexChartRow>] = [
    {
      key: seriesKey,
      label: view === "change" ? "1D CHANGE" : "LEVEL",
      color: chartSeriesColor("comparison"),
      axis: "left",
      scaleType: "linear",
      format: (v: number) => v.toFixed(2),
    },
  ];

  const referenceLevels: ReferenceLevel[] | undefined =
    view === "change" && stddev != null && Number.isFinite(stddev)
      ? [
          { value: 3 * stddev, label: "+3σ", color: "var(--text-muted)" },
          { value: -3 * stddev, label: "-3σ", color: "var(--text-muted)" },
        ]
      : undefined;

  return (
    <>
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Activity size={14} />
            <span>
              PANIC PROXY
              <span
                data-testid="panic-index-disclaimer"
                style={{
                  display: "block",
                  fontFamily: "var(--font-sans)",
                  fontSize: "var(--text-meta)",
                  color: "var(--text-muted)",
                  fontWeight: 400,
                  letterSpacing: "0.02em",
                }}
              >
                {DISCLAIMER}
              </span>
            </span>
            <InfoTooltip text={INFO_TOOLTIP} />
          </div>
        </div>

        {compact ? (
          <div className="m-regime-grid2x2" data-testid="panic-index-mobile-grid">
            <MetricCell
              label="1D CHANGE"
              value={formatDelta(current.delta_1d)}
              tone={toneMobile(tone)}
            />
            <MetricCell label="LEVEL" value={formatLevel(current.level)} />
            <MetricCell label="10Y RANK" value={rank.text} />
            <MetricCell label="VIX" value={formatClose(current.legs.vix.value)} />
            <MetricCell label="VVIX" value={formatClose(current.legs.vvix.value)} />
            <MetricCell label="VIX / VIX3M" value={formatTs(current.legs.ts.value)} />
            <MetricCell label="SKEW" value={formatClose(current.legs.skew.value)} />
            <MetricCell label="SOURCE UPDATED" value={sourceSession} />
            {overlay && overlay.value != null ? (
              <MetricCell label="25D SKEW" value={formatClose(overlay.value)} />
            ) : null}
          </div>
        ) : (
          <RegimeStrip>
            <RegimeStripCell
              testId="panic-index-strip-delta"
              label="1D CHANGE"
              value={
                <span data-testid="panic-index-delta" style={{ color: tone }}>
                  {formatDelta(current.delta_1d)}
                </span>
              }
              sub={<>z {formatZ(current.delta_z)} vs 10y</>}
            />
            <RegimeStripCell
              testId="panic-index-strip-level"
              label="LEVEL"
              value={<span data-testid="panic-index-level">{formatLevel(current.level)}</span>}
              sub={<>mean z of 4 legs</>}
            />
            <RegimeStripCell
              testId="panic-index-strip-rank"
              label="10Y RANK"
              value={<span data-testid="panic-index-rank">{rank.text}</span>}
              sub={<>{rank.sub}</>}
            />
            <RegimeStripCell
              testId="panic-index-strip-vix"
              label="VIX"
              value={<span data-testid="panic-index-vix">{formatClose(current.legs.vix.value)}</span>}
              sub={<>z {formatZ(current.legs.vix.z)}</>}
            />
            <RegimeStripCell
              testId="panic-index-strip-vvix"
              label="VVIX"
              value={<span data-testid="panic-index-vvix">{formatClose(current.legs.vvix.value)}</span>}
              sub={<>z {formatZ(current.legs.vvix.z)}</>}
            />
            <RegimeStripCell
              testId="panic-index-strip-ts"
              label="VIX / VIX3M"
              value={<span data-testid="panic-index-ts">{formatTs(current.legs.ts.value)}</span>}
              sub={<>z {formatZ(current.legs.ts.z)}</>}
            />
            <RegimeStripCell
              testId="panic-index-strip-skew"
              label="SKEW"
              value={<span data-testid="panic-index-skew">{formatClose(current.legs.skew.value)}</span>}
              sub={<>z {formatZ(current.legs.skew.z)}</>}
            />
            <RegimeStripCell
              testId="panic-index-strip-source-updated"
              label="SOURCE UPDATED"
              value={<span data-testid="panic-index-source-updated">{sourceSession}</span>}
              sub={<>latest joined session</>}
            />
            {overlay && overlay.value != null ? (
              <RegimeStripCell
                testId="panic-index-strip-skew25d"
                label="25D SKEW"
                value={<span data-testid="panic-index-skew25d">{formatClose(overlay.value)}</span>}
                sub={<>overlay, UW, not in composite</>}
              />
            ) : null}
          </RegimeStrip>
        )}

        <FreshnessRail
          schedule={PANIC_INDEX_REFRESH}
          asOf={data.data_date}
          testId="panic-index-freshness-rail"
          asOfTestId="panic-index-strip-asof"
        />
      </div>

      <div className="breadth-history-block" data-testid="panic-index-chart-section">
        <div
          style={{ display: "flex", gap: "8px", marginBottom: "8px" }}
          data-testid="panic-index-view-chips"
        >
          <button
            type="button"
            data-testid="panic-index-view-change"
            aria-pressed={view === "change"}
            onClick={() => setView("change")}
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "var(--text-meta)",
              color: view === "change" ? "var(--text-primary)" : "var(--text-muted)",
              background: "transparent",
              border: "1px solid var(--border-dim)",
              borderRadius: "4px",
              padding: "2px 8px",
              cursor: "pointer",
            }}
          >
            CHANGE
          </button>
          <button
            type="button"
            data-testid="panic-index-view-level"
            aria-pressed={view === "level"}
            onClick={() => setView("level")}
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "var(--text-meta)",
              color: view === "level" ? "var(--text-primary)" : "var(--text-muted)",
              background: "transparent",
              border: "1px solid var(--border-dim)",
              borderRadius: "4px",
              padding: "2px 8px",
              cursor: "pointer",
            }}
          >
            LEVEL
          </button>
        </div>
        <HistoryRangeChips
          active={activeRange}
          onChange={(next) => {
            setCustomRange(null);
            setActiveRange(next);
          }}
          maxSessions={total}
          ariaLabel="Panic proxy chart range"
          dataTestId="panic-index-range-chips"
        />
        <CriHistoryChart
          history={slice}
          series={chartSeries}
          title={chartTitle}
          xTickFormat={formatDayTick}
          referenceLevels={referenceLevels}
        />
        {total >= 2 && (
          <BrushMinimap
            values={rows.map((p) => (view === "change" ? p.delta_1d : p.level) ?? 0)}
            range={chartRange}
            onRangeChange={(r) => setCustomRange(r)}
            onCustom={() => setActiveRange("custom")}
            testIdPrefix="panic-index-brush"
            ariaLabel="Panic proxy history range brush"
          />
        )}
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "var(--text-meta)",
            color: "var(--text-muted)",
            marginTop: "8px",
          }}
        >
          {SOURCE_FOOTNOTE}
        </div>
      </div>
    </>
  );
}
