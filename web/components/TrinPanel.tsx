"use client";

import { useEffect, useMemo, useState } from "react";
import { Activity } from "lucide-react";
import BrushMinimap from "./BrushMinimap";
import CriHistoryChart, { type ChartSeries } from "./CriHistoryChart";
import HistoryRangeChips from "./HistoryRangeChips";
import InfoTooltip from "./InfoTooltip";
import MetricCell from "./mobile/MetricCell";
import SectionEmptyState from "./SectionEmptyState";
import SpectralLoader from "./SpectralLoader";
import { RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import { chartSeriesColor } from "@/lib/chartSystem";
import { formatSessionDate } from "@/lib/creditSpread";
import {
  defaultPresetForLength,
  presetRange,
  presetSessions,
  type RangePresetSlug,
} from "@/lib/historyRange";
import { ZONE_HIGH, ZONE_LOW, formatTrin, stateLabel, stateTone, type TrinState } from "@/lib/trin";
import { useTrin } from "@/lib/useTrin";
import { useViewport } from "@/lib/useViewport";

const INFO_TOOLTIP =
  "Arms Index on 60-minute bars with a 10-period average. When the average drops into the low zone (0.60) the market has often weakened or consolidated soon after. Above 1.50 is the opposite extreme.";

const SESSION_TZ = "America/New_York";

interface TrinChartRow {
  date: string;
  trin: number | null;
  ma10: number | null;
}

const TONE_COLOR = {
  positive: "var(--positive)",
  negative: "var(--negative)",
  muted: "var(--text-muted)",
} as const;

const TONE_MOBILE = { positive: "pos", negative: "neg", muted: "mut" } as const;

const ZONE_TEXT = `ZONE ${formatTrin(ZONE_LOW)} / ${formatTrin(ZONE_HIGH)}`;

function stateColor(state: TrinState | null | undefined): string {
  return TONE_COLOR[stateTone(state)];
}

function finiteOrNull(v: number | null | undefined): number | null {
  return v != null && Number.isFinite(v) ? v : null;
}

function formatCount(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return Math.round(v).toLocaleString("en-US", { useGrouping: false });
}

function formatCompactVolume(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(v);
}

function formatSessionTime(raw: string | null | undefined): string {
  if (!raw) return "---";
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return "---";
  return `${d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: SESSION_TZ })} ET`;
}

function formatHourlyTick(d: Date): string {
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: SESSION_TZ,
  });
}

