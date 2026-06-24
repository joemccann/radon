"use client";

import { useState, type FormEvent } from "react";
import { CheckCircle2, Loader2, Search, ShieldCheck, XCircle } from "lucide-react";
import SectionEmptyState from "./SectionEmptyState";
import SortTh from "./SortTh";
import TickerLink from "./TickerLink";
import { useSort } from "@/lib/useSort";
import type { StrengthConfirmationData, StrengthConfirmationResult } from "@/lib/types";

type StrengthSortKey = "ticker" | "score" | "groups" | "spot" | "verdict";

type StrengthConfirmationScannerProps = {
  data: StrengthConfirmationData | null;
  loading?: boolean;
  scanning?: boolean;
  error?: string | null;
  lastSync?: string | null;
  onScan?: () => void;
  onTickerScan?: (ticker: string) => void;
};

const FACTOR_ORDER = [
  "Q-SCORES",
  "NET GEX",
  "CALL POSITIONING",
  "TERM STRUCTURE",
  "VOLATILITY SMILE",
  "SYSTEMATIC POSITIONING",
  "MARKET BREADTH",
];

const FACTOR_SHORT: Record<string, string> = {
  "Q-SCORES": "Q",
  "NET GEX": "GEX",
  "CALL POSITIONING": "CALL",
  "TERM STRUCTURE": "TERM",
  "VOLATILITY SMILE": "SMILE",
  "SYSTEMATIC POSITIONING": "SYS",
  "MARKET BREADTH": "BREADTH",
};

function extract(row: StrengthConfirmationResult, key: StrengthSortKey): string | number | null {
  switch (key) {
    case "ticker": return row.ticker;
    case "score": return row.score;
    case "groups": return row.groups_passed;
    case "spot": return row.spot;
    case "verdict": return row.verdict;
    default: return null;
  }
}

function fmtPrice(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "---";
  return `$${value.toFixed(value >= 100 ? 2 : 2)}`;
}

function verdictLabel(verdict: string): string {
  if (verdict === "REAL_STRENGTH_CONFIRMED") return "REAL STRENGTH";
  if (verdict === "WATCHLIST") return "WATCHLIST";
  return "WEAK";
}

function verdictTone(verdict: string): string {
  if (verdict === "REAL_STRENGTH_CONFIRMED") return "pos";
  if (verdict === "WATCHLIST") return "warn";
  return "neg";
}

function factorMap(row: StrengthConfirmationResult) {
  return new Map(row.factors.map((factor) => [factor.group, factor]));
}

function failedFactors(row: StrengthConfirmationResult): string {
  const failed = row.factors.filter((factor) => !factor.passed).map((factor) => FACTOR_SHORT[factor.group] ?? factor.group);
  return failed.length ? failed.join(", ") : "NONE";
}

function StatusPill({ row }: { row: StrengthConfirmationResult }) {
  return <span className={`theta-pill theta-pill--${verdictTone(row.verdict)}`}>{verdictLabel(row.verdict)}</span>;
}

function FactorStrip({ row }: { row: StrengthConfirmationResult }) {
  const factors = factorMap(row);
  return (
    <div className="strength-factor-strip" aria-label={`${row.ticker} strength factor groups`}>
      {FACTOR_ORDER.map((group) => {
        const factor = factors.get(group);
        const passed = factor?.passed === true;
        return (
          <span
            key={group}
            className={`strength-factor-chip${passed ? " strength-factor-chip--pass" : " strength-factor-chip--fail"}`}
            title={factor ? `${group}: ${factor.checks_passed}/${factor.checks_total}` : `${group}: no data`}
          >
            {passed ? <CheckCircle2 size={11} aria-hidden="true" /> : <XCircle size={11} aria-hidden="true" />}
            {FACTOR_SHORT[group]}
          </span>
        );
      })}
    </div>
  );
}

function FactorCells({ row }: { row: StrengthConfirmationResult }) {
  const factors = factorMap(row);
  return (
    <>
      {FACTOR_ORDER.map((group) => {
        const factor = factors.get(group);
        const passed = factor?.passed === true;
        return (
          <td key={group} className="center">
            <span
              className={`strength-factor-dot${passed ? " strength-factor-dot--pass" : " strength-factor-dot--fail"}`}
              aria-label={`${group} ${passed ? "pass" : "fail"}`}
              title={factor ? `${factor.checks_passed}/${factor.checks_total} · ${factor.source}` : "No data"}
            >
              {passed ? <CheckCircle2 size={13} /> : <XCircle size={13} />}
            </span>
          </td>
        );
      })}
    </>
  );
}

