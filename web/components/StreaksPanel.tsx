"use client";

import { useMemo, useState, type FormEvent, type ReactNode } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Flame } from "lucide-react";
import BrushMinimap from "./BrushMinimap";
import HistoryRangeChips from "./HistoryRangeChips";
import InfoTooltip from "./InfoTooltip";
import MetricCell from "./mobile/MetricCell";
import SectionEmptyState from "./SectionEmptyState";
import SpectralLoader from "./SpectralLoader";
import StreaksChart from "./StreaksChart";
import { RegimeStrip, RegimeStripCell } from "./RegimeStrip";
import { presetRange, type RangePresetSlug } from "@/lib/historyRange";
import { boundedTicker } from "@/lib/requestBounds";
import {
  formatCloseValue,
  formatDayChangePct,
  formatRunsAtOrAbove,
  formatStreakDays,
  formatUpDayPct,
  sourceLabel,
  streakTone,
} from "@/lib/streaks";
import { useStreaks } from "@/lib/useStreaks";
import { useViewport } from "@/lib/useViewport";

const DEFAULT_SYMBOL = "SPY";

const TOOLTIP_COPY =
  "Consecutive daily gains for one symbol: the streak counts sessions with a close above the prior close and resets on any down or flat close. The histogram mirrors the price pane above it; RUNS AT OR ABOVE CURRENT counts how often this symbol has sustained a run at least as long as the live one.";

const INPUT_STYLE: React.CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: "12px",
  textTransform: "uppercase",
  padding: "5px 10px",
  width: "110px",
  background: "var(--bg-panel-raised)",
  border: "1px solid var(--border-dim)",
  borderRadius: "4px",
  color: "var(--text-primary)",
};

function toneColor(tone: "pos" | "mut"): string {
  return tone === "pos" ? "var(--positive)" : "var(--text-muted)";
}