export default function TrinPanel() {
  const { data, loading, syncing, lastSync } = useTrin();
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;

  const [preset, setPreset] = useState<RangePresetSlug | "custom" | null>(null);
  const [customRange, setCustomRange] = useState<[number, number] | null>(null);

  const hourly = data?.hourly ?? [];
  const total = hourly.length;
  const activeRange: RangePresetSlug | "custom" = preset ?? defaultPresetForLength(total);

  useEffect(() => {
    if (preset === null || preset === "custom" || preset === "all") return;
    if (presetSessions(preset) > total) setPreset("all");
  }, [preset, total]);

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
    return <SpectralLoader label="Loading TRIN series" />;
  }

  if (!data || data.missing || !data.current) {
    return (
      <SectionEmptyState
        icon={Activity}
        headline="No TRIN samples yet"
        secondary="Waiting for the radon-trin sampler"
      />
    );
  }

  const current = data.current;
  const rows: TrinChartRow[] = hourly.map((b) => ({
    date: b.ts,
    trin: finiteOrNull(b.trin),
    ma10: finiteOrNull(b.ma10),
  }));
  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);

  const chartSeries: [ChartSeries<TrinChartRow>, ChartSeries<TrinChartRow>] = [
    {
      key: "trin",
      label: "TRIN",
      color: chartSeriesColor("fault"),
      axis: "left",
      scaleType: "linear",
      format: formatTrin,
    },
    {
      key: "ma10",
      label: "MA10",
      color: chartSeriesColor("primary"),
      axis: "right",
      scaleType: "linear",
      format: formatTrin,
    },
  ];

  const sourceText = (data.source ?? "---").toUpperCase();
  const advDecText = `${formatCount(current.adv)} / ${formatCount(current.dec)}`;
  const volumeText = `${formatCompactVolume(current.up_vol)} / ${formatCompactVolume(current.down_vol)} VOL`;

  return (
    <>
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Activity size={14} />
            TRIN
            <InfoTooltip text={INFO_TOOLTIP} />
          </div>
          {lastSync && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "9px", color: "var(--text-muted)" }}>
              {new Date(lastSync).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}
            </span>
          )}
        </div>

        {compact ? (
          <div className="m-regime-grid2x2" data-testid="trin-mobile-grid">
            <MetricCell label="TRIN" value={formatTrin(current.trin)} />
            <MetricCell label="MA10" value={formatTrin(current.ma10)} />
            <MetricCell
              label="STATE"
              value={stateLabel(current.state)}
              tone={TONE_MOBILE[stateTone(current.state)]}
            />
            <MetricCell label="DAILY CLOSE" value={formatTrin(current.daily_close)} />
            <MetricCell label="ADV/DEC" value={advDecText} />
            <MetricCell label="UP/DOWN VOL" value={volumeText} />
            <MetricCell label="SOURCE" value={sourceText} />
          </div>
        ) : (
          <RegimeStrip>
            <RegimeStripCell
              testId="trin-strip-value"
              label="TRIN"
              value={<span data-testid="trin-value">{formatTrin(current.trin)}</span>}
              sub={<>{formatSessionTime(current.ts)}</>}
            />
            <RegimeStripCell
              testId="trin-strip-ma10"
              label="MA10"
              value={<span data-testid="trin-ma10">{formatTrin(current.ma10)}</span>}
              sub={<>10 x 60 MIN</>}
            />
            <RegimeStripCell
              testId="trin-strip-state"
              label="STATE"
              value={
                <span data-testid="trin-state" style={{ color: stateColor(current.state) }}>
                  {stateLabel(current.state)}
                </span>
              }
              sub={<>{ZONE_TEXT}</>}
            />
            <RegimeStripCell
              testId="trin-strip-daily"
              label="DAILY CLOSE"
              value={<span data-testid="trin-daily">{formatTrin(current.daily_close)}</span>}
              sub={<>{formatSessionDate(current.daily_date)}</>}
            />
            <RegimeStripCell
              testId="trin-strip-advdec"
              label="ADV/DEC"
              value={<span data-testid="trin-advdec">{advDecText}</span>}
              sub={<span data-testid="trin-source">{volumeText} · {sourceText}</span>}
            />
          </RegimeStrip>
        )}
      </div>

      <div className="breadth-history-block" data-testid="trin-chart-section">
        {total === 0 && (
          <div className="chart-section-note" data-testid="trin-no-bars">
            No 60-minute bars yet. The sampler records TRIN during NYSE regular hours; the first bar lands after the next open.
          </div>
        )}
        <HistoryRangeChips
          active={activeRange}
          onChange={(next) => {
            setCustomRange(null);
            setPreset(next);
          }}
          maxSessions={total}
          ariaLabel="TRIN 60 minute chart range"
          dataTestId="trin-range-chips"
        />
        <CriHistoryChart
          history={slice}
          series={chartSeries}
          title="TRIN 60 MIN"
          xTickFormat={formatHourlyTick}
          sharedAxis
          referenceLevels={[
            { value: ZONE_LOW, label: `LOW ZONE ${formatTrin(ZONE_LOW)}`, color: "var(--negative)" },
            { value: ZONE_HIGH, label: `HIGH ZONE ${formatTrin(ZONE_HIGH)}`, color: "var(--positive)" },
          ]}
        />
        {total >= 2 && (
          <BrushMinimap
            values={hourly.map((b) => finiteOrNull(b.trin) ?? 0)}
            range={chartRange}
            onRangeChange={(r) => setCustomRange(r)}
            onCustom={() => setPreset("custom")}
            testIdPrefix="trin-brush"
            ariaLabel="TRIN 60 minute history range brush"
          />
        )}
      </div>
    </>
  );
}
