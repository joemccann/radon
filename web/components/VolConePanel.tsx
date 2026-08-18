"use client";

import { useMemo, useState, type MouseEvent } from "react";
import Link from "next/link";
import { Cone } from "lucide-react";
import BrushMinimap from "./BrushMinimap";
import HistoryRangeChips from "./HistoryRangeChips";
import InfoTooltip from "./InfoTooltip";
import MetricCell from "./mobile/MetricCell";
import SectionEmptyState from "./SectionEmptyState";
import SpectralLoader from "./SpectralLoader";
import VolConeChart from "./VolConeChart";
import SortTh from "./SortTh";
import { RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import { useSort } from "@/lib/useSort";
import {
  defaultPresetForLength,
  presetRange,
  type RangePresetSlug,
} from "@/lib/historyRange";
import { useVolCone } from "@/lib/useVolCone";
import { useViewport } from "@/lib/useViewport";
import {
  buildVolConeAnalysis,
  buildVolConeChartRows,
  formatIvPct,
  formatMonthlyExpiry,
  formatPercentile,
  formatVolConeRegime,
  volConeOrderHref,
  volConeRegimeColor,
  volConeTradeAriaLabel,
  type VolConeName,
} from "@/lib/volCone";

function nameKey(name: Pick<VolConeName, "ticker" | "expiry">): string {
  return `${name.ticker}:${name.expiry}`;
}

const VOL_CONE_TOOLTIP =
  "A hit is cheap ATM (at or below the 15th percentile of this expiry's cone) plus cheap 10% OTM wings (both at or below the 20th percentile).";

const SOURCE_FOOTNOTE =
  "Unusual Whales greeks, expiry-local 10% OTM wings. Cone is the 90/10 ATM IV range for this monthly.";

type NameFilter = "all" | "hits";
type VolConeSortKey = "ticker" | "expiry" | "dte" | "atm" | "call10" | "put10" | "atm_pct" | "wing" | "regime";

function volConeExtract(name: VolConeName, key: VolConeSortKey): string | number | null {
  switch (key) {
    case "ticker": return name.ticker;
    case "expiry": return name.expiry;
    case "dte": return name.dte;
    case "atm": return name.atm_iv;
    case "call10": return name.call_10_iv;
    case "put10": return name.put_10_iv;
    case "atm_pct": return name.atm_percentile;
    case "wing": return name.wing_score;
    case "regime": return name.regime;
    default: return null;
  }
}

function regimeTone(regime: VolConeName["regime"]): "pos" | "warn" | "neg" | "mut" {
  const color = volConeRegimeColor(regime);
  if (color === "var(--positive)") return "pos";
  if (color === "var(--warning)") return "warn";
  if (color === "var(--negative)") return "neg";
  return "mut";
}

function finiteBrushValue(value: number | null | undefined): number {
  return value != null && Number.isFinite(value) ? value : 0;
}

function formatWingStrikes(put: number | null, call: number | null): string {
  if (put == null || call == null) return "---";
  return `${put} / ${call}`;
}

function formatWingSigma(sigma: number | null): string {
  if (sigma == null || !Number.isFinite(sigma)) return "---";
  return `${sigma.toFixed(2)}σ`;
}

function expressLine(analysis: {
  href: string | null;
  wingStrikes: { put: number | null; call: number | null };
  structureLabel: string;
}): string {
  const { put, call } = analysis.wingStrikes;
  if (analysis.href && put != null && call != null) {
    if (analysis.structureLabel.includes("STRANGLE")) {
      return `Buy the listed ${put} put and ${call} call. Debit is max loss.`;
    }
    return `Buy the listed ${call} call and ${put} put. Debit is max loss.`;
  }
  return "No long-vol structure on this print.";
}

function stopRowSelect(event: MouseEvent) {
  event.stopPropagation();
}

export default function VolConePanel() {
  const { data, loading, syncing, lastSync } = useVolCone();
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;

  const [filter, setFilter] = useState<NameFilter>("all");
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [preset, setPreset] = useState<RangePresetSlug | "custom" | null>(null);
  const [customRange, setCustomRange] = useState<[number, number] | null>(null);

  const names = data?.names ?? [];
  const selected =
    names.find((name) => nameKey(name) === selectedTicker) ?? data?.current ?? names[0] ?? null;
  const series = selected?.series ?? [];
  const total = series.length;
  const activePreset: RangePresetSlug | "custom" = preset ?? defaultPresetForLength(total);

  const chartRange = useMemo<[number, number]>(() => {
    if (total < 2) return [0, Math.max(total - 1, 0)];
    if (activePreset === "custom" && customRange) {
      const max = total - 1;
      const end = Math.min(customRange[1], max);
      const start = Math.max(0, Math.min(customRange[0], end));
      return [start, end];
    }
    return presetRange(activePreset === "custom" ? "all" : activePreset, total);
  }, [activePreset, customRange, total]);

  const visibleNames = filter === "hits" ? (data?.hits ?? []) : names;
  const { sorted: sortedNames, sort, toggle } = useSort(visibleNames, volConeExtract);

  if ((loading || syncing) && !data) {
    return <SpectralLoader label="Loading UW vol cone scan" />;
  }

  if (!data || data.missing || !data.current) {
    return (
      <SectionEmptyState
        icon={Cone}
        headline="No vol cone data yet"
        secondary="The vol-cone refresh timer populates this tab from Unusual Whales expiry-local 10% OTM wing IVs. Data appears after the first successful pull."
      />
    );
  }

  const current = data.current;
  const rows = buildVolConeChartRows(series);
  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);
  const chartTitle = `${selected?.ticker ?? current.ticker} ${selected?.expiry ?? current.expiry} 90/10 VOL CONE`;
  const regimeLabel = formatVolConeRegime(current.regime);
  const regimeColor = volConeRegimeColor(current.regime);
  const analysis = selected ? buildVolConeAnalysis(selected) : null;

  return (
    <>
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Cone size={14} />
            Vol Cone
            <InfoTooltip text={VOL_CONE_TOOLTIP} />
          </div>
          {lastSync && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "9px", color: "var(--text-muted)" }}>
              {new Date(lastSync).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}
            </span>
          )}
        </div>

        {compact ? (
          <div className="m-regime-grid2x2" data-testid="vol-cone-mobile-grid">
            <MetricCell label="HITS" value={String(data.hit_count)} />
            <MetricCell label="BEST" value={current.ticker} />
            <MetricCell label="ATM IV" value={formatIvPct(current.atm_iv)} />
            <MetricCell label="WING" value={formatPercentile(current.wing_score)} />
            <MetricCell label="REGIME" value={regimeLabel} tone={regimeTone(current.regime)} />
            <MetricCell
              label={data.is_intraday ? "SOURCE (LIVE)" : "SOURCE DATE"}
              value={data.source_as_of ?? "---"}
            />
          </div>
        ) : (
          <RegimeStrip>
            <RegimeStripCell
              testId="vol-cone-strip-hits"
              label="HITS"
              value={String(data.hit_count)}
              sub={<>CHEAP WINGS OR CHEAP ATM</>}
            />
            <RegimeStripCell
              testId="vol-cone-strip-ticker"
              label="BEST"
              value={`${current.ticker} ${current.dte}D`}
              sub={<>{current.expiry}</>}
            />
            <RegimeStripCell
              testId="vol-cone-strip-atm"
              label="ATM IV"
              value={formatIvPct(current.atm_iv)}
              sub={<>DECIMAL IV x 100</>}
            />
            <RegimeStripCell
              testId="vol-cone-strip-wing"
              label="WING SCORE"
              value={formatPercentile(current.wing_score)}
              sub={<>MEAN OF 10C AND 10P RANKS</>}
            />
            <RegimeStripCell
              testId="vol-cone-strip-regime"
              label="REGIME"
              value={
                <span data-testid="vol-cone-regime-value" style={{ color: regimeColor }}>
                  {regimeLabel}
                </span>
              }
              sub={<>ATM AND WING PERCENTILES</>}
            />
            <RegimeStripCell
              testId="vol-cone-strip-source"
              label="SOURCE DATE"
              value={data.source_as_of ?? "---"}
              sub={<>{data.is_intraday ? "LIVE THIS SESSION" : "SESSION AS OF"}</>}
            />
          </RegimeStrip>
        )}
      </div>

      <div className="section" data-testid="vol-cone-table-section">
        <div className="section-header">
          <div className="section-title">Names</div>
          <nav className="history-range-chips" aria-label="Vol cone name filter" data-testid="vol-cone-filter-chips">
            <button
              type="button"
              className={`history-range-chip${filter === "all" ? " is-active" : ""}`}
              aria-pressed={filter === "all"}
              onClick={() => setFilter("all")}
            >
              ALL
            </button>
            <button
              type="button"
              className={`history-range-chip${filter === "hits" ? " is-active" : ""}`}
              aria-pressed={filter === "hits"}
              onClick={() => setFilter("hits")}
            >
              HITS
            </button>
          </nav>
        </div>
        <div className="section-body">
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <SortTh<VolConeSortKey> label="Ticker" sortKey="ticker" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<VolConeSortKey> label="Expiry" sortKey="expiry" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<VolConeSortKey> label="DTE" sortKey="dte" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<VolConeSortKey> label="ATM" sortKey="atm" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<VolConeSortKey> label="10C" sortKey="call10" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<VolConeSortKey> label="10P" sortKey="put10" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<VolConeSortKey> label="ATM %" sortKey="atm_pct" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<VolConeSortKey> label="WING" sortKey="wing" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<VolConeSortKey> label="Regime" sortKey="regime" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                </tr>
              </thead>
              <tbody>
                {sortedNames.map((name) => {
                  const key = nameKey(name);
                  const active = key === nameKey(selected ?? current);
                  const href = volConeOrderHref(name);
                  const tradeLabel = volConeTradeAriaLabel(name);
                  return (
                    <tr
                      key={key}
                      data-testid={`vol-cone-row-${name.ticker}-${name.expiry}`}
                      aria-selected={active}
                      onClick={() => {
                        setSelectedTicker(key);
                        setPreset(null);
                        setCustomRange(null);
                      }}
                      style={{
                        cursor: "pointer",
                        background: active
                          ? "color-mix(in srgb, var(--signal-core) 10%, transparent)"
                          : undefined,
                      }}
                    >
                      <td>
                        {href && tradeLabel ? (
                          <Link
                            href={href}
                            className="ticker-link"
                            aria-label={tradeLabel}
                            onClick={stopRowSelect}
                          >
                            {name.ticker}
                          </Link>
                        ) : (
                          name.ticker
                        )}
                      </td>
                      <td>{formatMonthlyExpiry(name.expiry)}</td>
                      <td className="right">{name.dte}</td>
                      <td className="right">{formatIvPct(name.atm_iv) === "---" ? "---" : `${formatIvPct(name.atm_iv)}%`}</td>
                      <td className="right">{formatIvPct(name.call_10_iv) === "---" ? "---" : `${formatIvPct(name.call_10_iv)}%`}</td>
                      <td className="right">{formatIvPct(name.put_10_iv) === "---" ? "---" : `${formatIvPct(name.put_10_iv)}%`}</td>
                      <td className="right">{formatPercentile(name.atm_percentile)}</td>
                      <td className="right">{formatPercentile(name.wing_score)}</td>
                      <td style={{ color: volConeRegimeColor(name.regime) }}>
                        {name.regime}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {analysis && selected && (
        <div className="section vol-cone-analysis" data-testid="vol-cone-analysis">
          <div className="section-header">
            <div className="section-title">
              {selected.ticker} {selected.expiry}
              <span style={{ color: volConeRegimeColor(analysis.regime) }}>
                {analysis.structureLabel}
              </span>
            </div>
            {analysis.href && (
              <Link href={analysis.href} className="vol-cone-analysis-trade">
                OPEN TRADE
              </Link>
            )}
          </div>
          {compact ? (
            <div className="vol-cone-analysis-metrics vol-cone-analysis-metrics--stack">
              <MetricCell label="1-SIGMA" value={analysis.expectedMoveDollars} />
              <MetricCell label="MOVE" value={analysis.expectedMovePct} />
              <MetricCell label="BAND" value={analysis.expectedMoveRange} />
              <MetricCell label="CONE GAP" value={analysis.coneGapLabel} />
              <MetricCell
                label="WINGS"
                value={formatWingStrikes(analysis.wingStrikes.put, analysis.wingStrikes.call)}
              />
              <MetricCell label="WING σ" value={formatWingSigma(analysis.wingsSigma)} />
            </div>
          ) : (
            <div className="vol-cone-analysis-metrics">
              <RegimeStrip>
                <RegimeStripCell
                  testId="vol-cone-analysis-move"
                  label="1-SIGMA"
                  value={analysis.expectedMoveDollars}
                  sub={<>ATM IV x SQRT(DTE/365)</>}
                />
                <RegimeStripCell
                  testId="vol-cone-analysis-move-pct"
                  label="MOVE"
                  value={analysis.expectedMovePct}
                  sub={<>ONE SIGMA</>}
                />
                <RegimeStripCell
                  testId="vol-cone-analysis-band"
                  label="BAND"
                  value={analysis.expectedMoveRange}
                  sub={<>SPOT +/- 1-SIGMA</>}
                />
                <RegimeStripCell
                  testId="vol-cone-analysis-gap"
                  label="CONE GAP"
                  value={analysis.coneGapLabel}
                  sub={<>P10 MINUS ATM</>}
                />
                <RegimeStripCell
                  testId="vol-cone-analysis-wings"
                  label="WINGS"
                  value={formatWingStrikes(analysis.wingStrikes.put, analysis.wingStrikes.call)}
                  sub={<>LISTED 10% OTM</>}
                />
                <RegimeStripCell
                  testId="vol-cone-analysis-sigma"
                  label="WING σ"
                  value={formatWingSigma(analysis.wingsSigma)}
                  sub={<>10% MOVE / 1-SIGMA</>}
                />
              </RegimeStrip>
            </div>
          )}
          <div className="section-body">
            <div className="table-wrap">
              <table className="data-table" data-sortable-exempt="chrome">
                <tbody>
                  <tr>
                    <td>THESIS</td>
                    <td>{analysis.thesis}</td>
                  </tr>
                  <tr>
                    <td>EXPRESS</td>
                    <td>{expressLine(analysis)}</td>
                  </tr>
                  <tr>
                    <td>WINS IF</td>
                    <td>{analysis.winsIf}</td>
                  </tr>
                  <tr>
                    <td>DIES IF</td>
                    <td>{analysis.diesIf}</td>
                  </tr>
                  <tr>
                    <td>NOT EDGE</td>
                    <td>{analysis.notEdge}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      <div className="breadth-history-block" data-testid="vol-cone-chart-section">
        <HistoryRangeChips
          active={activePreset}
          onChange={(slug) => {
            setCustomRange(null);
            setPreset(slug);
          }}
          maxSessions={total}
          ariaLabel="Vol cone chart range"
          dataTestId="vol-cone-range-chips"
        />
        <VolConeChart
          rows={slice}
          p10={selected?.p10 ?? null}
          p90={selected?.p90 ?? null}
          title={chartTitle}
        />
        {total >= 2 && (
          <BrushMinimap
            values={series.map((point) => finiteBrushValue(point.atm_iv))}
            range={chartRange}
            onRangeChange={(range) => setCustomRange(range)}
            onCustom={() => setPreset("custom")}
            testIdPrefix="vol-cone-brush"
            ariaLabel="Vol cone history range brush"
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
