"use client";

import { useMemo, useState } from "react";
import { Gauge } from "lucide-react";

import BpiChart from "./BpiChart";
import BrushMinimap from "./BrushMinimap";
import HistoryRangeChips from "./HistoryRangeChips";
import InfoTooltip from "./InfoTooltip";
import MetricCell from "./mobile/MetricCell";
import SectionEmptyState from "./SectionEmptyState";
import SpectralLoader from "./SpectralLoader";
import { RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import {
  BPI_INDEX_SYMBOLS,
  bpiToneColor,
  buildBpiEntries,
  classifyBpiState,
  isBpiPayload,
  isBpiSessionCurrent,
  type BpiIndexSymbol,
  type BpiPayload,
  type BpiTone,
} from "@/lib/bpi";
import { useBpi } from "@/lib/useBpi";
import {
  defaultPresetForLength,
  presetRange,
  type RangePresetSlug,
} from "@/lib/historyRange";
import { useViewport } from "@/lib/useViewport";

const CHART_FOOTNOTE =
  "Close-only point and figure, traditional box scale, 3-box reversal. Members without a resolved signal are excluded from the denominator.";

const TOOLTIP_COPY =
  "Bullish Percent Index: share of index members whose point and figure chart is on a buy signal. Below 30 is oversold; the buy read is the cross back UP through 30 (the savvy oversold bull). Above 70 is overbought.";

function metricTone(tone: BpiTone): "pos" | "warn" | "mut" {
  if (tone === "positive") return "pos";
  if (tone === "warning") return "warn";
  return "mut";
}

function IndexSwitcher({
  active,
  compact,
  onChange,
}: {
  active: BpiIndexSymbol;
  compact: boolean;
  onChange: (symbol: BpiIndexSymbol) => void;
}) {
  if (compact) {
    return (
      <nav
        className="m-segment"
        aria-label="Bullish percent index"
        data-testid="bpi-index-chips"
        style={{ width: "auto", margin: "12px 16px" }}
      >
        {BPI_INDEX_SYMBOLS.map((symbol) => (
          <button
            key={symbol}
            type="button"
            className={`m-segment__item${active === symbol ? " m-segment__item--active" : ""}`}
            aria-pressed={active === symbol}
            onClick={() => onChange(symbol)}
            data-testid={`bpi-index-chip-${symbol}`}
          >
            {symbol}
          </button>
        ))}
      </nav>
    );
  }

  return (
    <nav className="history-range-chips" aria-label="Bullish percent index" data-testid="bpi-index-chips">
      {BPI_INDEX_SYMBOLS.map((symbol) => (
        <button
          key={symbol}
          type="button"
          className={`history-range-chip${active === symbol ? " is-active" : ""}`}
          aria-pressed={active === symbol}
          onClick={() => onChange(symbol)}
          data-testid={`bpi-index-chip-${symbol}`}
        >
          {symbol}
        </button>
      ))}
    </nav>
  );
}

function BpiSessionStaleMark() {
  return (
    <span
      data-testid="bpi-session-stale"
      style={{
        marginLeft: "8px",
        fontFamily: "var(--font-mono)",
        fontSize: "var(--text-meta)",
        fontWeight: 700,
        letterSpacing: "0.08em",
        color: "var(--warning)",
        border: "1px solid var(--warning)",
        padding: "2px 5px",
        textTransform: "uppercase",
        lineHeight: 1,
        borderRadius: "999px",
      }}
    >
      STALE
    </span>
  );
}

function BpiReadout({ payload, compact }: { payload: BpiPayload; compact: boolean }) {
  const badge = classifyBpiState(payload);
  const stateColor = bpiToneColor(badge.tone);
  const sessionStale = !isBpiSessionCurrent(payload.as_of_session);

  if (compact) {
    return (
      <div
        className="m-regime-grid2x2"
        data-testid="bpi-mobile-grid"
        style={{
          // Fuse into the section frame: the section border supplies the
          // left/right/bottom hairlines, a single top hairline separates
          // the grid from the switcher, and zero margin removes the sliver
          // row before the section's bottom border.
          borderWidth: "1px 0 0",
          borderTopColor: "var(--line-grid)",
          margin: 0,
        }}
      >
        <MetricCell label="BPI" value={payload.bpi.toFixed(1)} />
        <MetricCell label="STATE" value={badge.label} tone={metricTone(badge.tone)} />
        <MetricCell label="BULLISH" value={`${payload.bullish} / ${payload.members}`} />
        <MetricCell
          label="AS OF"
          value={sessionStale ? `${payload.as_of_session} STALE` : payload.as_of_session}
          tone={sessionStale ? "warn" : undefined}
        />
      </div>
    );
  }

  return (
    <RegimeStrip>
      <RegimeStripCell
        testId="bpi-strip-value"
        label="BULLISH %"
        value={<span data-testid="bpi-value">{payload.bpi.toFixed(1)}</span>}
        sub={<>{payload.index_name.toUpperCase()}</>}
      />
      <RegimeStripCell
        testId="bpi-strip-state"
        label="STATE"
        value={
          <span data-testid="bpi-state" data-tone={badge.tone} style={{ color: stateColor }}>
            {badge.label}
          </span>
        }
        sub={<>BELOW 30 OVERSOLD / ABOVE 70 OVERBOUGHT</>}
      />
      <RegimeStripCell
        testId="bpi-strip-members"
        label="MEMBERS BULLISH"
        value={<span data-testid="bpi-members">{`${payload.bullish} / ${payload.members}`}</span>}
        sub={<>RESOLVED P&F SIGNALS</>}
      />
      <RegimeStripCell
        testId="bpi-strip-session"
        label="AS OF SESSION"
        value={
          <>
            {payload.as_of_session}
            {sessionStale ? <BpiSessionStaleMark /> : null}
          </>
        }
        sub={<>DAILY CLOSE SERIES</>}
      />
    </RegimeStrip>
  );
}

export default function BpiPanel() {
  const { data, loading, error } = useBpi();
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;

  const [index, setIndex] = useState<BpiIndexSymbol>("NDX");
  const [preset, setPreset] = useState<RangePresetSlug | "custom" | null>(null);
  const [customRange, setCustomRange] = useState<[number, number] | null>(null);

  const entry = data?.indices?.[index];
  const payload = isBpiPayload(entry) ? entry : null;

  const entries = useMemo(
    () => (payload ? buildBpiEntries(payload.history, payload.thresholds.oversold) : []),
    [payload],
  );
  const total = entries.length;
  const activePreset: RangePresetSlug | "custom" = preset ?? defaultPresetForLength(total);

  const chartRange = useMemo<[number, number]>(() => {
    if (total === 0) return [0, 0];
    if (activePreset === "custom" && customRange) {
      const max = total - 1;
      const end = Math.min(customRange[1], max);
      const start = Math.max(0, Math.min(customRange[0], end));
      return [start, end];
    }
    return presetRange(activePreset === "custom" ? "all" : activePreset, total);
  }, [activePreset, customRange, total]);

  const switchIndex = (symbol: BpiIndexSymbol) => {
    setIndex(symbol);
    setPreset(null);
    setCustomRange(null);
  };

  if (loading && !data) {
    return <SpectralLoader label="Sampling point and figure buy signals" />;
  }

  if (error && !data) {
    return (
      <SectionEmptyState
        icon={Gauge}
        headline="Bullish percent measurement unavailable"
        secondary={error}
      />
    );
  }

  const [start, end] = chartRange;
  const slice = entries.slice(start, end + 1);

  return (
    <>
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Gauge size={14} />
            Bullish Percent Index
            <InfoTooltip text={TOOLTIP_COPY} />
          </div>
        </div>

        <IndexSwitcher active={index} compact={compact} onChange={switchIndex} />

        {payload ? (
          <BpiReadout payload={payload} compact={compact} />
        ) : (
          <SectionEmptyState
            icon={Gauge}
            headline={`No bullish percent data yet for ${index}`}
            secondary="The BPI scan timer populates this tab from member point and figure signals. Data appears after the first successful scan."
          />
        )}
      </div>

      {payload ? (
        <div className="breadth-history-block" data-testid="bpi-chart-section">
          <HistoryRangeChips
            active={activePreset}
            onChange={(slug) => {
              setCustomRange(null);
              setPreset(slug);
            }}
            maxSessions={total}
            ariaLabel="Bullish percent chart range"
            dataTestId="bpi-range-chips"
          />
          <BpiChart entries={slice} thresholds={payload.thresholds} indexSymbol={payload.index_symbol} />
          {total >= 2 && (
            <BrushMinimap
              values={entries.map((e) => e.bpi)}
              range={chartRange}
              onRangeChange={(range) => setCustomRange(range)}
              onCustom={() => setPreset("custom")}
              testIdPrefix="bpi-brush"
              ariaLabel="Bullish percent history brush"
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
            {CHART_FOOTNOTE}
          </div>
        </div>
      ) : null}
    </>
  );
}
