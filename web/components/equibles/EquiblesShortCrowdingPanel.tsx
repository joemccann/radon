"use client";

import Link from "next/link";
import { Flame } from "lucide-react";
import InfoTooltip from "../InfoTooltip";
import SectionEmptyState from "../SectionEmptyState";
import SpectralLoader from "../SpectralLoader";
import { RegimeStrip, RegimeStripCell } from "../RegimeStrip";
import { useEquiblesShortCrowding } from "./useEquiblesShortCrowding";
import {
  buildScoreboardSummary,
  formatChangePct,
  formatDaysToCover,
  formatPercent,
  formatShares,
  formatSqueezeScore,
  intensityColor,
  shortInterestBasisLabel,
  tierColor,
  vintageLabel,
  type ShortCrowdingEntry,
} from "./shortCrowding";

const FOOTNOTE =
  "Crowding is one number read two ways: convex upside for long call structures (Gate 1), and a fat left tail for short or naked put legs (Gate 3). The squeeze score ranks how crowded and catalyst-primed a short is; it is not a directional buy signal. Short interest is a FINRA settlement figure and publishes with a reporting lag.";

const BORROW_NOTE =
  "Borrow cost and shortable supply come from the IB probe on each ticker page; they are not joined into this board yet.";

function IntensityChip({
  testId,
  label,
  intensity,
}: {
  testId: string;
  label: string;
  intensity: string;
}) {
  return (
    <span
      data-testid={testId}
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: "9px",
        color: intensityColor(intensity),
        border: `1px solid ${intensityColor(intensity)}`,
        borderRadius: "4px",
        padding: "1px 4px",
        whiteSpace: "nowrap",
      }}
    >
      {label} {intensity.toUpperCase()}
    </span>
  );
}

function ScoreboardRow({ entry }: { entry: ShortCrowdingEntry }) {
  const changeColor =
    entry.short_position_change_pct == null
      ? "var(--text-muted)"
      : entry.short_position_change_pct >= 0
        ? "var(--fault)"
        : "var(--signal-core)";

  return (
    <tr data-testid={`short-crowding-row-${entry.ticker}`} data-ticker={entry.ticker}>
      <td>
        <Link href={`/${encodeURIComponent(entry.ticker)}`} className="ticker-link">
          {entry.ticker}
        </Link>
      </td>
      <td className="right" data-testid={`short-crowding-score-${entry.ticker}`}>
        {formatSqueezeScore(entry.squeeze_score)}
      </td>
      <td className="right">{formatDaysToCover(entry.days_to_cover)}</td>
      <td className="right">{formatShares(entry.short_position)}</td>
      <td className="right" style={{ color: changeColor }}>
        {formatChangePct(entry.short_position_change_pct)}
      </td>
      <td className="right" title={shortInterestBasisLabel(entry.short_interest_pct_basis)}>
        {formatPercent(entry.short_interest_pct)}
      </td>
      <td style={{ color: tierColor(entry.crowding_tier) }}>
        {entry.crowding_tier.toUpperCase()}
      </td>
      <td>
        <span style={{ display: "inline-flex", gap: "4px" }}>
          <IntensityChip
            testId={`short-crowding-upside-${entry.ticker}`}
            label="CALL"
            intensity={entry.readings.upside_convexity}
          />
          <IntensityChip
            testId={`short-crowding-tail-${entry.ticker}`}
            label="SHORT LEG"
            intensity={entry.readings.short_leg_tail_risk}
          />
        </span>
      </td>
      <td className="right" data-testid={`short-crowding-borrow-${entry.ticker}`}>
        {entry.borrow?.resolved && entry.borrow.fee_rate != null
          ? formatPercent(entry.borrow.fee_rate)
          : "---"}
      </td>
    </tr>
  );
}

export default function EquiblesShortCrowdingPanel() {
  const { data, loading, syncing } = useEquiblesShortCrowding();

  if ((loading || syncing) && !data) {
    return <SpectralLoader label="Loading short crowding" />;
  }

  const entries = data?.entries ?? [];

  if (!data || data.missing || entries.length === 0) {
    return (
      <SectionEmptyState
        icon={Flame}
        headline="No short interest data yet"
        secondary="The short-crowding refresh job populates this board from Equibles short interest and squeeze scores. Rows appear after the first successful pull."
      />
    );
  }

  const summary = buildScoreboardSummary(entries);
  const vintage = vintageLabel(
    data.latest_settlement_date ?? entries[0]?.settlement_date ?? null,
    entries[0]?.settlement_age_days ?? null,
  );

  return (
    <>
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Flame size={14} />
            Short Crowding and Squeeze
            <InfoTooltip text="Days to cover is how many sessions of average volume the short side would need to buy back. High crowding is convex upside for long call structures and a fat left tail for short or naked put legs. The squeeze score ranks crowding plus catalyst pressure, not direction." />
          </div>
          <span
            data-testid="short-crowding-vintage"
            style={{ fontFamily: "var(--font-mono)", fontSize: "9px", color: "var(--text-muted)" }}
          >
            {vintage}
          </span>
        </div>

        <RegimeStrip>
          <RegimeStripCell
            testId="short-crowding-strip-crowded"
            label="MOST CROWDED"
            value={
              <span style={{ color: tierColor(summary.mostCrowded?.crowding_tier ?? "unknown") }}>
                {summary.mostCrowded
                  ? `${summary.mostCrowded.ticker} ${formatDaysToCover(summary.mostCrowded.days_to_cover)}`
                  : "---"}
              </span>
            }
            sub={<>DAYS TO COVER</>}
          />
          <RegimeStripCell
            testId="short-crowding-strip-score"
            label="TOP SQUEEZE SCORE"
            value={
              summary.topScore
                ? `${summary.topScore.ticker} ${formatSqueezeScore(summary.topScore.squeeze_score)}`
                : "---"
            }
            sub={<>CROWDING RANK, NOT DIRECTION</>}
          />
          <RegimeStripCell
            testId="short-crowding-strip-extreme"
            label="EXTREME TIER"
            value={String(summary.extremeCount)}
            sub={<>5+ DAYS TO COVER</>}
          />
          <RegimeStripCell
            testId="short-crowding-strip-covered"
            label="TICKERS COVERED"
            value={String(entries.length)}
            sub={<>{vintage}</>}
          />
        </RegimeStrip>
      </div>

      <div className="section">
        <div className="table-scroll">
          <table className="data-table" data-testid="short-crowding-table">
            <thead>
              <tr>
                <th>Ticker</th>
                <th className="right">Squeeze</th>
                <th className="right">Days to Cover</th>
                <th className="right">Short Position</th>
                <th className="right">Chg vs Prior</th>
                <th className="right">Short Interest %</th>
                <th>Tier</th>
                <th>Readings</th>
                <th className="right">Borrow Fee</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <ScoreboardRow key={entry.ticker} entry={entry} />
              ))}
            </tbody>
          </table>
        </div>

        <div
          data-testid="short-crowding-footnote"
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "9px",
            color: "var(--text-muted)",
            marginTop: "8px",
          }}
        >
          {FOOTNOTE} {BORROW_NOTE}
        </div>
      </div>
    </>
  );
}
