"use client";

import { Fragment, useMemo, useState } from "react";
import { Unlink } from "lucide-react";
import BrushMinimap from "./BrushMinimap";
import HistoryRangeChips from "./HistoryRangeChips";
import InfoTooltip from "./InfoTooltip";
import MetricCell from "./mobile/MetricCell";
import SectionEmptyState from "./SectionEmptyState";
import SpectralLoader from "./SpectralLoader";
import VixCorChart from "./VixCorChart";
import { RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import { formatCor } from "@/lib/cor";
import { presetRange, type RangePresetSlug } from "@/lib/historyRange";
import {
  buildVixcorChartRows,
  formatCorr,
  formatDrawup,
  formatPercentile,
  formatVix,
  lastResolvedEpisode,
  openEpisode,
  parentLagCopy,
  visibleEpisodes,
  vixcorRegime,
  vixcorRegimeColor,
  type VixcorForwardBucket,
} from "@/lib/vixcor";
import { useVixcor } from "@/lib/useVixcor";
import { useViewport } from "@/lib/useViewport";

/**
 * VIX-COR regime tab. Descriptive read of how tightly the VIX and the Cboe
 * 3-month SPX implied correlation are moving together, and nothing more.
 *
 * The operator's premise was that a broken correlation is an early read on a
 * higher VIX. The validation study rejected it: post-breakdown forward VIX
 * drawup lands below the all-session mean at every horizon tested, and the
 * mechanism is mundane (a level correlation collapses when either leg goes
 * range-bound, so the detector fires on a quiet VIX). The rebuttal stops where
 * the data stops: on the median the two are indistinguishable at 42 sessions
 * (+31.8% after a breakdown against +29.3% unconditionally, P(+20% drawup)
 * 63.3% against 63.8%, Mann-Whitney p=0.68), which is the horizon the
 * operator's arrows span. The base-rate block below the chart therefore ships
 * as a required element, not decoration, showing the mean, the median and the
 * +20% share side by side, and no copy in this file may claim forward
 * information or overstate the refutation.
 *
 * Spec: docs/indicators/vixcor.md sections 0, G.5, G.6, G.7.
 */

const VIXCOR_TOOLTIP =
  "Rolling 20-session Pearson correlation between the VIX close and the Cboe 3-month SPX implied correlation close, computed on price levels over the two series' shared session calendar. Below 0.25 is DECOUPLED, 0.25 to 0.50 is LOOSENING, 0.50 and above is COUPLED. A level correlation collapses when either leg goes range-bound, so a low reading marks a quiet VIX tape: breakdown windows average a 20-day VIX coefficient of variation of 6.9 percent against 11.8 percent when the two are coupled. Forward VIX drawup after a breakdown runs below the all-session mean at every horizon, while on the median the two are indistinguishable at 42 sessions, so read this as a regime state, not a warning.";

const SOURCE_FOOTNOTE =
  "Cboe VIX daily closes and the COR3M implied correlation index, inner joined on session date since 2006-01. Shaded bands are breakdown episodes, defined as a run below 0.30 containing at least one session below 0.25. The window and all statistics span the full history, not the visible range.";

const BASE_RATE_HEADLINE = "FORWARD VIX DRAWUP, BREAKDOWNS VERSUS ALL SESSIONS";

const BASE_RATE_FOOTNOTE =
  "Forward VIX drawup after a breakdown is below the all-session mean at every horizon, while on the median the two are indistinguishable at 42 sessions and the share reaching +20% is the same. The mean gap comes from the right skew of the all-session distribution. This is a regime description, not a forecast.";

/**
 * The three measures the block shows side by side. The mean alone is what the
 * rebuttal is scoped to, so the median and the +20% share ship beside it and
 * the reader sees the 42-session wash without taking the copy on trust.
 */
const BASE_RATE_MEASURES: ReadonlyArray<{
  key: string;
  label: string;
  read: (bucket: VixcorForwardBucket | undefined) => string;
}> = [
  { key: "mean", label: "MEAN DRAWUP", read: (b) => formatDrawup(b?.mean_drawup) },
  { key: "median", label: "MEDIAN DRAWUP", read: (b) => formatDrawup(b?.median_drawup) },
  { key: "p20", label: "SHARE REACHING +20%", read: (b) => formatPercentile(b?.p_drawup_20) },
];

/** Column-group rule, so three paired measures read as three blocks. */
const GROUP_DIVIDER = "1px solid var(--border-dim)";

const EMPTY_SECONDARY =
  "The radon-vixcor timer populates this tab from the Cboe VIX history and the COR3M series already in Turso. Data appears after the first successful pull.";

/** Daily-series x-tick over a multi-decade span: "05 Aug 26". */
function formatDayTick(d: Date): string {
  return d.toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "2-digit",
    timeZone: "UTC",
  });
}

