"use client";

import { useMemo, useState } from "react";
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
import { presetRange, type RangePresetSlug } from "@/lib/historyRange";
import {
  formatSignedThousands,
  formatThousands,
  hyAdRegimeColor,
  hyAdRegimeLabel,
} from "@/lib/hyad";
import { useHyAd } from "@/lib/useHyAd";
import { useViewport } from "@/lib/useViewport";

const SOURCE_FOOTNOTE = "Source: FINRA Fixed Income Market Activity";

const INFO_TOOLTIP =
  "Cumulative advance-decline line for high yield corporate bonds, built from FINRA TRACE end of day breadth with CORP and 144A issues combined. " +
  "The level of a cumulative A-D line is arbitrary: the accumulation start sets an additive constant, so only the slope and divergences against the S&P 500 carry signal. " +
  "The line falling while the S&P 500 rises is a negative divergence that historically precedes risk-off phases; the line confirming new S&P 500 highs supports the advance. " +
  "A new point lands Tuesday through Saturday mornings once FINRA finalizes the prior session. " +
  SOURCE_FOOTNOTE + ".";

interface HyAdChartRow {
  date: string;
  spx: number | null;
  cum: number | null;
}

function finiteOrNull(v: number | null | undefined): number | null {
  return v != null && Number.isFinite(v) ? v : null;
}

function regimeToneMobile(color: string): "pos" | "neg" | "warn" | "mut" {
  if (color === "var(--positive)") return "pos";
  if (color === "var(--negative)") return "neg";
  if (color === "var(--warning)") return "warn";
  return "mut";
}

function formatMonthYearTick(d: Date): string {
  return d.toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
}

function formatClockTime(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

export default function HyAdPanel() {
  const { data, loading, syncing } = useHyAd();
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;

  // Multi-year daily series — default to the full history so divergences
  // against SPX read at a glance.
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
    return <SpectralLoader label="Loading high yield breadth series" />;
  }

  if (!data || data.missing || !data.current) {
    return (
      <SectionEmptyState
        icon={Activity}
        headline="No high yield breadth data yet"
        secondary="The hy-ad timer populates this tab from FINRA TRACE end of day breadth aggregates. Data appears after the first successful pull."
      />
    );
  }

  const current = data.current;
  const regime = hyAdRegimeLabel(current.cum, current.ma21, current.ma50);
  const regimeColor = hyAdRegimeColor(regime);
  const clock = formatClockTime(data.scan_time);
  const updatedText = data.data_date ?? "---";
  const advDecSub = `ADV ${formatThousands(current.advances)} / DEC ${formatThousands(current.declines)}`;

  const rows: HyAdChartRow[] = series.map((p) => ({
    date: p.date,
    // Log axis: skip non-positive SPX so d3.scaleLog never emits NaN paths.
    spx: p.spx_close != null && Number.isFinite(p.spx_close) && p.spx_close > 0 ? p.spx_close : null,
    cum: finiteOrNull(p.cum),
  }));
  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);

  const chartSeries: [ChartSeries<HyAdChartRow>, ChartSeries<HyAdChartRow>] = [
    {
      key: "spx",
      label: "S&P 500",
      color: chartSeriesColor("primary"),
      axis: "left",
      scaleType: "log",
      format: (v: number) => v.toFixed(0),
    },
    {
      key: "cum",
      label: "HY A-D CUM",
      color: chartSeriesColor("fault"),
      axis: "right",
      scaleType: "linear",
      format: (v: number) => formatSignedThousands(v),
    },
  ];

  return (
    <>
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Activity size={14} />
            High Yield Breadth
            <InfoTooltip text={INFO_TOOLTIP} />
          </div>
          {clock && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "9px", color: "var(--text-muted)" }}>
              {clock}
            </span>
          )}
        </div>

        {compact ? (
          <div className="m-regime-grid2x2" data-testid="hyad-mobile-grid">
            <MetricCell label="HY AD CUM" value={formatSignedThousands(current.cum)} />
            <MetricCell label="1D NET" value={formatSignedThousands(current.net)} />
            <MetricCell label="21D MA" value={formatSignedThousands(current.ma21)} />
            <MetricCell label="50D MA" value={formatSignedThousands(current.ma50)} />
            <MetricCell label="REGIME" value={regime} tone={regimeToneMobile(regimeColor)} />
            <MetricCell label="SOURCE UPDATED" value={updatedText} />
          </div>
        ) : (
          <RegimeStrip>
            <RegimeStripCell
              testId="hyad-strip-cum"
              label="HY AD CUM"
              value={<span data-testid="hyad-cum">{formatSignedThousands(current.cum)}</span>}
              sub={<>CUMULATIVE ADV MINUS DEC</>}
            />
            <RegimeStripCell
              testId="hyad-strip-net"
              label="1D NET"
              value={<span data-testid="hyad-net">{formatSignedThousands(current.net)}</span>}
              sub={<>{advDecSub}</>}
            />
            <RegimeStripCell
              testId="hyad-strip-ma"
              label="21D / 50D MA"
              value={
                <>
                  <span data-testid="hyad-ma21">{formatSignedThousands(current.ma21)}</span>
                  {" / "}
                  <span data-testid="hyad-ma50">{formatSignedThousands(current.ma50)}</span>
                </>
              }
              sub={<>21 AND 50 SESSION SMA OF CUM</>}
            />
            <RegimeStripCell
              testId="hyad-strip-regime"
              label="REGIME"
              value={
                <span data-testid="hyad-regime" style={{ color: regimeColor }}>
                  {regime}
                </span>
              }
              sub={<>CUM VS 21D VS 50D</>}
            />
            <RegimeStripCell
              testId="hyad-strip-updated"
              label="SOURCE UPDATED"
              value={<span data-testid="hyad-updated">{updatedText}</span>}
              sub={<>FINRA TRACE END OF DAY</>}
            />
          </RegimeStrip>
        )}
      </div>

      <div className="breadth-history-block" data-testid="hyad-chart-section">
        <HistoryRangeChips
          active={activeRange}
          onChange={(next) => {
            setCustomRange(null);
            setActiveRange(next);
          }}
          maxSessions={total}
          ariaLabel="High yield breadth chart range"
          dataTestId="hyad-range-chips"
        />
        <CriHistoryChart
          history={slice}
          series={chartSeries}
          title="HIGH YIELD BOND CUMULATIVE A-D LINE"
          xTickFormat={formatMonthYearTick}
        />
        {total >= 2 && (
          <BrushMinimap
            values={series.map((p) => finiteOrNull(p.cum) ?? 0)}
            range={chartRange}
            onRangeChange={(r) => setCustomRange(r)}
            onCustom={() => setActiveRange("custom")}
            testIdPrefix="hyad-brush"
            ariaLabel="High yield breadth history range brush"
          />
        )}
        <div
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "9px",
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
