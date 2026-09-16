"use client";

import Link from "next/link";
import { Activity, CheckCircle2, Loader2, XCircle } from "lucide-react";
import InfoTooltip from "./InfoTooltip";
import ScannerInstrumentShell from "./ScannerInstrumentShell";
import ScannerTickerSearch from "./ScannerTickerSearch";
import SectionEmptyState from "./SectionEmptyState";
import SortTh from "./SortTh";
import TickerLink from "./TickerLink";
import { useSort } from "@/lib/useSort";
import type { VolSkewMrData, VolSkewMrResult } from "@/lib/types";

type VolSkewSortKey = "ticker" | "spot" | "extension" | "iv_path" | "skew_path" | "verdict" | "structure";

type VolSkewMrScannerProps = {
  data: VolSkewMrData | null;
  loading?: boolean;
  scanning?: boolean;
  error?: string | null;
  lastSync?: string | null;
  onScan?: () => void;
  onTickerScan?: (tickers: string[]) => void;
};

const SECTION_HELP =
  "Short-term top and bottom framing from vol and skew versus spot extension. RSI and/or Bollinger percent B mark an extended high or low. Falling or flat IV into a high is TOP_MR; rising IV with the rally is BREAKOUT. Symmetric for BOTTOM_MR and BREAKDOWN. When vol and skew both diverge from spot, the row suggests a put spread (tops) or call spread (bottoms). Framing inspired by Options Insight / Imran Lakha.";

const GATE_HELP: Record<string, string> = {
  technicals: "Spot extension gate: RSI at or beyond 70/30 and/or Bollinger percent B outside 0 to 1.",
  iv: "IV path into the move. Falling or flat IV at an extreme is mean-reversion; rising IV with spot is continuation.",
  skew: "Skew path versus spot. Divergence with vol marks a defined-risk put spread (tops) or call spread (bottoms).",
};

function extract(row: VolSkewMrResult, key: VolSkewSortKey): string | number | null {
  switch (key) {
    case "ticker": return row.ticker;
    case "spot": return row.spot;
    case "extension": return row.extension;
    case "iv_path": return row.iv_path;
    case "skew_path": return row.skew_path;
    case "verdict": return row.verdict;
    case "structure": return row.suggested_structure;
    default: return null;
  }
}

function fmtNum(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return "---";
  return value.toFixed(digits);
}

function fmtPrice(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "---";
  return `$${value.toFixed(2)}`;
}

/**
 * A verdict is either a trade idea or a reason to stand aside. NO_SIGNAL has
 * no position to continue, so it must not borrow the continuation label.
 */
function structureLabel(row: VolSkewMrResult): string {
  if (row.suggested_structure) return row.suggested_structure;
  return row.verdict === "NO_SIGNAL" ? "---" : "continue";
}

function verdictLabel(verdict: string): string {
  if (verdict === "TOP_MR") return "TOP MR";
  if (verdict === "BOTTOM_MR") return "BOTTOM MR";
  if (verdict === "BREAKOUT") return "BREAKOUT";
  if (verdict === "BREAKDOWN") return "BREAKDOWN";
  return "NO SIGNAL";
}

function verdictTone(verdict: string): string {
  if (verdict === "BOTTOM_MR" || verdict === "BREAKOUT") return "pos";
  if (verdict === "TOP_MR" || verdict === "BREAKDOWN") return "neg";
  return "mut";
}

function extensionLabel(row: VolSkewMrResult): string {
  const rsi = row.rsi == null ? "RSI ---" : `RSI ${fmtNum(row.rsi, 0)}`;
  const band = row.pct_b == null ? "%B ---" : `%B ${fmtNum(row.pct_b, 2)}`;
  return `${row.extension} · ${rsi} · ${band}`;
}

/**
 * Deep-link a row into the ticker's chain deck. The row is ticker-level — no
 * expiry, strike, or right — so `?deck=c` opens the chain and leaves the order
 * builder empty rather than guessing a contract. Null when there is no setup.
 */
export function volSkewMrOrderHref(row: VolSkewMrResult): string | null {
  const ticker = row.ticker.trim().toUpperCase();
  if (!ticker) return null;
  if (row.verdict === "NO_SIGNAL") return null;
  const params = new URLSearchParams({ deck: "c", src: "vol-skew-mr" });
  return `/${encodeURIComponent(ticker)}?${params.toString()}`;
}

function SpotChainLink({ row }: { row: VolSkewMrResult }) {
  const href = volSkewMrOrderHref(row);
  if (!href) return <>{fmtPrice(row.spot)}</>;
  const ticker = row.ticker.trim().toUpperCase();
  return (
    <Link
      href={href}
      className="ticker-link"
      data-testid={`vol-skew-mr-order-link-${ticker}`}
      title={`Open the ${ticker} options chain`}
    >
      {fmtPrice(row.spot)}
    </Link>
  );
}

function StatusPill({ row }: { row: VolSkewMrResult }) {
  return <span className={`theta-pill theta-pill--${verdictTone(row.verdict)}`}>{verdictLabel(row.verdict)}</span>;
}

function GateChip({ label, passed }: { label: string; passed: boolean }) {
  return (
    <span
      className={`strength-factor-chip${passed ? " strength-factor-chip--pass" : " strength-factor-chip--fail"}`}
      title={`${label}: ${passed ? "pass" : "fail"}`}
    >
      {passed ? <CheckCircle2 size={11} aria-hidden="true" /> : <XCircle size={11} aria-hidden="true" />}
      {label}
    </span>
  );
}