export default function VixCorPanel() {
  const { data, loading, syncing, lastSync } = useVixcor();
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;

  const [preset, setPreset] = useState<RangePresetSlug | "custom" | null>(null);
  const [customRange, setCustomRange] = useState<[number, number] | null>(null);

  const series = data?.series ?? [];
  const total = series.length;
  // Multi-decade series: default to the full history, not a recent slice.
  const activePreset: RangePresetSlug | "custom" = preset ?? "all";

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
    return <SpectralLoader label="Loading Cboe VIX and implied correlation series" />;
  }

  if (!data || data.missing || !data.current || series.length === 0) {
    return (
      <SectionEmptyState
        icon={Unlink}
        headline="No VIX correlation data yet"
        secondary={EMPTY_SECONDARY}
      />
    );
  }

  const current = data.current;
  const regime = vixcorRegime(current.corr20) ?? current.regime;
  const regimeTone = vixcorRegimeColor(regime);
  const live = openEpisode(data.episodes) ?? (current.episode?.open ? current.episode : null);
  const lastClosed = lastResolvedEpisode(data.episodes);
  const lagSessions = data.lag_sessions ?? 0;
  const lagCopy = parentLagCopy(lagSessions);
  const lagTone = lagSessions > 1 ? "var(--warning)" : "var(--text-muted)";

  const rows = buildVixcorChartRows(series, chartRange);
  const bands = visibleEpisodes(
    data.episodes,
    rows.length > 0 ? rows[0].date : null,
    rows.length > 0 ? rows[rows.length - 1].date : null,
  );
  const horizons = data.forward_stats?.horizons ?? [];

  const episodeValue = live ? live.start : "NONE";
  const episodeSub = live
    ? `${live.sessions} SESSIONS, TROUGH ${formatCorr(live.trough)}`
    : lastClosed
      ? `LAST ${lastClosed.start}`
      : "NO EPISODES ON RECORD";

  return (
    <>
      {/* ── Summary strip ─────────────────────────────────── */}
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Unlink size={14} />
            VIX / COR3M Correlation
            <InfoTooltip text={VIXCOR_TOOLTIP} />
          </div>
          {lastSync && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: "9px", color: "var(--text-muted)" }}>
              {new Date(lastSync).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}
            </span>
          )}
        </div>

        {compact ? (
          <div className="m-regime-grid2x2" data-testid="vixcor-mobile-grid">
            <MetricCell label="CORR 20D" value={formatCorr(current.corr20)} />
            <MetricCell label="REGIME" value={regime ?? "---"} />
            <MetricCell label="VIX" value={formatVix(current.vix_close)} />
            <MetricCell label="COR 3M" value={formatCor(current.cor3m_close)} />
            <MetricCell label="EPISODE" value={episodeValue} />
            <MetricCell label="AS OF" value={data.as_of ?? current.date} />
          </div>
        ) : (
          <div className="vixcor-strip">
            <RegimeStrip>
              <RegimeStripCell
                testId="vixcor-strip-corr"
                label="CORR 20D"
                value={formatCorr(current.corr20)}
                sub={<>1D {formatCorr(current.change_1d)}</>}
              />
              <RegimeStripCell
                testId="vixcor-strip-regime"
                label="REGIME"
                value={
                  <span data-testid="vixcor-regime-value" style={{ color: regimeTone }}>
                    {regime ?? "---"}
                  </span>
                }
                sub={<>PCTILE {formatPercentile(current.percentile)}</>}
              />
              <RegimeStripCell
                testId="vixcor-strip-vix"
                label="VIX"
                value={formatVix(current.vix_close)}
                sub={<>20D COV {formatPercentile(current.vix_cov_20d)}</>}
              />
              <RegimeStripCell
                testId="vixcor-strip-cor3m"
                label="COR 3M"
                value={formatCor(current.cor3m_close)}
                sub={<>CBOE 3M IMPLIED CORRELATION</>}
              />
              <RegimeStripCell
                testId="vixcor-strip-episode"
                label="EPISODE"
                value={episodeValue}
                sub={<>{episodeSub}</>}
              />
              <RegimeStripCell
                testId="vixcor-strip-asof"
                label="AS OF"
                value={data.as_of ?? current.date}
                sub={
                  <span data-testid="vixcor-lag-copy" style={{ color: lagTone }}>
                    {lagCopy}
                  </span>
                }
                />
            </RegimeStrip>
          </div>
        )}
      </div>

      {/* ── VIX over the 20-session correlation ───────────── */}
      <div className="breadth-history-block" data-testid="vixcor-chart-section">
        <HistoryRangeChips
          active={activePreset}
          onChange={(slug) => {
            setCustomRange(null);
            setPreset(slug);
          }}
          maxSessions={total}
          ariaLabel="VIX correlation chart range"
          dataTestId="vixcor-range-chips"
        />

        <VixCorChart
          history={rows}
          episodes={bands}
          title="VIX VS 3-MONTH IMPLIED CORRELATION"
          xTickFormat={formatDayTick}
        />

        {total >= 2 && (
          <BrushMinimap
            values={series.map((entry) => entry.corr20)}
            range={chartRange}
            onRangeChange={(r) => setCustomRange(r)}
            onCustom={() => setPreset("custom")}
            testIdPrefix="vixcor-brush"
            ariaLabel="VIX correlation history range brush"
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

      {/* ── The validation verdict, rendered ──────────────── */}
      {data.forward_stats && horizons.length > 0 ? (
        <div className="section vixcor-base-rate" data-testid="vixcor-base-rate">
          <div className="section-header">
            <div className="section-title">{BASE_RATE_HEADLINE}</div>
          </div>
          <div style={{ overflowX: "auto" }}>
            <table className="vixcor-base-rate-table" data-sortable-exempt="matrix">
              <thead>
                <tr>
                  <th scope="col" rowSpan={2}>
                    HORIZON
                  </th>
                  {BASE_RATE_MEASURES.map((measure) => (
                    <th
                      key={measure.key}
                      scope="colgroup"
                      colSpan={2}
                      style={{ textAlign: "center", borderLeft: GROUP_DIVIDER }}
                    >
                      {measure.label}
                    </th>
                  ))}
                </tr>
                <tr>
                  {BASE_RATE_MEASURES.map((measure) => (
                    <Fragment key={measure.key}>
                      <th scope="col" style={{ borderLeft: GROUP_DIVIDER }}>
                        BREAKDOWN
                      </th>
                      <th scope="col">ALL SESSIONS</th>
                    </Fragment>
                  ))}
                </tr>
              </thead>
              <tbody>
                {horizons.map((horizon) => {
                  const key = String(horizon);
                  const event = data.forward_stats?.event[key];
                  const base = data.forward_stats?.base[key];
                  return (
                    <tr key={key} data-testid={`vixcor-base-rate-row-${key}`}>
                      <th scope="row">{`${horizon}D`}</th>
                      {BASE_RATE_MEASURES.map((measure) => (
                        <Fragment key={measure.key}>
                          <td style={{ borderLeft: GROUP_DIVIDER }}>{measure.read(event)}</td>
                          <td>{measure.read(base)}</td>
                        </Fragment>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="vixcor-base-rate-note">{BASE_RATE_FOOTNOTE}</div>
        </div>
      ) : null}
    </>
  );
}
