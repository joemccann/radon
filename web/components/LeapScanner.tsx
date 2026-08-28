"use client";

import Link from "next/link";
import { Loader2, Telescope } from "lucide-react";
import InfoTooltip from "./InfoTooltip";
import ScannerInstrumentShell from "./ScannerInstrumentShell";
import ScannerTickerSearch from "./ScannerTickerSearch";
import SectionEmptyState from "./SectionEmptyState";
import { SigMeter } from "./SigMeter";
import SortTh from "./SortTh";
import { useSort } from "@/lib/useSort";
import { serializeLegsParam } from "@/lib/useChainUrlState";
import { formatExpiry } from "@/lib/optionsChainUtils";
import type { LeapData, LeapResult } from "@/lib/types";

type LeapSortKey = "ticker" | "price" | "iv" | "iv_rank" | "hv_20" | "hv_252" | "leaps" | "best_gap" | "status";

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

/**
 * Deep-link a row's headline LEAP into the chain order builder, prefilled as a
 * long call. Mirrors `thetaOrderHref`: `?deck=c` opens the chain deck, `legs`
 * seeds the builder. Null for scans written before the scanner emitted
 * contract detail — a gap alone cannot address a strike.
 */
export function leapOrderHref(row: LeapResult): string | null {
  const contract = row.best_leap;
  if (!contract) return null;
  const legs = serializeLegsParam([
    { action: "BUY", quantity: 1, strike: contract.strike, right: contract.right },
  ]);
  if (!legs) return null;
  const params = new URLSearchParams({
    deck: "c",
    expiry: formatExpiry(contract.expiry),
    strikes: "100",
    legs,
    src: "leap",
  });
  return `/${encodeURIComponent(row.ticker.toUpperCase())}?${params.toString()}`;
}

function contractLabel(row: LeapResult): string {
  const contract = row.best_leap;
  if (!contract) return row.ticker.toUpperCase();
  return `${row.ticker.toUpperCase()} ${contract.strike}${contract.right}`;
}

function widestMispriced(rows: LeapResult[]): LeapResult | null {
  return rows.reduce<LeapResult | null>((best, row) => {
    if (!row.is_mispriced || !row.best_leap) return best;
    return best == null || row.best_gap > best.best_gap ? row : best;
  }, null);
}

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
    case "status": return row.is_mispriced ? "MISPRICED" : "FAIR";
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
  const { sorted, sort, toggle } = useSort(rows, extract, "best_gap", "desc");
  const mispricedCount = rows.filter((r) => r.is_mispriced).length;
  const bestRow = widestMispriced(rows);
  const bestHref = bestRow ? leapOrderHref(bestRow) : null;

  return (
    <ScannerInstrumentShell
      moduleId="LEAP / 05"
      title="LEAP IV Mispricing"
      titleAccessory={
        <InfoTooltip
          text={LEAP_SECTION_HELP}
          ariaLabel="LEAP scanner details"
          triggerTestId="leap-scanner-title-tooltip"
          contentTestId="leap-scanner-title-tooltip-content"
        />
      }
      controls={
        <div className="theta-harvester__meta">
          {lastSync && <span className="report-meta">{new Date(lastSync).toLocaleTimeString()}</span>}
          <span className="pill defined">{mispricedCount} MISPRICED</span>
          {bestRow && bestHref && (
            <Link
              href={bestHref}
              className="theta-scan-button"
              data-testid="leap-best-order-link"
              title={`Open the ${contractLabel(bestRow)} LEAP in the order builder`}
            >
              TRADE BEST · {contractLabel(bestRow)}
            </Link>
          )}
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
              className="theta-scan-button"
              onClick={onScan}
              disabled={scanning}
            >
              {scanning ? <Loader2 size={12} className="spin" /> : null}
              {scanning ? "SCANNING" : "SCAN"}
            </button>
          )}
        </div>
      }
      rail={[
        { k: "engine", v: "leap.iv.mispricing" },
        { k: "universe", v: data?.universe ?? "—" },
        { k: "last.sample", v: lastSync ? new Date(lastSync).toLocaleTimeString() : "—" },
        { k: "mispriced", v: String(mispricedCount) },
      ]}
      className="leap-scanner"
      testId="leap-scanner-section"
    >
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
            secondary="Run a scan, or wait for the scheduled large-cap universe refresh (radon-leap.timer, trading days)."
            action={onScan ? { label: "Run scan", onClick: onScan } : undefined}
          />
        ) : (
          <div className="table-wrap">
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
                  <SortTh<LeapSortKey> label="Status" sortKey="status" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                </tr>
              </thead>
              <tbody>
                {sorted.map((r) => {
                  const orderHref = leapOrderHref(r);
                  return (
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
                      <td className="right">
                        {orderHref ? (
                          <Link
                            href={orderHref}
                            className="ticker-link"
                            data-testid={`leap-order-link-${r.ticker}`}
                            title={`Open the ${contractLabel(r)} LEAP in the order builder`}
                          >
                            {signed(r.best_gap)}
                          </Link>
                        ) : (
                          signed(r.best_gap)
                        )}
                      </td>
                      <td>
                        <span className={`pill ${r.is_mispriced ? "defined" : "undefined"}`}>
                          {r.is_mispriced ? "MISPRICED" : "FAIR"}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </ScannerInstrumentShell>
  );
}
