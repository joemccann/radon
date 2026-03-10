"use client";

import { useCallback, useEffect, useState } from "react";
import type { PriceData, FundamentalsData } from "@/lib/pricesProtocol";

type CompanyData = {
  uw_info: Record<string, unknown>;
  stock_state: Record<string, unknown>;
  profile: Record<string, unknown>;
  stats: Record<string, unknown>;
};

type CompanyTabProps = {
  ticker: string;
  active: boolean;
  priceData: PriceData | null;
  fundamentals: FundamentalsData | null;
};

function fmtMktCap(val: unknown): string {
  if (val == null) return "---";
  const n = typeof val === "string" ? parseFloat(val) : (val as number);
  if (isNaN(n)) return String(val);
  if (n >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (n >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  return `$${n.toLocaleString()}`;
}

function fmtVol(val: unknown): string {
  if (val == null) return "---";
  const n = typeof val === "string" ? parseFloat(String(val).replace(/,/g, "")) : (val as number);
  if (isNaN(n)) return String(val);
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)}K`;
  return n.toLocaleString();
}

function fmtNum(val: unknown): string {
  if (val == null) return "---";
  const s = String(val);
  const n = parseFloat(s.replace(/[$,]/g, ""));
  if (isNaN(n)) return s;
  return `$${n.toFixed(2)}`;
}

export default function CompanyTab({ ticker, active, priceData, fundamentals }: CompanyTabProps) {
  const [data, setData] = useState<CompanyData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fetched, setFetched] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const fetchInfo = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/ticker/info?ticker=${encodeURIComponent(ticker)}`);
      if (!res.ok) {
        const json = await res.json().catch(() => ({}));
        throw new Error(json.error || `Failed to fetch info (${res.status})`);
      }
      setData(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch company info");
    } finally {
      setLoading(false);
      setFetched(true);
    }
  }, [ticker]);

  useEffect(() => {
    if (active && !fetched) fetchInfo();
  }, [active, fetched, fetchInfo]);

  if (loading) {
    return <div className="tab-loading"><div className="tab-loading-text">Loading company info...</div></div>;
  }
  if (error) {
    return <div className="tab-error">{error}</div>;
  }
  if (!data) {
    return <div className="tab-empty">No data for {ticker}</div>;
  }

  const info = data.uw_info;
  const state = data.stock_state;
  const profile = data.profile;
  const stats = data.stats;

  const name = (info.name ?? info.full_name ?? info.company_name ?? "") as string;
  const description = (info.description ?? "") as string;
  const sector = (info.sector ?? "") as string;
  const issueType = (info.issue_type ?? "") as string;

  // Profile fields from Exa/Robinhood
  const ceo = profile.ceo as string | undefined;
  const employees = profile.employees as string | undefined;
  const headquarters = profile.headquarters as string | undefined;
  const founded = profile.founded as string | undefined;

  // Key stats — IB fundamentals (tick 258) → IB price ticks (tick 165) → UW → Exa → Yahoo
  const p = priceData;
  const f = fundamentals;
  const marketCap = info.marketcap ?? info.market_cap;
  const beta = info.beta;
  const avgVolume = p?.avgVolume ?? info.avg30_volume ?? stats.avg_volume;
  const nextEarnings = info.next_earnings_date;
  const peRatio = f?.peRatio ?? stats.pe_ratio;
  const dividendYield = f?.dividendYield ?? stats.dividend_yield;
  const eps = f?.eps;
  const week52High = f?.week52High ?? p?.week52High ?? stats.week_52_high;
  const week52Low = f?.week52Low ?? p?.week52Low ?? stats.week_52_low;

  // Today's state from stock-state
  const todayOpen = state.open;
  const todayHigh = state.high;
  const todayLow = state.low;
  const todayVolume = state.volume ?? state.full_day_volume;

  // Stat items
  const statItems: { label: string; value: string }[] = [
    { label: "Market Cap", value: fmtMktCap(marketCap) },
    { label: "P/E Ratio", value: peRatio != null && !isNaN(Number(peRatio)) ? Number(peRatio).toFixed(2) : "---" },
    { label: "EPS", value: eps != null && !isNaN(Number(eps)) ? `$${Number(eps).toFixed(2)}` : "---" },
    { label: "Div Yield", value: dividendYield != null && !isNaN(Number(dividendYield)) ? `${Number(dividendYield).toFixed(2)}%` : "---" },
    { label: "Avg Volume", value: fmtVol(avgVolume) },
    { label: "High Today", value: todayHigh != null ? fmtNum(todayHigh) : "---" },
    { label: "Low Today", value: todayLow != null ? fmtNum(todayLow) : "---" },
    { label: "Open", value: todayOpen != null ? fmtNum(todayOpen) : "---" },
    { label: "Volume", value: fmtVol(todayVolume) },
    { label: "52W High", value: week52High != null ? fmtNum(week52High) : "---" },
    { label: "52W Low", value: week52Low != null ? fmtNum(week52Low) : "---" },
    { label: "Next Earnings", value: nextEarnings != null ? String(nextEarnings) : "---" },
    { label: "Beta", value: beta != null ? Number(beta).toFixed(2) : "---" },
  ];

  const profileItems: { label: string; value: string }[] = [
    ...(ceo ? [{ label: "CEO", value: ceo }] : []),
    ...(employees ? [{ label: "Employees", value: employees }] : []),
    ...(headquarters ? [{ label: "HQ", value: headquarters }] : []),
    ...(founded ? [{ label: "Founded", value: founded }] : []),
  ];

  return (
    <div className="company-tab">
      {/* About section */}
      <div className="company-about">
        <div className="company-about-header">
          <span className="ratings-targets-title">
            About {ticker}
          </span>
          {(sector || issueType) && (
            <span className="company-meta">
              {sector}{sector && issueType ? " \u00B7 " : ""}{issueType}
            </span>
          )}
        </div>
        {name && <div className="company-name">{name}</div>}
        {description && (
          <div className={`company-description ${expanded ? "expanded" : ""}`}>
            <p>{description}</p>
            {description.length > 200 && (
              <button
                className="company-show-more"
                onClick={() => setExpanded(!expanded)}
              >
                {expanded ? "Show less" : "Show more"}
              </button>
            )}
          </div>
        )}

        {/* Profile grid */}
        {profileItems.length > 0 && (
          <div className="company-profile-grid">
            {profileItems.map((item) => (
              <div key={item.label} className="pos-stat">
                <span className="pos-stat-label">{item.label}</span>
                <span className="pos-stat-value">{item.value}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Key Statistics */}
      <div className="company-stats">
        <div className="ratings-targets-title">Key Statistics</div>
        <div className="company-stats-grid">
          {statItems.map((item) => (
            <div key={item.label} className="pos-stat">
              <span className="pos-stat-label">{item.label}</span>
              <span className="pos-stat-value">{item.value}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
