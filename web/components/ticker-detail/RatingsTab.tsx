"use client";

import { useCallback, useEffect, useState } from "react";
import { fmtPrice } from "@/lib/positionUtils";

/* ─── Types matching the actual API response ─── */

type RatingsBreakdown = {
  strong_buy?: number;
  buy?: number;
  hold?: number;
  sell?: number;
  strong_sell?: number;
  total?: number;
  buy_pct?: number;
  sell_pct?: number;
};

type PriceTarget = {
  mean?: number;
  high?: number;
  low?: number;
  median?: number;
  count?: number;
};

type UpgradeEntry = {
  date?: string;
  firm: string;
  action: string;
  to_grade?: string;
  from_grade?: string;
};

type RawRatingsData = {
  ticker: string;
  recommendation?: string | null;
  analyst_count?: number;
  ratings?: RatingsBreakdown | null;
  target_price?: PriceTarget | number | null;
  recent_changes?: UpgradeEntry[];
  upgrade_downgrade_history?: UpgradeEntry[];
  source?: string;
  error?: string | null;
  // Flat fields (legacy/alternative shape)
  buy_count?: number;
  hold_count?: number;
  sell_count?: number;
  strong_buy_count?: number;
  strong_sell_count?: number;
  price_target_low?: number;
  price_target_high?: number;
  price_target_mean?: number;
  price_target_median?: number;
  [key: string]: unknown;
};

type RatingsTabProps = {
  ticker: string;
  active: boolean;
  currentPrice?: number | null;
};

function gradePillClass(grade: string): string {
  const g = grade.toLowerCase();
  if (g.includes("buy") || g.includes("outperform") || g.includes("overweight")) return "bullish";
  if (g.includes("sell") || g.includes("underperform") || g.includes("underweight")) return "bearish";
  return "neutral";
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
}

