"use client";

import Link from "next/link";
import { Loader2, Telescope } from "lucide-react";
import InfoTooltip from "./InfoTooltip";
import ScannerTickerSearch from "./ScannerTickerSearch";
import SectionEmptyState from "./SectionEmptyState";
import { SigMeter } from "./SigMeter";
import SortTh from "./SortTh";
import { useSort } from "@/lib/useSort";
import type { LeapData, LeapResult } from "@/lib/types";

type LeapSortKey = "ticker" | "price" | "iv" | "iv_rank" | "hv_20" | "hv_252" | "leaps" | "best_gap";

type LeapScannerProps = {
  data: LeapData | null;
  loading?: boolean;
  scanning?: boolean;
  error?: string | null;
  lastSync?: string | null;
  onScan?: () => void;
  onTickerScan?: (tickers: string[]) => void;
};

const LEAP_SECTION_HELP =
  "Long-dated IV mispricing scan: tickers where LEAP implied vol trades below realized vol. best_gap is the headline signal (HV − IV in vol points); MISPRICED rows clear the scan's own gap threshold.";

function extract(row: LeapResult, key: LeapSortKey): string | number | null {
  switch (key) {
    case "ticker": return row.ticker;
    case "price": return row.price;
    case "iv": return row.current_iv;
    case "iv_rank": return row.iv_rank;
    case "hv_20": return row.hv_20;
    case "hv_252": return row.hv_252;
    case "leaps": return row.leap_count;
    case "best_gap": return row.best_gap;
    default: return null;
  }
}

function fmt(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return "---";
  return value.toFixed(digits);
}

function signed(value: number, digits = 1): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

export default function LeapScanner({
  data,
  loading = false,
  scanning = false,
  error = null,
  lastSync = null,
  onScan,
  onTickerScan,
}: LeapScannerProps) {
  const rows = data?.results ?? [];
  const { sorted, sort, toggle } = useSort(rows, extract);
  const mispricedCount = rows.filter((r) => r.is_mispriced).length;

  return (
    <section className="section leap-scanner" data-testid="leap-scanner-section">
      <div className="section-header">
        <div className="section-title">
          <Telescope size={14} />
          LEAP IV Mispricing
          <InfoTooltip
            text={LEAP_SECTION_HELP}
            ariaLabel="LEAP scanner details"
            triggerTestId="leap-scanner-title-tooltip"
            contentTestId="leap-scanner-title-tooltip-content"
          />
        </div>
        <div className="theta-harvester__meta">
          {lastSync && <span className="report-meta">{new Date(lastSync).toLocaleTimeString()}</span>}
          <span className="pill defined">{mispricedCount} MISPRICED</span>
          {onTickerScan && (
            <ScannerTickerSearch
              id="leap-ticker-search"
              scanning={scanning}
              onTickerScan={onTickerScan}
            />
          )}
          {onScan && (
            <button
              type="button"
              className="theta-search__button"
              onClick={onScan}
              disabled={scanning}
            >
              {scanning ? <Loader2 size={12} className="spin" /> : null}
              {scanning ? "Scanning…" : "Run scan"}
            </button>
          )}
        </div>
      </div>
      <div className="section-body">
        {error ? (
          <div className="alert-item bearish">{error}</div>
        ) : loading && rows.length === 0 ? (
          <div className="report-meta">Sampling…</div>
        ) : rows.length === 0 && data?.universe === "explicit" ? (
          <SectionEmptyState
            icon={Telescope}
            headline="Scan complete: no qualifying setups"
            secondary={`0 of ${data.requested_tickers?.length ?? 0} requested tickers qualified (${(data.requested_tickers ?? []).join(", ")}). Adjust the list or run the preset scan.`}
            action={onScan ? { label: "Run scan", onClick: onScan } : undefined}
          />
        ) : rows.length === 0 ? (
          <SectionEmptyState
            icon={Telescope}
            headline="No LEAP scan on file"
            secondary="Run a scan, or wait for the scheduled daily refresh (radon-leap.timer, 14:00 UTC trading days)."
            action={onScan ? { label: "Run scan", onClick: onScan } : undefined}
          />
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <SortTh<LeapSortKey> label="Ticker" sortKey="ticker" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<LeapSortKey> label="Price" sortKey="price" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<LeapSortKey> label="IV" sortKey="iv" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} helpText="Current LEAP at-the-money implied volatility." helpAriaLabel="IV details" />
                  <SortTh<LeapSortKey> label="IV Rank" sortKey="iv_rank" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} helpText="Where current IV sits in its 1-year range. Low rank + positive gap = cheap long-dated optionality." helpAriaLabel="IV rank details" />
                  <SortTh<LeapSortKey> label="HV20" sortKey="hv_20" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<LeapSortKey> label="HV252" sortKey="hv_252" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<LeapSortKey> label="LEAPs" sortKey="leaps" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<LeapSortKey> label="Best Gap" sortKey="best_gap" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} helpText="Headline signal: realized vol minus LEAP IV in vol points. Positive = options priced below realized movement." helpAriaLabel="Best gap details" />
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((r) => (
                  <tr key={r.ticker}>
                    <td>
                      <Link href={`/${encodeURIComponent(r.ticker)}`} className="ticker-link">
                        {r.ticker}
                      </Link>
                    </td>
                    <td className="right">{r.price != null ? `$${fmt(r.price, 2)}` : "---"}</td>
                    <td className="right">{fmt(r.current_iv)}</td>
                    <td className="right">
                      {fmt(r.iv_rank)}
                      <SigMeter value={r.iv_rank ?? null} tone={r.is_mispriced ? "pos" : "mut"} />
                    </td>
                    <td className="right">{fmt(r.hv_20)}</td>
                    <td className="right">{fmt(r.hv_252)}</td>
                    <td className="right">{r.leap_count}</td>
                    <td className="right">{signed(r.best_gap)}</td>
                    <td>
                      <span className={`pill ${r.is_mispriced ? "defined" : "undefined"}`}>
                        {r.is_mispriced ? "MISPRICED" : "FAIR"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