function SourceSummary({ row }: { row: StrengthConfirmationResult }) {
  const approx = row.factors.filter((factor) => factor.source === "APPROX").length;
  return (
    <span className="report-meta">
      {approx > 0 ? `${approx} APPROX` : "UW"}
    </span>
  );
}

export default function StrengthConfirmationScanner({
  data,
  loading = false,
  scanning = false,
  error = null,
  lastSync = null,
  onScan,
  onTickerScan,
}: StrengthConfirmationScannerProps) {
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
    <section className="section strength-confirmation" data-testid="strength-confirmation-section">
      <div className="section-header">
        <div className="section-title">
          <ShieldCheck size={14} />
          7-Step Strength
        </div>
        <div className="strength-confirmation__meta">
          {lastSync && <span className="report-meta">{new Date(lastSync).toLocaleTimeString()}</span>}
          <span className="pill defined">{data?.confirmed_strength_count ?? 0} CONFIRMED</span>
          <span className="pill neutral">{data?.candidates_found ?? 0} CANDIDATES</span>
          <span className="pill neutral">{data?.tickers_scanned ?? 0} SCANNED</span>
          {onTickerScan && (
            <form className="theta-search strength-search" onSubmit={submitTickerScan}>
              <Search size={13} aria-hidden="true" />
              <input
                id="strength-ticker-search"
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
                aria-label="Strength ticker symbol"
                aria-invalid={tickerError ? "true" : "false"}
                aria-describedby={tickerError ? "strength-ticker-search-error" : undefined}
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
                <span id="strength-ticker-search-error" className="theta-search__error" role="alert">
                  {tickerError}
                </span>
              )}
            </form>
          )}
          {onScan && (
            <button
              type="button"
              className="theta-scan-button strength-scan-button"
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
          <div className="snapshot-card__empty">Measuring seven strength factors...</div>
        </div>
      ) : error ? (
        <div className="section-body">
          <div className="alert-item bearish">{error}</div>
        </div>
      ) : rows.length === 0 ? (
        <div className="section-body">
          <SectionEmptyState
            icon={ShieldCheck}
            headline="No confirmed strength setups"
            secondary="The latest scan did not find names with all seven factor groups aligned."
          />
        </div>
      ) : (
        <>
          <div className="section-body table-wrap strength-confirmation__table-wrap">
            <table>
              <thead>
                <tr>
                  <SortTh<StrengthSortKey> label="Ticker" sortKey="ticker" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<StrengthSortKey> label="Score" sortKey="score" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<StrengthSortKey> label="Groups" sortKey="groups" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<StrengthSortKey> label="Spot" sortKey="spot" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  {FACTOR_ORDER.map((group) => <th key={group} className="center">{FACTOR_SHORT[group]}</th>)}
                  <th>Failed</th>
                  <SortTh<StrengthSortKey> label="Status" sortKey="verdict" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                </tr>
              </thead>
              <tbody>
                {sorted.map((row) => (
                  <tr key={`strength-${row.ticker}`}>
                    <td>
                      <TickerLink ticker={row.ticker} />
                      <FactorStrip row={row} />
                    </td>
                    <td className="right">{row.score.toFixed(0)}</td>
                    <td className="right">{row.groups_passed}/7</td>
                    <td className="right">{fmtPrice(row.spot)}</td>
                    <FactorCells row={row} />
                    <td className="mono">{failedFactors(row)}</td>
                    <td>
                      <StatusPill row={row} />
                      <SourceSummary row={row} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="strength-confirmation__cards" data-testid="strength-confirmation-mobile-list">
            {sorted.map((row) => (
              <article key={`strength-card-${row.ticker}`} className="strength-card">
                <div className="strength-card__head">
                  <div>
                    <TickerLink ticker={row.ticker} />
                    <div className="strength-card__meta">{row.groups_passed}/7 · {fmtPrice(row.spot)}</div>
                  </div>
                  <StatusPill row={row} />
                </div>
                <div className="strength-card__score">
                  <span>{row.score.toFixed(0)}</span>
                  <em>CONFIRMATION SCORE</em>
                </div>
                <FactorStrip row={row} />
                <div className="strength-card__failed">FAILED: {failedFactors(row)}</div>
              </article>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
