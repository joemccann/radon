"use client";

import { useMemo, useState } from "react";
import { Cone } from "lucide-react";
import BrushMinimap from "./BrushMinimap";
import HistoryRangeChips from "./HistoryRangeChips";
import InfoTooltip from "./InfoTooltip";
import MetricCell from "./mobile/MetricCell";
import SectionEmptyState from "./SectionEmptyState";
import SpectralLoader from "./SpectralLoader";
import VolConeChart from "./VolConeChart";
import { RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import {
  defaultPresetForLength,
  presetRange,
  type RangePresetSlug,
} from "@/lib/historyRange";
import { useVolCone } from "@/lib/useVolCone";
import { useViewport } from "@/lib/useViewport";
import {
  buildVolConeChartRows,
  formatIvPct,
  formatMonthlyExpiry,
  formatPercentile,
  formatVolConeRegime,
  volConeRegimeColor,
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
  const visibleNames = filter === "hits" ? data.hits : names;
  const rows = buildVolConeChartRows(series);
  const [start, end] = chartRange;
  const slice = rows.slice(start, end + 1);
  const chartTitle = `${selected?.ticker ?? current.ticker} ${selected?.expiry ?? current.expiry} 90/10 VOL CONE`;
  const regimeLabel = formatVolConeRegime(current.regime);
  const regimeColor = volConeRegimeColor(current.regime);

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
            <MetricCell label="SOURCE DATE" value={data.source_as_of ?? "---"} />
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
              sub={<>SESSION AS OF</>}
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
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Ticker</th>
                  <th>Expiry</th>
                  <th className="right">DTE</th>
                  <th className="right">ATM</th>
                  <th className="right">10C</th>
                  <th className="right">10P</th>
                  <th className="right">ATM %</th>
                  <th className="right">WING</th>
                  <th>Regime</th>
                </tr>
              </thead>
              <tbody>
                {visibleNames.map((name) => {
                  const key = nameKey(name);
                  const active = key === nameKey(selected ?? current);
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
                      <td>{name.ticker}</td>
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