export default function StreaksPanel() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const initialSymbol = boundedTicker(searchParams.get("symbol")) ?? DEFAULT_SYMBOL;

  const [symbol, setSymbol] = useState(initialSymbol);
  const [input, setInput] = useState(initialSymbol);
  const [inputError, setInputError] = useState<string | null>(null);
  const { data, loading, error, refresh } = useStreaks(symbol);
  const { isMobile, hasMounted } = useViewport();
  const compact = hasMounted && isMobile;

  // The full-history histogram is the point of the reference plate.
  const [activeRange, setActiveRange] = useState<RangePresetSlug | "custom">("all");
  const [customRange, setCustomRange] = useState<[number, number] | null>(null);

  // A payload for a previously loaded symbol must not render under the new
  // symbol's header while the swap is in flight.
  const payload = data && data.symbol === symbol ? data : null;
  const series = payload?.series ?? [];
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

  const submitSymbol = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const next = boundedTicker(input);
    if (!next) {
      setInputError("Enter a valid ticker symbol.");
      return;
    }
    setInputError(null);
    setInput(next);
    if (next === symbol) {
      refresh();
      return;
    }
    setActiveRange("all");
    setCustomRange(null);
    setSymbol(next);
    router.replace(`/regime/streaks?symbol=${encodeURIComponent(next)}`, { scroll: false });
  };

  let body: ReactNode;
  if (loading && !payload) {
    body = <SpectralLoader label="Loading daily close series" />;
  } else if (error && !payload) {
    body = (
      <SectionEmptyState
        icon={Flame}
        tone="danger"
        headline="Streak feed unreachable"
        secondary={`The streaks route did not answer for ${symbol}: ${error}`}
      />
    );
  } else if (!payload || payload.missing || !payload.current || !payload.stats || total === 0) {
    body = (
      <SectionEmptyState
        icon={Flame}
        headline={`No daily history for ${symbol}`}
        secondary="IB, Unusual Whales, Robinhood, and Yahoo returned no daily closes for this symbol. Check the symbol and retry once a source is reachable."
      />
    );
  } else {
    const current = payload.current;
    const stats = payload.stats;
    const tone = streakTone(current.streak);
    const changeColor =
      current.day_change_pct != null && current.day_change_pct < 0
        ? "var(--negative)"
        : "var(--positive)";
    const [start, end] = chartRange;
    const slice = series.slice(start, end + 1);

    body = (
      <>
        {compact ? (
          <div className="m-regime-grid2x2" data-testid="streaks-mobile-grid">
            <MetricCell
              label="STREAK"
              value={formatStreakDays(current.streak)}
              tone={tone}
            />
            <MetricCell label="RECORD" value={formatStreakDays(stats.max_streak)} />
            <MetricCell label="RUNS ≥ CUR" value={formatRunsAtOrAbove(stats.runs_ge_current)} />
            <MetricCell label="LAST CLOSE" value={formatCloseValue(current.close)} />
          </div>
        ) : (
          <RegimeStrip>
            <RegimeStripCell
              testId="streaks-strip-current"
              label="CURRENT STREAK"
              value={
                <span style={{ color: toneColor(tone) }}>
                  {formatStreakDays(current.streak)}
                </span>
              }
              sub={<>CONSECUTIVE UP CLOSES</>}
            />
            <RegimeStripCell
              testId="streaks-strip-record"
              label="RECORD STREAK"
              value={formatStreakDays(stats.max_streak)}
              sub={<>LAST HIT {stats.max_streak_end ?? "---"}</>}
            />
            <RegimeStripCell
              testId="streaks-strip-precedent"
              label="RUNS ≥ CURRENT"
              value={formatRunsAtOrAbove(stats.runs_ge_current)}
              sub={<>{stats.runs_total} RUNS ON RECORD · AVG {stats.avg_run ?? "---"}</>}
            />
            <RegimeStripCell
              testId="streaks-strip-updays"
              label="UP DAYS"
              value={formatUpDayPct(stats.up_day_pct)}
              sub={<>SHARE OF SESSIONS CLOSING HIGHER</>}
            />
            <RegimeStripCell
              testId="streaks-strip-last"
              label="LAST SESSION"
              value={
                <>
                  {formatCloseValue(current.close)}{" "}
                  <span style={{ fontSize: "10px", color: changeColor }}>
                    {formatDayChangePct(current.day_change_pct)}
                  </span>
                </>
              }
              sub={<>{payload.last_date ?? "---"} · {sourceLabel(payload.source)}</>}
            />
          </RegimeStrip>
        )}

        <div className="breadth-history-block" data-testid="streaks-chart-section">
          <HistoryRangeChips
            active={activeRange}
            onChange={(slug) => {
              setCustomRange(null);
              setActiveRange(slug);
            }}
            maxSessions={total}
            ariaLabel="Streaks chart range"
            dataTestId="streaks-range-chips"
          />
          <StreaksChart
            points={slice}
            title={`${payload.symbol} DAILY CLOSE VS CONSECUTIVE DAILY GAINS`}
          />
          {total >= 2 && (
            <BrushMinimap
              values={series.map((entry) => entry.close)}
              range={chartRange}
              onRangeChange={(range) => setCustomRange(range)}
              onCustom={() => setActiveRange("custom")}
              testIdPrefix="streaks-brush"
              ariaLabel="Streaks history range brush"
            />
          )}
        </div>
      </>
    );
  }

  return (
    <>
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Flame size={14} />
            Consecutive Daily Gains
            <InfoTooltip text={TOOLTIP_COPY} />
          </div>
          {payload?.scan_time && (
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "9px",
                color: "var(--text-muted)",
              }}
            >
              {new Date(payload.scan_time).toLocaleTimeString("en-US", {
                hour: "numeric",
                minute: "2-digit",
              })}
            </span>
          )}
        </div>

        <form
          data-testid="streaks-form"
          role="search"
          aria-label="Streaks ticker"
          onSubmit={submitSymbol}
          style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "12px" }}
        >
          <input
            id="streaks-symbol-input"
            data-testid="streaks-symbol-input"
            type="text"
            inputMode="text"
            autoComplete="off"
            autoCapitalize="characters"
            maxLength={10}
            placeholder="SPY"
            aria-label="Ticker symbol"
            value={input}
            onChange={(event) => {
              setInput(event.target.value.toUpperCase());
              if (inputError) setInputError(null);
            }}
            style={INPUT_STYLE}
          />
          <button type="submit" className="history-range-chip" disabled={!input.trim()}>
            LOAD
          </button>
          {inputError ? (
            <span
              role="alert"
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: "10px",
                color: "var(--negative)",
              }}
            >
              {inputError}
            </span>
          ) : null}
        </form>

        {body}
      </div>
    </>
  );
}
