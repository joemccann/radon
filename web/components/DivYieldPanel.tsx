"use client";

import { useMemo, useState } from "react";
import { Percent } from "lucide-react";
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
  divYieldRegimeColor,
  divYieldRegimeLabel,
  formatDivYieldCount,
  formatDivYieldPct,
} from "@/lib/divyield";
import { useDivYield } from "@/lib/useDivYield";
import { useViewport } from "@/lib/useViewport";

const SURVIVORSHIP_CAVEAT =
  "Pre-cutover history is a survivorship-biased approximation: today's constituents projected backward, not point-in-time index membership.";

const INFO_TOOLTIP =
  "Share of current S&P 500 constituents whose trailing 12 month dividend yield exceeds the 10 Year Treasury yield. " +
  "Trailing yield sums all cash dividends over the trailing 12 months, specials included, divided by the last price. " +
  "A stock counts only when its yield is strictly above the 10Y; a tie does not count. " +
  "Low readings mean Treasuries out-yield nearly the whole index; high readings mark yield-abundant regimes. " +
  SURVIVORSHIP_CAVEAT;

interface DivYieldChartRow {
  date: string;
  pct_above: number | null;
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

function formatMonthYearTick(d: Date): string {
  return d.toLocaleDateString("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
}

function formatClockTime(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

export default function DivYieldPanel() {
  const { data, loading, syncing } = useDivYield();
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;

  // Multi-decade monthly series — default to the full history, unlike the
  // session-based regime charts that default to a recent window.
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
    return <SpectralLoader label="Loading dividend yield breadth series" />;
  }

  if (!data || data.missing || !data.current) {
    return (
      <SectionEmptyState
        icon={Percent}
        headline="No dividend yield data yet"
        secondary="The div-yield refresh timer populates this tab from S&P 500 constituent yields and the 10 Year Treasury series. Data appears after the first successful pull."
      />
    );
  }

  const current = data.current;
  const pctAbove = finiteOrNull(current.pct_above);
  const regime = pctAbove != null ? divYieldRegimeLabel(pctAbove) : null;
  const regimeColor = regime ? divYieldRegimeColor(regime) : "var(--text-muted)";
  const clock = formatClockTime(data.scan_time);
  const updatedText = data.data_date ?? "---";

  const rows: DivYieldChartRow[] = series.map((p) => ({
    date: p.date,
    pct_above: finiteOrNull(p.pct_above),
    spacer: null,
  }));
  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);

  const chartSeries: [ChartSeries<DivYieldChartRow>, ChartSeries<DivYieldChartRow>] = [
    {
      key: "spacer",
      label: "",
      color: "transparent",
      axis: "left",
      format: () => "",
    },
    {
      key: "pct_above",
      label: "PCT ABOVE 10Y",
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
            <Percent size={14} />
            Dividend Yield Breadth
            <InfoTooltip text={INFO_TOOLTIP} />
          </div>
          {clock && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "var(--text-meta)", color: "var(--text-muted)" }}>
              {clock}
            </span>
          )}
        </div>

        {compact ? (
          <div className="m-regime-grid2x2" data-testid="divyield-mobile-grid">
            <MetricCell label="ABOVE 10Y" value={formatDivYieldPct(current.pct_above)} />
            <MetricCell label="COUNT" value={formatDivYieldCount(current.count_above, current.total)} />
            <MetricCell label="10Y YIELD" value={formatDivYieldPct(current.y10)} />
            <MetricCell
              label="REGIME"
              value={regime ?? "---"}
              tone={regimeToneMobile(regimeColor)}
            />
            <MetricCell label="SOURCE UPDATED" value={updatedText} />
          </div>
        ) : (
          <RegimeStrip>
            <RegimeStripCell
              testId="divyield-strip-above"
              label="ABOVE 10Y"
              value={<span data-testid="divyield-above">{formatDivYieldPct(current.pct_above)}</span>}
              sub={<>TTM YIELD VS 10Y</>}
            />
            <RegimeStripCell
              testId="divyield-strip-count"
              label="COUNT"
              value={<span data-testid="divyield-count">{formatDivYieldCount(current.count_above, current.total)}</span>}
              sub={<>STRICTLY ABOVE 10Y</>}
            />
            <RegimeStripCell
              testId="divyield-strip-y10"
              label="10Y YIELD"
              value={<span data-testid="divyield-y10">{formatDivYieldPct(current.y10)}</span>}
              sub={<>10Y CMT PAR YIELD</>}
            />
            <RegimeStripCell
              testId="divyield-strip-regime"
              label="REGIME"
              value={
                <span data-testid="divyield-regime" style={{ color: regimeColor }}>
                  {regime ?? "---"}
                </span>
              }
              sub={<>BANDS 5 / 15 / 35 / 50</>}
            />
            <RegimeStripCell
              testId="divyield-strip-updated"
              label="SOURCE UPDATED"
              value={<span data-testid="divyield-updated">{updatedText}</span>}
              sub={<>S&P 500 CONSTITUENT SWEEP</>}
            />
          </RegimeStrip>
        )}
      </div>

      <div className="breadth-history-block" data-testid="divyield-chart-section">
        <HistoryRangeChips
          active={activeRange}
          onChange={(next) => {
            setCustomRange(null);
            setActiveRange(next);
          }}
          maxSessions={total}
          ariaLabel="Dividend yield breadth chart range"
          dataTestId="divyield-range-chips"
        />
        <CriHistoryChart
          history={slice}
          series={chartSeries}
          title="PCT OF SPX STOCKS YIELDING ABOVE 10Y"
          xTickFormat={formatMonthYearTick}
        />
        {total >= 2 && (
          <BrushMinimap
            values={series.map((p) => finiteOrNull(p.pct_above) ?? 0)}
            range={chartRange}
            onRangeChange={(r) => setCustomRange(r)}
            onCustom={() => setActiveRange("custom")}
            testIdPrefix="divyield-brush"
            ariaLabel="Dividend yield breadth history range brush"
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
          {SURVIVORSHIP_CAVEAT}
        </div>
      </div>
    </>
  );
}
