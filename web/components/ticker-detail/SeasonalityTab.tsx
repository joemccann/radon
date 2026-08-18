"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CalendarRange } from "lucide-react";
import SectionEmptyState from "@/components/SectionEmptyState";
import SortTh from "@/components/SortTh";
import { useSort } from "@/lib/useSort";

type SeasonSortKey = "month" | "avg" | "median" | "best" | "worst" | "win" | "rating";

/* ─── Types matching UW /api/seasonality/{ticker}/monthly ─── */

type MonthData = {
  month: number;
  avg_change: number;
  median_change: number;
  max_change: number;
  min_change: number;
  positive_closes: number;
  positive_months_perc: number;
  years: number;
};

type SeasonalityTabProps = {
  ticker: string;
  active: boolean;
};

const MONTH_LABELS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

const MONTH_FULL = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

type Rating = "FAVORABLE" | "NEUTRAL" | "UNFAVORABLE";

function rateMonth(winRate: number, _avgReturn: number): Rating {
  // winRate is decimal (0.65 = 65%)
  if (winRate > 0.6) return "FAVORABLE";
  if (winRate < 0.5) return "UNFAVORABLE";
  return "NEUTRAL";
}

function ratingClass(rating: Rating): string {
  if (rating === "FAVORABLE") return "seasonality-favorable";
  if (rating === "UNFAVORABLE") return "seasonality-unfavorable";
  return "seasonality-neutral";
}

function overallRating(months: MonthData[]): { rating: Rating; favorable: number; unfavorable: number } {
  const observed = months.filter((month) => month.years > 0);
  let favorable = 0;
  let unfavorable = 0;
  for (const m of observed) {
    const r = rateMonth(m.positive_months_perc, m.avg_change);
    if (r === "FAVORABLE") favorable++;
    if (r === "UNFAVORABLE") unfavorable++;
  }
  const rating: Rating = observed.length < 6
    ? "NEUTRAL"
    : favorable >= 6
      ? "FAVORABLE"
      : unfavorable >= 6
        ? "UNFAVORABLE"
        : "NEUTRAL";
  return { rating, favorable, unfavorable };
}

function seasonExtract(m: MonthData, key: SeasonSortKey): string | number | null {
  const hasData = m.years > 0;
  switch (key) {
    case "month": return m.month;
    case "avg": return hasData ? m.avg_change : null;
    case "median": return hasData ? m.median_change : null;
    case "best": return hasData ? m.max_change : null;
    case "worst": return hasData ? m.min_change : null;
    case "win": return hasData ? m.positive_months_perc : null;
    case "rating": return hasData ? rateMonth(m.positive_months_perc, m.avg_change) : null;
    default: return null;
  }
}

