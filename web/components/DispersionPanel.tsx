"use client";

import { useMemo, useState } from "react";
import { Layers } from "lucide-react";
import BrushMinimap from "./BrushMinimap";
import DispersionChart from "./DispersionChart";
import HistoryRangeChips from "./HistoryRangeChips";
import InfoTooltip from "./InfoTooltip";
import MetricCell from "./mobile/MetricCell";
import SectionEmptyState from "./SectionEmptyState";
import SpectralLoader from "./SpectralLoader";
import { RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import { getFreshnessWindowMs, getMarketStateFromDate } from "@/lib/serviceHealthWindows";
import { presetRange, type RangePresetSlug } from "@/lib/historyRange";
import {
  COMPRESSED_Z,
  STRESS_Z,
  WINDOW,
  formatSource,
  formatSpreadPct,
  formatVix,
  formatZ,
  regimeTone,
  sourceFootnote,
  type DispersionPoint,
} from "@/lib/dispersion";
import { useDispersion } from "@/lib/useDispersion";
import { useViewport } from "@/lib/useViewport";

/**
 * DISPERSION regime tab. Three volatility gauges on one z-score axis since
 * 2017: the VIX, the 95-5 spread of daily single-stock returns across the
 * S&P 500 seed, and the same spread across the 11 sector SPDRs. Descriptive
 * regime read only: no validation study was run, so no copy in this file may
 * claim forward information.
 *
 * Spec: docs/indicators/dispersion.md section H.
 */

const STRESS_LEVEL = STRESS_Z.toFixed(0);
const COMPRESSED_LEVEL = COMPRESSED_Z.toFixed(0);

const INFO_TOOLTIP =
  "Three volatility gauges on one z-score axis since 2017. VIX: the index. SINGLE STOCK: the gap between the " +
  "95th and 5th percentile of daily S&P 500 constituent returns. CROSS SECTOR: the same gap across the 11 sector " +
  `SPDRs. Each line is a rolling ${WINDOW}-session mean. ` +
  `Stock or sector above +${STRESS_LEVEL} while the VIX sits below zero means volatility is rising below the surface: ` +
  "index hedges are cheap while single-name risk is extreme. " +
  `Regimes: VIX at or above +${STRESS_LEVEL} is BROAD STRESS. Otherwise stock or sector at or above +${STRESS_LEVEL} ` +
  `is BELOW THE SURFACE. All three at or below ${COMPRESSED_LEVEL} is COMPRESSED. Anything else is NORMAL. ` +
  "Caveats: the universe is today's 503-name S&P 500 seed, so departed names are missing and later joiners start on " +
  "their first session. Returns are split-adjusted price returns, not total returns. XLC begins 2018-06-19, so " +
  "earlier sessions hold 10 sectors. Source: Interactive Brokers daily bars, Yahoo fallback.";

const EMPTY_SECONDARY =
  "The radon-dispersion timer populates this tab from Interactive Brokers daily bars for the S&P 500 seed, " +
  "the sector SPDRs and the VIX. Data appears after the first successful sweep.";

const STALE_BADGE_TITLE = "Source stale: re-serving the last confirmed series";

const YAHOO_BADGE_TITLE =
  "The Interactive Brokers rung served nothing this sweep; every price is a Yahoo Finance bar";

function finiteOrNull(v: number | null | undefined): number | null {
  return v != null && Number.isFinite(v) ? v : null;
}

/** Unsigned two-decimal z for the regime sub-line: 2.38 renders "2.38", -0.31 renders "-0.31". */
function formatPlainZ(v: number | null | undefined): string {
  return v != null && Number.isFinite(v) ? v.toFixed(2) : "---";
}

function regimeToneMobile(color: string): "pos" | "neg" | "warn" | "mut" {
  if (color === "var(--positive)") return "pos";
  if (color === "var(--negative)") return "neg";
  if (color === "var(--warning)") return "warn";
  return "mut";
}

/** Writer freshness as an AGE against the shared service-health catalog (R-365). */
function writerAge(
  raw: string | null | undefined,
  now: number = Date.now(),
): { label: string; state: "current" | "behind" | "unknown" } {
  if (!raw) return { label: "---", state: "unknown" };
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return { label: "---", state: "unknown" };

  const ageMs = Math.max(0, now - d.getTime());
  const windowMs = getFreshnessWindowMs("dispersion", getMarketStateFromDate(new Date(now)));
  const minutes = Math.floor(ageMs / 60_000);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  const magnitude = days >= 1 ? `${days}d` : hours >= 1 ? `${hours}h` : `${minutes}m`;

  return {
    label: `${magnitude} ago`,
    state: ageMs > windowMs ? "behind" : "current",
  };
}

export default function DispersionPanel() {
  const { data, loading, syncing } = useDispersion();
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;

  // Daily series back to 2017: default to the full history so the secular
  // shape of the three lines reads at a glance.
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
    return <SpectralLoader label="Loading dispersion series" />;
  }

  if (!data || data.missing || !data.current) {
    return (
      <SectionEmptyState
        icon={Layers}
        headline="No dispersion data yet"
        secondary={EMPTY_SECONDARY}
      />
    );
  }

  const current = data.current;
  const regime = current.regime;
  const regimeColor = regimeTone(regime);
  const writer = writerAge(data.scan_time);
  const isStale = data.status === "stale_source";
  const sourceSession = data.data_date ?? current.date ?? "---";
  const coverage = `${current.n_stocks ?? "---"} stocks / ${current.n_sectors ?? "---"} sectors`;
  const sourceText = formatSource(data.source);
  const yahooOnly = data.source?.prices === "yahoo";
  const regimeDetail = `stock ${formatPlainZ(current.z_stock)} / sector ${formatPlainZ(current.z_sector)} / VIX ${formatPlainZ(current.z_vix)}`;

  const rows: DispersionPoint[] = series.map((p) => ({
    date: p.date,
    z_vix: finiteOrNull(p.z_vix) as number,
    z_stock: finiteOrNull(p.z_stock) as number,
    z_sector: finiteOrNull(p.z_sector) as number,
    vix: finiteOrNull(p.vix) as number,
    stock_spread: finiteOrNull(p.stock_spread) as number,
    sector_spread: finiteOrNull(p.sector_spread) as number,
  }));
  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);

  const staleBadge = isStale ? (
    <span
      data-testid="dispersion-source-stale"
      title={STALE_BADGE_TITLE}
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: "9px",
        letterSpacing: "0.08em",
        color: "var(--text-muted)",
        border: "1px solid var(--border-dim)",
        borderRadius: "4px",
        padding: "1px 6px",
      }}
    >
      SOURCE STALE
    </span>
  ) : null;

  // R-434: a Yahoo-built sweep is real data but never a silent primary (rule 7).
  const yahooBadge = yahooOnly ? (
    <span
      data-testid="dispersion-source-degraded"
      title={YAHOO_BADGE_TITLE}
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: "9px",
        letterSpacing: "0.08em",
        color: "var(--warning)",
        border: "1px solid var(--warning)",
        borderRadius: "4px",
        padding: "1px 6px",
      }}
    >
      YAHOO FALLBACK
    </span>
  ) : null;

  return (
    <>
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Layers size={14} />
            Volatility Dispersion
            <InfoTooltip text={INFO_TOOLTIP} />
          </div>
          <span style={{ display: "inline-flex", alignItems: "center", gap: "8px" }}>
            {staleBadge}
            {yahooBadge}
            <span
              data-testid="dispersion-writer-age"
              data-state={writer.state}
              title={data.scan_time ? `Writer last wrote ${data.scan_time}` : "No writer timestamp"}
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "9px",
                color: writer.state === "behind" ? "var(--warning)" : "var(--text-muted)",
              }}
            >
              {writer.label}
            </span>
          </span>
        </div>

        {compact ? (
          <div className="m-regime-grid2x2" data-testid="dispersion-mobile-grid">
            <MetricCell label="REGIME" value={regime} tone={regimeToneMobile(regimeColor)} title={regimeDetail} />
            <MetricCell label="SINGLE STOCK" value={formatZ(current.z_stock)} title={`95-5 spread ${formatSpreadPct(current.stock_spread)}`} />
            <MetricCell label="CROSS SECTOR" value={formatZ(current.z_sector)} title={`95-5 spread ${formatSpreadPct(current.sector_spread)}`} />
            <MetricCell label="VIX" value={formatZ(current.z_vix)} title={formatVix(current.vix)} />
            <MetricCell label="SURFACE GAP" value={formatZ(current.surface_gap)} title="max(stock, sector) minus VIX" />
            <MetricCell label="SOURCE UPDATED" value={sourceSession} title={`${coverage} · ${sourceText}`} />
          </div>
        ) : (
          <RegimeStrip>
            <RegimeStripCell
              testId="dispersion-strip-regime"
              label="REGIME"
              value={
                <span data-testid="dispersion-regime" style={{ color: regimeColor }}>
                  {regime}
                </span>
              }
              sub={<>{regimeDetail}</>}
            />
            <RegimeStripCell
              testId="dispersion-strip-z-stock"
              label="SINGLE STOCK"
              value={<span data-testid="dispersion-z-stock">{formatZ(current.z_stock)}</span>}
              sub={<>95-5 spread {formatSpreadPct(current.stock_spread)}</>}
            />
            <RegimeStripCell
              testId="dispersion-strip-z-sector"
              label="CROSS SECTOR"
              value={<span data-testid="dispersion-z-sector">{formatZ(current.z_sector)}</span>}
              sub={<>95-5 spread {formatSpreadPct(current.sector_spread)}</>}
            />
            <RegimeStripCell
              testId="dispersion-strip-z-vix"
              label="VIX"
              value={<span data-testid="dispersion-z-vix">{formatZ(current.z_vix)}</span>}
              sub={<>{formatVix(current.vix)}</>}
            />
            <RegimeStripCell
              testId="dispersion-strip-gap"
              label="SURFACE GAP"
              value={<span data-testid="dispersion-gap">{formatZ(current.surface_gap)}</span>}
              sub={<>max(stock, sector) minus VIX</>}
            />
            <RegimeStripCell
              testId="dispersion-strip-source-updated"
              label="SOURCE UPDATED"
              value={<span data-testid="dispersion-source-updated">{sourceSession}</span>}
              sub={
                <>
                  {coverage} · <span data-testid="dispersion-source">{sourceText}</span>
                </>
              }
            />
          </RegimeStrip>
        )}
      </div>

      <div className="breadth-history-block" data-testid="dispersion-chart-section">
        <HistoryRangeChips
          active={activeRange}
          onChange={(next) => {
            setCustomRange(null);
            setActiveRange(next);
          }}
          maxSessions={total}
          ariaLabel="Volatility dispersion chart range"
          dataTestId="dispersion-range-chips"
        />
        <DispersionChart entries={slice} />
        {total >= 2 && (
          <BrushMinimap
            values={rows.map((p) => finiteOrNull(p.z_stock))}
            range={chartRange}
            onRangeChange={(r) => setCustomRange(r)}
            onCustom={() => setActiveRange("custom")}
            testIdPrefix="dispersion-brush"
            ariaLabel="Volatility dispersion history range brush"
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
          {sourceFootnote(data.source, data.fetch)}
        </div>
      </div>
    </>
  );
}
