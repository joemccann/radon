"use client";

import { useState, type FormEvent } from "react";
import Link from "next/link";
import { Loader2, Search, Sparkles } from "lucide-react";
import SectionEmptyState from "./SectionEmptyState";
import SortTh from "./SortTh";
import TickerLink from "./TickerLink";
import { useSort } from "@/lib/useSort";
import type { ThetaHarvesterData, ThetaHarvesterResult } from "@/lib/types";

type ThetaSortKey = "ticker" | "score" | "theta" | "delta" | "iv_edge" | "range" | "dte" | "credit";

type ThetaHarvesterScannerProps = {
  data: ThetaHarvesterData | null;
  loading?: boolean;
  scanning?: boolean;
  error?: string | null;
  lastSync?: string | null;
  onScan?: () => void;
  onTickerScan?: (ticker: string) => void;
};

function extract(row: ThetaHarvesterResult, key: ThetaSortKey): string | number | null {
  switch (key) {
    case "ticker": return row.ticker;
    case "score": return row.score;
    case "theta": return row.structure.theta;
    case "delta": return Math.abs(row.structure.net_delta);
    case "iv_edge": return row.iv_rv_edge;
    case "range": return row.range_score;
    case "dte": return row.structure.dte;
    case "credit": return row.structure.credit ?? null;
    default: return null;
  }
}

function signed(value: number, digits = 1): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function fmtMoney(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "---";
  return `$${value.toFixed(2)}`;
}

function fmtDelta(delta: number): string {
  return `${signed(delta * 100, 1)} sh`;
}

function fmtTheta(theta: number): string {
  return `${signed(theta * 100, 2)}/day`;
}

function verdictLabel(verdict: string): string {
  if (verdict === "THETA_HARVEST") return "TRUE THETA";
  if (verdict === "DIRECTIONAL_DISGUISE") return "DIRECTIONAL";
  return "WATCH";
}

function verdictTone(verdict: string): string {
  if (verdict === "THETA_HARVEST") return "pos";
  if (verdict === "DIRECTIONAL_DISGUISE") return "neg";
  return "warn";
}

function rangeLabel(row: ThetaHarvesterResult): string {
  return row.gates.range_bound ? "RANGE" : "TREND";
}

function dealerLabel(row: ThetaHarvesterResult): string {
  if (row.dealer_support === "SUPPORT") return "SUPPORT";
  if (row.dealer_support === "NO_SUPPORT") return "NO SUPPORT";
  return "UNKNOWN";
}

function structureLabel(row: ThetaHarvesterResult): string {
  const put = row.structure.short_put;
  const call = row.structure.short_call;
  return `${put.strike.toFixed(0)}P / ${call.strike.toFixed(0)}C`;
}

function RowStatus({ row }: { row: ThetaHarvesterResult }) {
  const tone = verdictTone(row.verdict);
  return <span className={`theta-pill theta-pill--${tone}`}>{verdictLabel(row.verdict)}</span>;
}

function GateStrip({ row }: { row: ThetaHarvesterResult }) {
  const gates = [
    ["DELTA", row.gates.delta_near_zero],
    ["IV RICH", row.gates.iv_rich_vs_rv],
    ["DEALER", row.gates.dealer_support],
    ["THETA", row.gates.theta_positive],
  ] as const;
  return (
    <div className="theta-gates" aria-label={`${row.ticker} theta gates`}>
      {gates.map(([label, pass]) => (
        <span key={label} className={`theta-gate${pass ? " theta-gate--pass" : " theta-gate--fail"}`}>
          {pass ? label : `NO ${label}`}
        </span>
      ))}
    </div>
  );
}