export default function RatingsTab({ ticker, active, currentPrice }: RatingsTabProps) {
  const [data, setData] = useState<RawRatingsData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fetched, setFetched] = useState(false);

  const fetchRatings = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/ticker/ratings?ticker=${encodeURIComponent(ticker)}`);
      if (!res.ok) {
        const json = await res.json().catch(() => ({}));
        throw new Error(json.error || `Failed to fetch ratings (${res.status})`);
      }
      const json = await res.json();
      const item = Array.isArray(json) ? json[0] : json;
      if (item?.error) throw new Error(item.error);
      setData(item ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch ratings");
    } finally {
      setLoading(false);
      setFetched(true);
    }
  }, [ticker]);

  useEffect(() => {
    if (active && !fetched) fetchRatings();
  }, [active, fetched, fetchRatings]);

  if (loading) {
    return <div className="tab-loading"><div className="tab-loading-text">Loading ratings...</div></div>;
  }
  if (error) {
    return <div className="tab-error">{error}</div>;
  }
  if (!data) {
    return <div className="tab-empty">No analyst data for {ticker}</div>;
  }

  // Normalize: handle both nested (UW) and flat (legacy) shapes
  const r = data.ratings;
  const strongBuy = r?.strong_buy ?? data.strong_buy_count ?? 0;
  const buy = r?.buy ?? data.buy_count ?? 0;
  const hold = r?.hold ?? data.hold_count ?? 0;
  const sell = r?.sell ?? data.sell_count ?? 0;
  const strongSell = r?.strong_sell ?? data.strong_sell_count ?? 0;
  const total = strongBuy + buy + hold + sell + strongSell;
  const buyPct = r?.buy_pct ?? (total > 0 ? Math.round(((strongBuy + buy) / total) * 100) : null);
  const sellPct = r?.sell_pct ?? (total > 0 ? Math.round(((sell + strongSell) / total) * 100) : null);
  const analystCount = data.analyst_count ?? r?.total ?? total;

  // Price targets: nested object or flat fields
  const tp = typeof data.target_price === "object" && data.target_price !== null ? data.target_price as PriceTarget : null;
  const ptLow = tp?.low ?? data.price_target_low;
  const ptHigh = tp?.high ?? data.price_target_high;
  const ptMean = tp?.mean ?? data.price_target_mean;
  const ptMedian = tp?.median ?? data.price_target_median;
  const hasPriceTargets = ptLow != null || ptHigh != null || ptMean != null || ptMedian != null;

  // Upside/downside from current price to mean target
  const upsideDownside = currentPrice != null && currentPrice > 0 && ptMean != null
    ? ((ptMean - currentPrice) / currentPrice) * 100
    : null;

  // Upgrade/downgrade history (try both field names)
  const changes = (data.upgrade_downgrade_history ?? data.recent_changes ?? []).slice(0, 10);

  const rec = data.recommendation;

  return (
    <div className="ratings-tab">
      {/* Recommendation + summary */}
      <div className="ratings-header">
        {rec && (
          <span className={`ratings-rec-pill ${gradePillClass(rec)}`}>
            {rec.toUpperCase()}
          </span>
        )}
        <div className="ratings-summary">
          {analystCount > 0 && <span className="ratings-count">{analystCount} analysts</span>}
          {buyPct != null && <span className="ratings-pct bullish">{buyPct}% buy</span>}
          {sellPct != null && sellPct > 0 && <span className="ratings-pct bearish">{sellPct}% sell</span>}
        </div>
      </div>

      {/* Ratings distribution bar */}
      {total > 0 && (
        <div className="ratings-bar-wrap">
          <div className="ratings-bar">
            {strongBuy > 0 && <div className="ratings-bar-seg ratings-strong-buy" style={{ flex: strongBuy }}>{strongBuy}</div>}
            {buy > 0 && <div className="ratings-bar-seg ratings-buy" style={{ flex: buy }}>{buy}</div>}
            {hold > 0 && <div className="ratings-bar-seg ratings-hold" style={{ flex: hold }}>{hold}</div>}
            {sell > 0 && <div className="ratings-bar-seg ratings-sell" style={{ flex: sell }}>{sell}</div>}
            {strongSell > 0 && <div className="ratings-bar-seg ratings-strong-sell" style={{ flex: strongSell }}>{strongSell}</div>}
          </div>
          <div className="ratings-bar-labels">
            <span>Strong Buy</span>
            <span>Buy</span>
            <span>Hold</span>
            <span>Sell</span>
            <span>Strong Sell</span>
          </div>
        </div>
      )}

      {/* Price targets */}
      {hasPriceTargets && (
        <div className="ratings-targets">
          <div className="ratings-targets-title">Price Targets</div>
          <div className="ratings-targets-grid">
            {ptLow != null && (
              <div className="pos-stat">
                <span className="pos-stat-label">Low</span>
                <span className="pos-stat-value">{fmtPrice(ptLow)}</span>
              </div>
            )}
            {ptMedian != null && (
              <div className="pos-stat">
                <span className="pos-stat-label">Median</span>
                <span className="pos-stat-value">{fmtPrice(ptMedian)}</span>
              </div>
            )}
            {ptMean != null && (
              <div className="pos-stat">
                <span className="pos-stat-label">Mean</span>
                <span className="pos-stat-value">{fmtPrice(ptMean)}</span>
              </div>
            )}
            {ptHigh != null && (
              <div className="pos-stat">
                <span className="pos-stat-label">High</span>
                <span className="pos-stat-value">{fmtPrice(ptHigh)}</span>
              </div>
            )}
            {currentPrice != null && (
              <div className="pos-stat">
                <span className="pos-stat-label">Current</span>
                <span className="pos-stat-value">{fmtPrice(currentPrice)}</span>
              </div>
            )}
            {upsideDownside != null && (
              <div className="pos-stat">
                <span className="pos-stat-label">vs Mean</span>
                <span className={`pos-stat-value ${upsideDownside >= 0 ? "positive" : "negative"}`}>
                  {upsideDownside >= 0 ? "+" : ""}{upsideDownside.toFixed(1)}%
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Upgrade/downgrade history */}
      {changes.length > 0 && (
        <div className="ratings-changes">
          <div className="ratings-targets-title">Recent Analyst Actions</div>
          <table className="pos-legs-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Firm</th>
                <th>Action</th>
                <th>Rating</th>
              </tr>
            </thead>
            <tbody>
              {changes.map((c, i) => {
                const grade = c.to_grade || c.from_grade || "";
                return (
                  <tr key={i}>
                    <td>{c.date || "---"}</td>
                    <td>{c.firm}</td>
                    <td>{capitalize(c.action)}</td>
                    <td>
                      {grade && (
                        <span className={gradePillClass(grade)}>
                          {c.from_grade && c.to_grade && c.from_grade !== c.to_grade
                            ? `${capitalize(c.from_grade)} → ${capitalize(c.to_grade)}`
                            : capitalize(grade)}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Source indicator */}
      {data.source && (
        <div className="news-fallback-notice">via {data.source === "uw" ? "Unusual Whales" : data.source}</div>
      )}
    </div>
  );
}