function SeasonalityDetailTable({ months, currentMonth }: { months: MonthData[]; currentMonth: number }) {
  const { sorted, sort, toggle } = useSort(months, seasonExtract, "month", "asc");
  return (
    <table className="pos-legs-table">
      <thead>
        <tr>
          <SortTh<SeasonSortKey> label="Month" sortKey="month" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <SortTh<SeasonSortKey> label="Avg" sortKey="avg" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <SortTh<SeasonSortKey> label="Median" sortKey="median" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <SortTh<SeasonSortKey> label="Best" sortKey="best" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <SortTh<SeasonSortKey> label="Worst" sortKey="worst" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <SortTh<SeasonSortKey> label="Win Rate" sortKey="win" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <SortTh<SeasonSortKey> label="Rating" sortKey="rating" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
        </tr>
      </thead>
      <tbody>
        {sorted.map((m) => {
          const hasData = m.years > 0;
          const monthRating = hasData ? rateMonth(m.positive_months_perc, m.avg_change) : "NEUTRAL" as Rating;
          const isCurrent = m.month === currentMonth;
          return (
            <tr key={m.month} className={`${isCurrent ? "seasonality-row-current" : ""} ${!hasData ? "seasonality-row-nodata" : ""}`}>
              <td>
                {MONTH_FULL[m.month - 1]}
                {isCurrent && <span className="seasonality-now">NOW</span>}
              </td>
              {hasData ? (
                <>
                  <td className={m.avg_change >= 0 ? "positive" : "negative"}>{fmtPct(m.avg_change)}</td>
                  <td className={m.median_change >= 0 ? "positive" : "negative"}>{fmtPct(m.median_change)}</td>
                  <td className="positive">{fmtPct(m.max_change)}</td>
                  <td className="negative">{fmtPct(m.min_change)}</td>
                  <td className={m.positive_months_perc > 0.6 ? "positive" : m.positive_months_perc < 0.5 ? "negative" : ""}>{fmtWinRate(m.positive_months_perc)}</td>
                  <td><span className={`seasonality-table-badge ${ratingClass(monthRating)}`}>{monthRating}</span></td>
                </>
              ) : (
                <td colSpan={6} style={{ color: "var(--text-muted)", fontStyle: "italic" }}>No data</td>
              )}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** Format decimal as percentage string: 0.0534 -> "+5.3%" */
function fmtPct(val: number): string {
  const pct = val * 100;
  const sign = pct >= 0 ? "+" : "";
  return `${sign}${pct.toFixed(1)}%`;
}

/** Format win rate: 0.6667 -> "66.7%" */
function fmtWinRate(val: number): string {
  return `${(val * 100).toFixed(1)}%`;
}

/** Bar width for the heatmap bar (clamped 5-100%) */
function barWidth(absReturn: number): number {
  // Scale: 10% return = full bar. Minimum 5% width for visibility.
  return Math.max(5, Math.min(100, Math.abs(absReturn) * 100 * 10));
}

type DataSource = "uw" | "uw+equityclock" | "equityclock" | null;

export default function SeasonalityTab({ ticker, active }: SeasonalityTabProps) {
  const [months, setMonths] = useState<MonthData[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fetched, setFetched] = useState(false);
  const [source, setSource] = useState<DataSource>(null);
  const [resolvedTicker, setResolvedTicker] = useState<string | null>(null);
  const requestGenerationRef = useRef(0);

  const fetchSeasonality = useCallback(async (signal: AbortSignal, generation: number) => {
    setLoading(true);
    setError(null);
    setMonths([]);
    setSource(null);
    setFetched(false);
    setResolvedTicker(null);
    try {
      const res = await fetch(`/api/ticker/seasonality?ticker=${encodeURIComponent(ticker)}`, { signal });
      const json = await res.json();
      if (signal.aborted || generation !== requestGenerationRef.current) return;
      if (!res.ok) throw new Error(json.error || `Failed (${res.status})`);
      setSource(json.source ?? null);
      const items = json.data ?? json ?? [];
      if (!Array.isArray(items) || items.length === 0) {
        setMonths([]);
      } else {
        // Build a map of returned months, then ensure all 12 are present
        const byMonth = new Map<number, MonthData>();
        for (const item of items) {
          byMonth.set(item.month, item);
        }
        const all12: MonthData[] = [];
        for (let m = 1; m <= 12; m++) {
          all12.push(byMonth.get(m) ?? {
            month: m,
            avg_change: 0,
            median_change: 0,
            max_change: 0,
            min_change: 0,
            positive_closes: 0,
            positive_months_perc: 0,
            years: 0,
          });
        }
        setMonths(all12);
      }
    } catch (err) {
      if (signal.aborted || generation !== requestGenerationRef.current) return;
      setError(err instanceof Error ? err.message : "Failed to fetch seasonality");
    } finally {
      if (signal.aborted || generation !== requestGenerationRef.current) return;
      setLoading(false);
      setFetched(true);
      setResolvedTicker(ticker);
    }
  }, [ticker]);

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    const generation = ++requestGenerationRef.current;
    void fetchSeasonality(controller.signal, generation);
    return () => controller.abort();
  }, [active, fetchSeasonality]);

  const isCurrentTicker = resolvedTicker === ticker;

  if (loading || (active && !isCurrentTicker)) {
    return (
      <div className="tab-loading">
        <div className="tab-loading-text">Loading seasonality...</div>
      </div>
    );
  }

  if (isCurrentTicker && error) {
    return <div className="tab-error">{error}</div>;
  }

  if (isCurrentTicker && fetched && months.length === 0) {
    return (
      <div className="tab-empty">
        <SectionEmptyState
          icon={CalendarRange}
          headline={`No seasonality data for ${ticker}`}
          variant="compact"
        />
      </div>
    );
  }

  if (!isCurrentTicker || months.length === 0) return null;

  const currentMonth = new Date().getMonth() + 1; // 1-indexed
  const { rating, favorable, unfavorable } = overallRating(months);
  const yearsAnalyzed = months[0]?.years ?? 0;

  return (
    <div className="seasonality-tab">
      {/* Overall assessment header */}
      <div className="seasonality-header">
        <span className={`seasonality-rating-pill ${ratingClass(rating)}`}>
          {rating}
        </span>
        <div className="seasonality-summary">
          <span className="seasonality-stat">{favorable} favorable</span>
          <span className="seasonality-stat">{unfavorable} unfavorable</span>
          {yearsAnalyzed > 0 && (
            <span className="seasonality-stat">{yearsAnalyzed}y history</span>
          )}
          {source && source !== "uw" && (
            <span className="seasonality-source-badge">
              {source === "equityclock" ? "EQUITYCLOCK" : "UW + EQUITYCLOCK"}
            </span>
          )}
        </div>
      </div>

      {/* Monthly grid */}
      <div className="seasonality-grid">
        {months.map((m) => {
          const hasData = m.years > 0;
          const monthRating = hasData ? rateMonth(m.positive_months_perc, m.avg_change) : "NEUTRAL" as Rating;
          const isCurrent = m.month === currentMonth;
          const isPositive = m.avg_change >= 0;

          return (
            <div
              key={m.month}
              className={`seasonality-cell ${isCurrent ? "seasonality-cell-current" : ""} ${!hasData ? "seasonality-cell-nodata" : ""}`}
            >
              <div className="seasonality-cell-month">
                {MONTH_LABELS[m.month - 1]}
                {isCurrent && <span className="seasonality-now">NOW</span>}
              </div>

              {hasData ? (
                <>
                  <div className="seasonality-cell-bar-wrap">
                    <div
                      className={`seasonality-cell-bar ${isPositive ? "seasonality-bar-positive" : "seasonality-bar-negative"}`}
                      style={{
                        transform: `scaleX(${Math.max(0, Math.min(1, barWidth(m.avg_change) / 100))})`,
                        transformOrigin: "left center",
                      }}
                    />
                  </div>
                  <div className={`seasonality-cell-return ${isPositive ? "positive" : "negative"}`}>
                    {fmtPct(m.avg_change)}
                  </div>
                  <div className="seasonality-cell-winrate">
                    {fmtWinRate(m.positive_months_perc)} win
                  </div>
                  <div className={`seasonality-cell-badge ${ratingClass(monthRating)}`}>
                    {monthRating.charAt(0)}
                  </div>
                </>
              ) : (
                <div className="seasonality-cell-nodata-text">No data</div>
              )}
            </div>
          );
        })}
      </div>

      {/* Detail table */}
      <div className="seasonality-detail">
        <div className="seasonality-detail-title">Monthly Detail</div>
        <SeasonalityDetailTable months={months} currentMonth={currentMonth} />
      </div>

      {/* Legend */}
      <div className="seasonality-legend">
        <span className="seasonality-legend-item">
          <span className="seasonality-legend-dot seasonality-favorable" /> FAVORABLE: win rate &gt;60%
        </span>
        <span className="seasonality-legend-item">
          <span className="seasonality-legend-dot seasonality-neutral" /> NEUTRAL: 50-60% win rate
        </span>
        <span className="seasonality-legend-item">
          <span className="seasonality-legend-dot seasonality-unfavorable" /> UNFAVORABLE: win rate &lt;50%
        </span>
      </div>
    </div>
  );
}
