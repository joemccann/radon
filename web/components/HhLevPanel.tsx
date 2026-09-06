"use client";

import { useMemo, useState } from "react";
import { Home } from "lucide-react";
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
  formatLeveragePct,
  formatQuarter,
  formatTrillions,
  hhLevRegimeColor,
  hhLevRegimeLabel,
} from "@/lib/hhlev";
import { useHhLev } from "@/lib/useHhLev";
import { useViewport } from "@/lib/useViewport";

const SOURCE_FOOTNOTE = "Source: Board of Governors via FRED";

const INFO_TOOLTIP =
  "Total liabilities of US households and nonprofit organizations as a percent of their net worth, from the Federal Reserve Z.1 Financial Accounts balance sheet (B.101 family). " +
  "Values are end-of-period levels keyed to quarter start dates; the quarterly source publishes with roughly a 10 week lag, and the daily hhlev refresh timer picks up each release. " +
  "Readings under 12 mark a deleveraged household sector with balance sheet capacity; 12 to 16 is moderate, 16 to 20 elevated, and 20 or more stretched, the zone of the 2008-09 credit bust. " +
  SOURCE_FOOTNOTE + ".";

interface HhLevChartRow {
  date: string;
  leverage_pct: number | null;
  spacer: null;
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

function formatYearTick(d: Date): string {
  return String(d.getUTCFullYear());
}

function formatClockTime(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

export default function HhLevPanel() {
  const { data, loading, syncing } = useHhLev();
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;

  // 80-year quarterly series — default to the full history so the secular
  // leverage cycle reads at a glance.
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
    return <SpectralLoader label="Loading household leverage series" />;
  }

  if (!data || data.missing || !data.current) {
    return (
      <SectionEmptyState
        icon={Home}
        headline="No household leverage data yet"
        secondary="The hhlev timer populates this tab from the Federal Reserve Z.1 household balance sheet series via FRED. Data appears after the first successful pull."
      />
    );
  }

  const current = data.current;
  const regime = hhLevRegimeLabel(current.leverage_pct);
  const regimeColor = hhLevRegimeColor(regime);
  const clock = formatClockTime(data.scan_time);
  const updatedText = formatQuarter(data.data_date);

  const rows: HhLevChartRow[] = series.map((p) => ({
    date: p.date,
    leverage_pct: finiteOrNull(p.leverage_pct),
    spacer: null,
  }));
  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);

  const chartSeries: [ChartSeries<HhLevChartRow>, ChartSeries<HhLevChartRow>] = [
    {
      key: "spacer",
      label: "",
      color: "transparent",
      axis: "left",
      format: () => "",
    },
    {
      key: "leverage_pct",
      label: "LEVERAGE PCT",
      color: chartSeriesColor("primary"),
      axis: "right",
      scaleType: "linear",
      format: (v: number) => `${v.toFixed(2)}%`,
    },
  ];

  return (
    <>
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Home size={14} />
            Household Leverage
            <InfoTooltip text={INFO_TOOLTIP} />
          </div>
          {clock && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-meta)", color: "var(--text-muted)" }}>
              {clock}
            </span>
          )}
        </div>

        {compact ? (
          <div className="m-regime-grid2x2" data-testid="hhlev-mobile-grid">
            <MetricCell label="LEVERAGE" value={formatLeveragePct(current.leverage_pct)} />
            <MetricCell label="LIABILITIES" value={formatTrillions(current.liabilities_musd)} />
            <MetricCell label="NET WORTH" value={formatTrillions(current.net_worth_musd)} />
            <MetricCell label="REGIME" value={regime} tone={regimeToneMobile(regimeColor)} />
            <MetricCell label="SOURCE UPDATED" value={updatedText} />
          </div>
        ) : (
          <RegimeStrip>
            <RegimeStripCell
              testId="hhlev-strip-leverage"
              label="LEVERAGE"
              value={<span data-testid="hhlev-leverage">{formatLeveragePct(current.leverage_pct)}</span>}
              sub={<>LIABILITIES PCT OF NET WORTH</>}
            />
            <RegimeStripCell
              testId="hhlev-strip-liab"
              label="LIABILITIES"
              value={<span data-testid="hhlev-liab">{formatTrillions(current.liabilities_musd)}</span>}
              sub={<>HOUSEHOLD TOTAL LIABILITIES</>}
            />
            <RegimeStripCell
              testId="hhlev-strip-networth"
              label="NET WORTH"
              value={<span data-testid="hhlev-networth">{formatTrillions(current.net_worth_musd)}</span>}
              sub={<>HOUSEHOLD NET WORTH LEVEL</>}
            />
            <RegimeStripCell
              testId="hhlev-strip-regime"
              label="REGIME"
              value={
                <span data-testid="hhlev-regime" style={{ color: regimeColor }}>
                  {regime}
                </span>
              }
              sub={<>BANDS 12 / 16 / 20</>}
            />
            <RegimeStripCell
              testId="hhlev-strip-updated"
              label="SOURCE UPDATED"
              value={<span data-testid="hhlev-updated">{updatedText}</span>}
              sub={<>FED Z.1 B.101 VIA FRED</>}
            />
          </RegimeStrip>
        )}
      </div>

      <div className="breadth-history-block" data-testid="hhlev-chart-section">
        <HistoryRangeChips
          active={activeRange}
          onChange={(next) => {
            setCustomRange(null);
            setActiveRange(next);
          }}
          maxSessions={total}
          ariaLabel="Household leverage chart range"
          dataTestId="hhlev-range-chips"
        />
        <CriHistoryChart
          history={slice}
          series={chartSeries}
          title="US HOUSEHOLD LEVERAGE PCT OF NET WORTH"
          xTickFormat={formatYearTick}
        />
        {total >= 2 && (
          <BrushMinimap
            values={series.map((p) => finiteOrNull(p.leverage_pct) ?? 0)}
            range={chartRange}
            onRangeChange={(r) => setCustomRange(r)}
            onCustom={() => setActiveRange("custom")}
            testIdPrefix="hhlev-brush"
            ariaLabel="Household leverage history range brush"
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
