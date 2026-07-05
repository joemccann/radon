"use client";

import Link from "next/link";
import { GitCompareArrows, Loader2 } from "lucide-react";
import InfoTooltip from "./InfoTooltip";
import ScannerTickerSearch from "./ScannerTickerSearch";
import SectionEmptyState from "./SectionEmptyState";
import SortTh from "./SortTh";
import { useSort } from "@/lib/useSort";
import type { GarchConvergenceData, GarchPair } from "@/lib/types";

type GarchSortKey = "pair" | "lagger" | "divergence" | "gap" | "iv_rank" | "expected_move" | "signal";

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
  "Cross-asset vol repricing scan: correlated pairs where the leader's vol has repriced but the lagger's has not. Divergence is the composite metric; a row is actionable only when it passes the four-gate framework, and failing gates are named.";

function extract(row: GarchPair, key: GarchSortKey): string | number | null {
  switch (key) {
    case "pair": return `${row.pair[0]}-${row.pair[1]}`;
    case "lagger": return row.lagger;
    case "divergence": return Math.abs(row.divergence);
    case "gap": return row.lagger_hv_iv_gap;
    case "iv_rank": return row.lagger_iv_rank;
    case "expected_move": return row.expected_move;
    case "signal": return row.signal;
    default: return null;
  }
}

function fmt(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return "---";
  return value.toFixed(digits);
}

function signed(value: number, digits = 2): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

function signalTone(pair: GarchPair): string {
  if (!pair.gates_passed) return "undefined";
  if (pair.signal === "STRONG") return "defined";
  return "neutral";
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
  const rows = data?.pairs ?? [];
  const { sorted, sort, toggle } = useSort<GarchPair, GarchSortKey>(rows, extract, "divergence", "desc");
  const actionableCount = rows.filter((p) => p.gates_passed).length;

  return (
    <section className="section garch-scanner" data-testid="garch-scanner-section">
      <div className="section-header">
        <div className="section-title">
          <GitCompareArrows size={14} />
          GARCH Convergence
          <InfoTooltip
            text={GARCH_SECTION_HELP}
            ariaLabel="GARCH scanner details"
            triggerTestId="garch-scanner-title-tooltip"
            contentTestId="garch-scanner-title-tooltip-content"
          />
        </div>
        <div className="theta-harvester__meta">
          {lastSync && <span className="report-meta">{new Date(lastSync).toLocaleTimeString()}</span>}
          <span className="pill defined">{actionableCount} ACTIONABLE</span>
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
            icon={GitCompareArrows}
            headline="Scan complete: no qualifying setups"
            secondary={`0 of ${data.requested_tickers?.length ?? 0} requested tickers qualified (${(data.requested_tickers ?? []).join(", ")}). Adjust the pairs or run the preset scan.`}
            action={onScan ? { label: "Run scan", onClick: onScan } : undefined}
          />
        ) : rows.length === 0 ? (
          <SectionEmptyState
            icon={GitCompareArrows}
            headline="No GARCH scan on file"
            secondary="Run a scan, or wait for the scheduled refresh (radon-garch.timer, 3x per trading day)."
            action={onScan ? { label: "Run scan", onClick: onScan } : undefined}
          />
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <SortTh<GarchSortKey> label="Pair" sortKey="pair" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<GarchSortKey> label="Lagger" sortKey="lagger" activeKey={sort.key} direction={sort.direction} onToggle={toggle} helpText="The pair member whose implied vol has not yet repriced. The trade candidate is on the lagger." helpAriaLabel="Lagger details" />
                  <SortTh<GarchSortKey> label="Divergence" sortKey="divergence" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} helpText="Composite repricing-lag metric. Larger magnitude = larger unpriced move implied by the leader." helpAriaLabel="Divergence details" />
                  <SortTh<GarchSortKey> label="HV−IV Gap" sortKey="gap" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} helpText="Lagger realized vol minus implied vol, in vol points. Positive = lagger options cheap versus its own movement." helpAriaLabel="Gap details" />
                  <SortTh<GarchSortKey> label="IV Rank" sortKey="iv_rank" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<GarchSortKey> label="Exp Move" sortKey="expected_move" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} helpText="GARCH-expected move for the lagger if it converges to the model-implied vol." helpAriaLabel="Expected move details" />
                  <SortTh<GarchSortKey> label="Signal" sortKey="signal" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <th>Gates</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((p) => {
                  const key = `${p.pair[0]}-${p.pair[1]}`;
                  return (
                    <tr key={key}>
                      <td>{p.pair[0]} ↔ {p.pair[1]}</td>
                      <td>
                        <Link href={`/${encodeURIComponent(p.lagger)}`} className="ticker-link" title={`Lagger ${p.lagger}, led by ${p.leader}`}>
                          {p.lagger}
                        </Link>
                      </td>
                      <td className="right">{signed(p.divergence)}</td>
                      <td className="right">{signed(p.lagger_hv_iv_gap, 1)}</td>
                      <td className="right">{fmt(p.lagger_iv_rank)}</td>
                      <td className="right">{p.expected_move != null ? `${fmt(p.expected_move)}%` : "---"}</td>
                      <td>
                        <span className={`pill ${signalTone(p)}`}>{p.signal || "NONE"}</span>
                      </td>
                      <td>
                        {p.gates_passed
                          ? <span className="pill defined">PASS</span>
                          : <span className="report-meta">Failed: {p.failing_gates.length > 0 ? p.failing_gates.join(", ") : "gates"}</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
