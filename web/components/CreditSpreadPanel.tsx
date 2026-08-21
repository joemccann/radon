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
import { presetRange, presetSessions, type RangePresetSlug } from "@/lib/historyRange";
import {
  formatDateTick,
  formatPct,
  formatSessionDate,
  regimeColor,
  type CreditRegime,
} from "@/lib/creditSpread";
import { useCreditSpread } from "@/lib/useCreditSpread";
import { useViewport } from "@/lib/useViewport";

const SOURCE_FOOTNOTE =
  "Source: Interactive Brokers daily closes (HYG, S&P 500), then Unusual Whales, then Yahoo Finance. HYG is the traded high-yield credit proxy. ICE CCC OAS is not stored.";

const INFO_TOOLTIP =
  "HYG is the traded high-yield credit proxy. ICE CCC option-adjusted spreads are not stored. Equities and high-yield credit usually rise together. Divergence means the S&P 500 is up over 168 sessions while HYG is down.";

interface CreditChartRow {
  date: string;
  spx: number | null;
  hyg: number | null;
}

function regimeTone(regime: CreditRegime | null | undefined): "pos" | "neg" | "warn" | "mut" {
  const color = regimeColor(regime);
  if (color === "var(--negative)") return "neg";
  if (color === "var(--warning)") return "warn";
  if (color === "var(--positive)") return "pos";
  return "mut";
}

export default function CreditSpreadPanel() {
  const { data, loading, syncing, lastSync } = useCreditSpread();
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;

  const [activeRange, setActiveRange] = useState<RangePresetSlug | "custom">("all");
  const [customRange, setCustomRange] = useState<[number, number] | null>(null);
  const [rangeTouched, setRangeTouched] = useState(false);

  const series = data?.series ?? [];
  const total = series.length;

  useEffect(() => {
    if (rangeTouched || activeRange === "custom" || activeRange === "all") return;
    if (presetSessions(activeRange) > total) setActiveRange("all");
  }, [activeRange, rangeTouched, total]);

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
    return <SpectralLoader label="Loading high-yield credit series" />;
  }

  if (!data || data.missing || !data.current || series.length === 0) {
    return (
      <SectionEmptyState
        icon={Activity}
        headline="No credit series yet"
        secondary="The credit-spread refresh timer populates this tab from Interactive Brokers daily closes for HYG and the S&P 500."
      />
    );
  }

  const current = data.current;
  const rows: CreditChartRow[] = series.map((p) => ({
    date: p.date,
    // Log axis: skip non-positive SPX so d3.scaleLog never emits NaN paths.
    spx: Number.isFinite(p.spx_close) && p.spx_close > 0 ? p.spx_close : null,
    hyg: Number.isFinite(p.hyg_close) ? p.hyg_close : null,
  }));
  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);

  const chartSeries: [ChartSeries<CreditChartRow>, ChartSeries<CreditChartRow>] = [
    {
      key: "spx",
      label: "S&P 500",
      color: chartSeriesColor("primary"),
      axis: "left",
      scaleType: "log",
      format: (v: number) => v.toFixed(0),
    },
    {
      key: "hyg",
      label: "HYG",
      color: chartSeriesColor("fault"),
      axis: "right",
      scaleType: "linear",
      format: (v: number) => v.toFixed(2),
    },
  ];

  return (
    <>
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Activity size={14} />
            Credit
            <InfoTooltip text={INFO_TOOLTIP} />
          </div>
          {lastSync && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "9px", color: "var(--text-muted)" }}>
              {new Date(lastSync).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}
            </span>
          )}
        </div>

        {compact ? (
          <div className="m-regime-grid2x2" data-testid="credit-spread-mobile-grid">
            <MetricCell
              label="REGIME"
              value={current.regime.toUpperCase()}
              tone={regimeTone(current.regime)}
            />
            <MetricCell label="HYG 8M" value={formatPct(current.hyg_ret)} />
            <MetricCell label="SPX 8M" value={formatPct(current.spx_ret)} />
            <MetricCell label="SESSION" value={formatSessionDate(current.date)} />
          </div>
        ) : (
          <RegimeStrip>
            <RegimeStripCell
              testId="credit-spread-strip-regime"
              label="REGIME"
              value={
                <span data-testid="credit-spread-regime-value" style={{ color: regimeColor(current.regime) }}>
                  {current.regime.toUpperCase()}
                </span>
              }
              sub={<>168 SESSION HYG VS SPX</>}
            />
            <RegimeStripCell
              testId="credit-spread-strip-hyg"
              label="HYG 8M"
              value={<span data-testid="credit-spread-hyg-ret">{formatPct(current.hyg_ret)}</span>}
            />
            <RegimeStripCell
              testId="credit-spread-strip-spx"
              label="SPX 8M"
              value={<span data-testid="credit-spread-spx-ret">{formatPct(current.spx_ret)}</span>}
            />
            <RegimeStripCell
              testId="credit-spread-strip-session"
              label="LATEST SESSION"
              value={<span data-testid="credit-spread-session">{formatSessionDate(current.date)}</span>}
            />
          </RegimeStrip>
        )}
      </div>

      <div className="breadth-history-block" data-testid="credit-spread-chart-section">
        <HistoryRangeChips
          active={activeRange}
          onChange={(preset) => {
            setRangeTouched(true);
            setCustomRange(null);
            setActiveRange(preset);
          }}
          maxSessions={total}
          ariaLabel="Credit series chart range"
          dataTestId="credit-spread-range-chips"
        />
        <CriHistoryChart
          history={slice}
          series={chartSeries}
          title="S&P 500 VS HYG"
          xTickFormat={formatDateTick}
        />
        {total >= 2 && (
          <BrushMinimap
            values={series.map((p) => p.hyg_close ?? 0)}
            range={chartRange}
            onRangeChange={(r) => setCustomRange(r)}
            onCustom={() => {
              setRangeTouched(true);
              setActiveRange("custom");
            }}
            testIdPrefix="credit-spread-brush"
            ariaLabel="Credit series history range brush"
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