function GateMap({ row }: { row: VolSkewMrResult }) {
  return (
    <div className="strength-factor-strip" aria-label={`${row.ticker} vol/skew gates`}>
      <GateChip label="TECH" passed={row.gates?.technicals ?? false} />
      <GateChip label="IV" passed={row.gates?.iv ?? false} />
      <GateChip label="SKEW" passed={row.gates?.skew ?? false} />
    </div>
  );
}

export default function VolSkewMrScanner({
  data,
  loading = false,
  scanning = false,
  error = null,
  lastSync = null,
  onScan,
  onTickerScan,
}: VolSkewMrScannerProps) {
  const rows = data?.results ?? [];
  const { sorted, sort, toggle } = useSort(rows, extract);

  return (
    <ScannerInstrumentShell
      moduleId="VOL SKEW / 08"
      title="Vol/Skew MR"
      titleAccessory={
        <InfoTooltip
          text={SECTION_HELP}
          ariaLabel="Vol/Skew MR scanner details"
          triggerTestId="vol-skew-mr-title-tooltip"
          contentTestId="vol-skew-mr-title-tooltip-content"
        />
      }
      controls={
        <div className="strength-confirmation__meta">
          {lastSync && <span className="report-meta">{new Date(lastSync).toLocaleTimeString()}</span>}
          <span className="pill defined">{data?.actionable_count ?? 0} MR</span>
          <span className="pill neutral">{data?.candidates_found ?? 0} NAMES</span>
          <span className="pill neutral">{data?.tickers_scanned ?? 0} SCANNED</span>
          {onTickerScan && (
            <ScannerTickerSearch
              id="vol-skew-mr-ticker-search"
              scanning={scanning}
              placeholder="AAPL, MSFT"
              onTickerScan={onTickerScan}
            />
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
      }
      rail={[
        { k: "source", v: data?.source ?? "Unusual Whales" },
        { k: "universe", v: data?.universe ?? "—" },
        { k: "last.sample", v: lastSync ? new Date(lastSync).toLocaleTimeString() : "—" },
        { k: "actionable", v: String(data?.actionable_count ?? 0) },
      ]}
      className="strength-confirmation"
      testId="vol-skew-mr-section"
    >
      {loading ? (
        <div className="section-body">
          <div className="snapshot-card__empty">Measuring spot extension, IV path, and skew...</div>
        </div>
      ) : error ? (
        <div className="section-body">
          <div className="alert-item bearish">{error}</div>
        </div>
      ) : rows.length === 0 ? (
        <div className="section-body">
          <SectionEmptyState
            icon={Activity}
            headline="No vol/skew MR readings"
            secondary="The latest scan did not return names. Run SCAN NDX or enter tickers."
          />
        </div>
      ) : (
        <>
          <div className="section-body table-wrap strength-confirmation__table-wrap">
            <table>
              <thead>
                <tr>
                  <SortTh<VolSkewSortKey> label="Ticker" sortKey="ticker" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<VolSkewSortKey> label="Spot" sortKey="spot" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<VolSkewSortKey> label="Spot ext" sortKey="extension" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<VolSkewSortKey> label="IV path" sortKey="iv_path" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<VolSkewSortKey> label="Skew path" sortKey="skew_path" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<VolSkewSortKey> label="Verdict" sortKey="verdict" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<VolSkewSortKey> label="Structure" sortKey="structure" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <th className="center strength-factor-header-cell">
                    <span className="strength-factor-header">
                      <span>Gates</span>
                      <InfoTooltip
                        text={`${GATE_HELP.technicals} ${GATE_HELP.iv} ${GATE_HELP.skew}`}
                        ariaLabel="Vol/Skew MR gate map details"
                        triggerTestId="vol-skew-mr-gate-tooltip"
                        contentTestId="vol-skew-mr-gate-tooltip-content"
                      />
                    </span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((row) => (
                  <tr key={`vol-skew-mr-${row.ticker}`} data-testid={`vol-skew-mr-row-${row.ticker}`}>
                    <td>
                      <TickerLink ticker={row.ticker} />
                    </td>
                    <td className="mono"><SpotChainLink row={row} /></td>
                    <td className="mono">{extensionLabel(row)}</td>
                    <td className="mono">{row.iv_path}</td>
                    <td className="mono">{row.skew_path}</td>
                    <td><StatusPill row={row} /></td>
                    <td className="mono">{structureLabel(row)}</td>
                    <td>
                      <GateMap row={row} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="strength-confirmation__cards" data-testid="vol-skew-mr-mobile-list">
            {sorted.map((row) => (
              <article key={`vol-skew-mr-card-${row.ticker}`} className="strength-card">
                <div className="strength-card__head">
                  <div>
                    <TickerLink ticker={row.ticker} />
                    <div className="strength-card__meta">{fmtPrice(row.spot)} · {extensionLabel(row)}</div>
                  </div>
                  <StatusPill row={row} />
                </div>
                <div className="strength-card__score">
                  <span>{structureLabel(row)}</span>
                  <em>STRUCTURE</em>
                </div>
                <GateMap row={row} />
                <div className="strength-card__failed">IV {row.iv_path} · SKEW {row.skew_path}</div>
              </article>
            ))}
          </div>
        </>
      )}
    </ScannerInstrumentShell>
  );
}