export default function ThetaHarvesterScanner({
  data,
  loading = false,
  scanning = false,
  error = null,
  lastSync = null,
  onScan,
  onTickerScan,
}: ThetaHarvesterScannerProps) {
  const [tickerQuery, setTickerQuery] = useState("");
  const [tickerError, setTickerError] = useState<string | null>(null);
  const rows = data?.results ?? [];
  const { sorted, sort, toggle } = useSort(rows, extract);
  const normalizedTicker = tickerQuery.trim().toUpperCase();

  const submitTickerScan = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!onTickerScan || scanning) return;
    if (!/^[A-Z]{1,6}$/.test(normalizedTicker)) {
      setTickerError("Enter 1-6 letters.");
      return;
    }
    setTickerError(null);
    onTickerScan(normalizedTicker);
  };

  return (
    <section className="section theta-harvester" data-testid="theta-harvester-section">
      <div className="section-header">
        <div className="section-title">
          <Sparkles size={14} />
          Theta Harvester
        </div>
        <div className="theta-harvester__meta">
          {lastSync && <span className="report-meta">{new Date(lastSync).toLocaleTimeString()}</span>}
          <span className="pill defined">{data?.theta_harvest_count ?? 0} TRUE THETA</span>
          {onTickerScan && (
            <form className="theta-search" onSubmit={submitTickerScan}>
              <Search size={13} aria-hidden="true" />
              <input
                id="theta-ticker-search"
                className="theta-search__input"
                value={tickerQuery}
                onChange={(event) => {
                  setTickerQuery(event.target.value.toUpperCase());
                  setTickerError(null);
                }}
                placeholder="Ticker"
                autoCapitalize="characters"
                autoComplete="off"
                spellCheck={false}
                aria-label="Ticker symbol"
                aria-invalid={tickerError ? "true" : "false"}
                aria-describedby={tickerError ? "theta-ticker-search-error" : undefined}
              />
              <button
                type="submit"
                className="theta-search__button"
                disabled={scanning || normalizedTicker.length === 0}
              >
                {scanning ? <Loader2 size={12} className="spin" /> : null}
                Scan
              </button>
              {tickerError && (
                <span id="theta-ticker-search-error" className="theta-search__error" role="alert">
                  {tickerError}
                </span>
              )}
            </form>
          )}
          {onScan && (
            <button
              type="button"
              className="theta-scan-button"
              onClick={onScan}
              disabled={scanning}
            >
              <Loader2 size={12} className={scanning ? "spin" : ""} />
              {scanning ? "SCANNING" : "SCAN NDX"}
            </button>
          )}
        </div>
      </div>

      {loading ? (
        <div className="section-body">
          <div className="snapshot-card__empty">Sampling theta surface...</div>
        </div>
      ) : error ? (
        <div className="section-body">
          <div className="alert-item bearish">{error}</div>
        </div>
      ) : rows.length === 0 ? (
        <div className="section-body">
          <SectionEmptyState
            icon={Sparkles}
            headline="No theta harvest candidates"
            secondary="No neutral short-premium setups are measured in the latest scan."
          />
        </div>
      ) : (
        <>
          <div className="section-body table-wrap theta-harvester__table-wrap">
            <table>
              <thead>
                <tr>
                  <SortTh<ThetaSortKey> label="Ticker" sortKey="ticker" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <th>Structure</th>
                  <SortTh<ThetaSortKey> label="Score" sortKey="score" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<ThetaSortKey> label="Theta" sortKey="theta" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<ThetaSortKey> label="Net Delta" sortKey="delta" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<ThetaSortKey> label="IV/RV" sortKey="iv_edge" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <th>Dealer</th>
                  <SortTh<ThetaSortKey> label="Range" sortKey="range" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<ThetaSortKey> label="DTE" sortKey="dte" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<ThetaSortKey> label="Credit" sortKey="credit" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((row) => (
                  <tr key={`theta-${row.ticker}`}>
                    <td><TickerLink ticker={row.ticker} /></td>
                    <td>
                      <span className="mono">SHORT {structureLabel(row)}</span>
                      <GateStrip row={row} />
                    </td>
                    <td className="right">{row.score.toFixed(1)}</td>
                    <td className="right">{fmtTheta(row.structure.theta)}</td>
                    <td className="right">{fmtDelta(row.structure.net_delta)}</td>
                    <td className="right">{signed(row.iv_rv_edge, 1)} pt / {row.iv_rv_ratio.toFixed(2)}x</td>
                    <td>{dealerLabel(row)}</td>
                    <td>{rangeLabel(row)} {Math.round(row.range_score * 100)}%</td>
                    <td className="right">{row.structure.dte}</td>
                    <td className="right">{fmtMoney(row.structure.credit)}</td>
                    <td><RowStatus row={row} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="theta-harvester__cards" data-testid="theta-harvester-mobile-list">
            {sorted.map((row) => (
              <Link key={`theta-card-${row.ticker}`} href={`/${encodeURIComponent(row.ticker)}?tab=chain`} className="theta-card">
                <div className="theta-card__head">
                  <span className="theta-card__ticker">{row.ticker}</span>
                  <RowStatus row={row} />
                </div>
                <div className="theta-card__structure">SHORT {structureLabel(row)} · {row.structure.dte}D</div>
                <div className="theta-card__stats">
                  <span><b>{row.score.toFixed(0)}</b><em>SCORE</em></span>
                  <span><b>{fmtTheta(row.structure.theta)}</b><em>THETA</em></span>
                  <span><b>{fmtDelta(row.structure.net_delta)}</b><em>DELTA</em></span>
                  <span><b>{signed(row.iv_rv_edge, 1)} pt</b><em>IV/RV</em></span>
                </div>
                <GateStrip row={row} />
              </Link>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
