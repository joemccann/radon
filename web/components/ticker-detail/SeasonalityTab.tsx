"use client";

import { useCallback, useEffect, useState } from "react";

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
  let favorable = 0;
  let unfavorable = 0;
  for (const m of months) {
    const r = rateMonth(m.positive_months_perc, m.avg_change);
    if (r === "FAVORABLE") favorable++;
    if (r === "UNFAVORABLE") unfavorable++;
  }
  const rating: Rating = favorable >= 6 ? "FAVORABLE" : unfavorable >= 6 ? "UNFAVORABLE" : "NEUTRAL";
  return { rating, favorable, unfavorable };
}

/** Format decimal as percentage string: 0.0534 -> "+5.3%" */
import { fmtPct as _fmtPctShared } from "@/lib/format";
function fmtPct(val: number): string { return _fmtPctShared(val, 1); }

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

  const fetchSeasonality = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/ticker/seasonality?ticker=${encodeURIComponent(ticker)}`);
      const json = await res.json();
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
      setError(err instanceof Error ? err.message : "Failed to fetch seasonality");
    } finally {
      setLoading(false);
      setFetched(true);
    }
  }, [ticker]);

  useEffect(() => {
    if (active && !fetched) fetchSeasonality();
  }, [active, fetched, fetchSeasonality]);

  if (loading) {
    return (
      <div className="tab-loading">
        <div className="tl-t">Loading seasonality...</div>
      </div>
    );
  }

  if (error) {
    return <div className="tab-error">{error}</div>;
  }

  if (fetched && months.length === 0) {
    return <div className="tab-empty">No seasonality data for {ticker}</div>;
  }

  if (months.length === 0) return null;

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
                      style={{ width: `${barWidth(m.avg_change)}%` }}
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
        <table className="pos-legs-table">
          <thead>
            <tr>
              <th>Month</th>
              <th>Avg</th>
              <th>Median</th>
              <th>Best</th>
              <th>Worst</th>
              <th>Win Rate</th>
              <th>Rating</th>
            </tr>
          </thead>
          <tbody>
            {months.map((m) => {
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
                    <td colSpan={6} className="text-muted" style={{ fontStyle: "italic" }}>No data</td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Legend */}
      <div className="seasonality-legend">
        <span className="sl-i">
          <span className="sl-d seasonality-favorable" /> FAVORABLE: win rate &gt;60%
        </span>
        <span className="sl-i">
          <span className="sl-d seasonality-neutral" /> NEUTRAL: 50-60% win rate
        </span>
        <span className="sl-i">
          <span className="sl-d seasonality-unfavorable" /> UNFAVORABLE: win rate &lt;50%
        </span>
      </div>
    </div>
  );
}
