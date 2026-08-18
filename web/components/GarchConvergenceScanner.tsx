"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { GitCompareArrows, Loader2 } from "lucide-react";
import InfoTooltip from "./InfoTooltip";
import ScannerInstrumentShell from "./ScannerInstrumentShell";
import ScannerTickerSearch from "./ScannerTickerSearch";
import SectionEmptyState from "./SectionEmptyState";
import SpectralLoader from "./SpectralLoader";
import SortTh from "./SortTh";
import { useSort } from "@/lib/useSort";
import type { GarchConvergenceData, GarchPair } from "@/lib/types";

type GarchSortKey = "pair" | "lagger" | "divergence" | "gap" | "iv_rank" | "expected_move" | "signal" | "gates";
type GateFilter = "all" | "actionable" | "failed";

type GarchConvergenceScannerProps = {
  data: GarchConvergenceData | null;
  loading?: boolean;
  scanning?: boolean;
  error?: string | null;
  lastSync?: string | null;
  onScan?: () => void;
  onTickerScan?: (tickers: string[]) => void;
};

const GARCH_SECTION_HELP =
  "Cross-asset vol repricing: correlated pairs where the leader's vol has moved but the lagger's has not. Divergence is the composite lag metric. A row is actionable only when all four gates pass; failing gates are named.";

function extract(row: GarchPair, key: GarchSortKey): string | number | null {
  switch (key) {
    case "pair": return `${row.pair[0]}-${row.pair[1]}`;
    case "lagger": return row.lagger;
    case "divergence": return Math.abs(row.divergence);
    case "gap": return row.lagger_hv_iv_gap;
    case "iv_rank": return row.lagger_iv_rank;
    case "expected_move": return row.expected_move;
    case "signal": return row.signal;
    case "gates": return row.gates_passed ? "PASS" : (row.failing_gates.join(",") || "gates");
    default: return null;
  }
}

function fmt(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return "--";
  return value.toFixed(digits);
}

function signed(value: number, digits = 2): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function signedTone(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value) || value === 0) return "cell-muted";
  return value > 0 ? "positive" : "negative";
}

function signalPillClass(pair: GarchPair): string {
  if (!pair.gates_passed) return "garch-signal garch-signal--none";
  const s = (pair.signal || "").toUpperCase();
  if (s === "STRONG") return "garch-signal garch-signal--strong";
  if (s === "WATCH" || s === "WEAK") return "garch-signal garch-signal--watch";
  return "garch-signal garch-signal--pass";
}

function signalLabel(pair: GarchPair): string {
  const s = (pair.signal || "NONE").toUpperCase();
  if (!pair.gates_passed && (s === "NONE" || !pair.signal)) return "NONE";
  return s;
}

function formatScanTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return iso;
  return new Date(ms).toLocaleString("en-US", {
    timeZone: "America/New_York",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export default function GarchConvergenceScanner({
  data,
  loading = false,
  scanning = false,
  error = null,
  lastSync = null,
  onScan,
  onTickerScan,
}: GarchConvergenceScannerProps) {
  const [gateFilter, setGateFilter] = useState<GateFilter>("all");
  const rows = data?.pairs ?? [];
  const actionableCount = rows.filter((p) => p.gates_passed).length;
  const failedCount = rows.length - actionableCount;

  const filtered = useMemo(() => {
    if (gateFilter === "actionable") return rows.filter((p) => p.gates_passed);
    if (gateFilter === "failed") return rows.filter((p) => !p.gates_passed);
    return rows;
  }, [rows, gateFilter]);

  const { sorted, sort, toggle } = useSort<GarchPair, GarchSortKey>(
    filtered,
    extract,
    "divergence",
    "desc",
  );

  return (
    <ScannerInstrumentShell
      moduleId="GARCH / 06"
      title="GARCH Convergence"
      titleAccessory={
        <InfoTooltip
          text={GARCH_SECTION_HELP}
          ariaLabel="GARCH scanner details"
          triggerTestId="garch-scanner-title-tooltip"
          contentTestId="garch-scanner-title-tooltip-content"
        />
      }
      controls={
        <div className="theta-harvester__meta garch-scanner__meta">
          {lastSync && (
            <span className="report-meta" title={lastSync}>
              {formatScanTime(lastSync)} ET
            </span>
          )}
          <span className="pill defined" data-testid="garch-actionable-count">
            {actionableCount} ACTIONABLE
          </span>
          {rows.length > 0 && (
            <span className="report-meta garch-scanner__pair-count">
              {rows.length} pairs
            </span>
          )}
          {onTickerScan && (
            <ScannerTickerSearch
              id="garch-ticker-search"
              scanning={scanning}
              requirePairs
              dedupe={false}
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
        { k: "engine", v: "garch.convergence" },
        { k: "universe", v: data?.universe ?? "—" },
        {
          k: "last.sample",
          v: lastSync ? `${formatScanTime(lastSync)} ET` : "—",
        },
        { k: "actionable", v: String(actionableCount) },
      ]}
      className="garch-scanner"
      testId="garch-scanner-section"
    >
      {rows.length > 0 && (
        <div className="garch-scanner__filterbar" role="toolbar" aria-label="Filter pairs by gate status">
          {(
            [
              { id: "all", label: "All", count: rows.length },
              { id: "actionable", label: "Actionable", count: actionableCount },
              { id: "failed", label: "Failed gates", count: failedCount },
            ] as const
          ).map((opt) => (
            <button
              key={opt.id}
              type="button"
              className={`garch-filter-chip${gateFilter === opt.id ? " garch-filter-chip--active" : ""}`}
              aria-pressed={gateFilter === opt.id}
              onClick={() => setGateFilter(opt.id)}
              data-testid={`garch-filter-${opt.id}`}
            >
              {opt.label}
              <span className="garch-filter-chip__count">{opt.count}</span>
            </button>
          ))}
        </div>
      )}

      <div className="section-body garch-scanner__body">
        {error ? (
          <SectionEmptyState
            icon={GitCompareArrows}
            tone="danger"
            headline="GARCH scan failed"
            secondary={error}
            action={onScan ? { label: scanning ? "Scanning..." : "Retry scan", onClick: onScan, disabled: scanning } : undefined}
            testId="garch-scanner-error"
          />
        ) : loading && rows.length === 0 ? (
          <div className="p-6">
            <SpectralLoader label="Loading GARCH scan" />
          </div>
        ) : rows.length === 0 && data?.universe === "explicit" ? (
          <SectionEmptyState
            icon={GitCompareArrows}
            headline="Scan complete: no qualifying setups"
            secondary={`0 of ${data.requested_tickers?.length ?? 0} requested tickers qualified (${(data.requested_tickers ?? []).join(", ")}). Adjust the pairs or run the preset scan.`}
            action={onScan ? { label: "Run scan", onClick: onScan } : undefined}
          />
        ) : rows.length === 0 ? (
          <SectionEmptyState
            icon={GitCompareArrows}
            headline="No GARCH scan on file"
            secondary="Run a scan, or wait for the scheduled large-cap universe refresh (radon-garch.timer, trading days)."
            action={onScan ? { label: "Run scan", onClick: onScan } : undefined}
          />
        ) : sorted.length === 0 ? (
          <SectionEmptyState
            icon={GitCompareArrows}
            headline={gateFilter === "actionable" ? "No actionable pairs" : "No pairs in this filter"}
            secondary={
              gateFilter === "actionable"
                ? "No rows pass all gates. Switch to All or Failed gates to inspect the lag."
                : "No pairs match the current filter."
            }
            action={{ label: "Show all pairs", onClick: () => setGateFilter("all") }}
            testId="garch-scanner-filter-empty"
          />
        ) : (
          <div className="table-wrap garch-scanner__table-wrap">
            <table className="data-table garch-scanner__table">
              <thead>
                <tr>
                  <SortTh<GarchSortKey> label="Pair" sortKey="pair" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<GarchSortKey> label="Lagger" sortKey="lagger" activeKey={sort.key} direction={sort.direction} onToggle={toggle} helpText="The pair member whose implied vol has not yet repriced. Trade candidate is on the lagger." helpAriaLabel="Lagger details" />
                  <SortTh<GarchSortKey> label="Divergence" sortKey="divergence" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} helpText="Composite repricing-lag metric. Larger magnitude means a larger unpriced move implied by the leader." helpAriaLabel="Divergence details" />
                  <SortTh<GarchSortKey> label="HV-IV" sortKey="gap" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} helpText="Lagger realized vol minus implied vol, in vol points. Positive = lagger options cheap versus its own movement." helpAriaLabel="HV-IV gap details" />
                  <SortTh<GarchSortKey> label="IV Rank" sortKey="iv_rank" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<GarchSortKey> label="Exp move" sortKey="expected_move" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} helpText="GARCH-expected move for the lagger if it converges to model-implied vol." helpAriaLabel="Expected move details" />
                  <SortTh<GarchSortKey> label="Signal" sortKey="signal" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<GarchSortKey> label="Gates" sortKey="gates" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                </tr>
              </thead>
              <tbody>
                {sorted.map((p) => {
                  const key = `${p.pair[0]}-${p.pair[1]}`;
                  const leader = p.leader || p.pair[0];
                  const lagger = p.lagger || p.pair[1];
                  return (
                    <tr
                      key={key}
                      className={p.gates_passed ? "garch-row garch-row--actionable" : "garch-row garch-row--failed"}
                      data-testid={`garch-row-${key}`}
                      data-actionable={p.gates_passed ? "true" : "false"}
                    >
                      <td className="garch-pair-cell">
                        <span className="garch-pair-cell__leader" title={`Leader ${leader}`}>
                          {leader}
                        </span>
                        <span className="garch-pair-cell__arrow" aria-hidden>
                          →
                        </span>
                        <span className="garch-pair-cell__lagger-name" title={`Lagger ${lagger}`}>
                          {lagger}
                        </span>
                      </td>
                      <td>
                        <Link
                          href={`/${encodeURIComponent(lagger)}`}
                          className="ticker-link garch-lagger-link"
                          title={`Open ${lagger} (lagger; led by ${leader})`}
                        >
                          {lagger}
                        </Link>
                      </td>
                      <td className={`right mono ${signedTone(p.divergence)}`}>
                        {signed(p.divergence)}
                      </td>
                      <td className={`right mono ${signedTone(p.lagger_hv_iv_gap)}`}>
                        {signed(p.lagger_hv_iv_gap, 1)}
                      </td>
                      <td className="right mono cell-muted">{fmt(p.lagger_iv_rank)}</td>
                      <td className="right mono">
                        {p.expected_move != null ? `${fmt(p.expected_move)}%` : "--"}
                      </td>
                      <td>
                        <span className={signalPillClass(p)}>{signalLabel(p)}</span>
                      </td>
                      <td className="garch-gates-cell">
                        {p.gates_passed ? (
                          <span className="pill defined">PASS</span>
                        ) : (
                          <div className="garch-fail-list" title={p.failing_gates.join(", ") || "gates"}>
                            {(p.failing_gates.length > 0 ? p.failing_gates : ["gates"]).map((g) => (
                              <span key={g} className="garch-fail-chip">
                                {g}
                              </span>
                            ))}
                          </div>
                        )}
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
