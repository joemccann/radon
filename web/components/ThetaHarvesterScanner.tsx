"use client";

import { useState, type CSSProperties, type FormEvent, type KeyboardEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2, Search, Sparkles } from "lucide-react";
import InfoTooltip from "./InfoTooltip";
import ScannerInstrumentShell from "./ScannerInstrumentShell";
import SectionEmptyState from "./SectionEmptyState";
import SortTh from "./SortTh";
import { useSort } from "@/lib/useSort";
import { formatExpiry } from "@/lib/optionsChainUtils";
import type { ThetaHarvesterData, ThetaHarvesterEarnings, ThetaHarvesterResult } from "@/lib/types";

type ThetaSortKey = "ticker" | "score" | "theta" | "delta" | "iv_edge" | "range" | "dte" | "credit";

export type ThetaScanParams = { minDte: number; maxDte: number; minCredit: number };

export const THETA_DEFAULT_SCAN_PARAMS: ThetaScanParams = { minDte: 7, maxDte: 45, minCredit: 0 };

type ThetaHarvesterScannerProps = {
  data: ThetaHarvesterData | null;
  loading?: boolean;
  scanning?: boolean;
  error?: string | null;
  lastSync?: string | null;
  onScan?: (params: ThetaScanParams) => void;
  onTickerScan?: (ticker: string) => void;
};

const THETA_PARAMS_ROW: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 4,
};

const THETA_PARAMS_LABEL: CSSProperties = {
  fontFamily: "var(--font-mono)",
  fontSize: 9,
  letterSpacing: "0.05em",
  color: "var(--text-muted)",
};

const THETA_PARAMS_INPUT: CSSProperties = {
  width: 40,
  padding: "2px 4px",
  fontFamily: "var(--font-mono)",
  fontSize: 11,
  textAlign: "right",
  background: "var(--bg-panel-raised)",
  color: "var(--text-primary)",
  border: "1px solid var(--border-dim)",
  borderRadius: 4,
};

function clampNumber(raw: string, min: number, max: number, fallback: number, integer: boolean): number {
  const parsed = integer ? parseInt(raw, 10) : parseFloat(raw);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, parsed));
}

const THETA_SECTION_HELP =
  "Neutral short-premium scan for true theta setups: near-zero net delta, positive theta, rich IV versus realized vol, dealer support, and range-bound price action.";

const THETA_HEADER_HELP = {
  score: "Composite theta harvest score across delta neutrality, IV/RV richness, dealer support, range state, theta, and credit quality.",
  theta: "Estimated daily theta for the short strangle, shown per 1-lot. Positive means the structure earns decay.",
  "net-delta": "Net position delta for both short legs, normalized to shares. Near zero means less directional exposure.",
  "iv-rv": "Implied-volatility edge versus realized volatility, shown as point spread and IV/RV ratio.",
  dealer: "Dealer support gate. SUPPORT means positioning is expected to dampen spot movement inside the short range.",
  range: "Range-bound score. RANGE favors contained movement; TREND warns directional movement.",
  dte: "Days to expiration for the selected short put and short call expiry.",
  credit: "Estimated entry credit per share for the 1x short put plus 1x short call. Multiply by 100 per contract.",
  earnings: "Next earnings date if it falls inside the short-strangle DTE window. AMC=after close, BMO=before open.",
  status: "Final verdict: TRUE THETA passes the active gates, DIRECTIONAL indicates disguised delta exposure, and WATCH is mixed.",
} as const;

const SHORT_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] as const;

type ThetaHelpKey = keyof typeof THETA_HEADER_HELP;

function thetaHelpProps(label: string, helpKey: ThetaHelpKey) {
  return {
    helpText: THETA_HEADER_HELP[helpKey],
    helpAriaLabel: `${label} theta harvester details`,
    helpTriggerTestId: `theta-harvester-tooltip-${helpKey}`,
    helpContentTestId: `theta-harvester-tooltip-content-${helpKey}`,
  };
}

function ThetaHeaderInfoLabel({ label, helpKey }: { label: string; helpKey: ThetaHelpKey }) {
  return (
    <span className="scanner-header-label">
      <span>{label}</span>
      <InfoTooltip
        text={THETA_HEADER_HELP[helpKey]}
        ariaLabel={`${label} theta harvester details`}
        triggerTestId={`theta-harvester-tooltip-${helpKey}`}
        contentTestId={`theta-harvester-tooltip-content-${helpKey}`}
      />
    </span>
  );
}

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

function earningsSessionLabel(reportTime: string | null | undefined): string {
  if (reportTime === "postmarket") return "AMC";
  if (reportTime === "premarket") return "BMO";
  return "TBD";
}

function formatEarningsShortDate(reportDate: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(reportDate);
  if (!match) return reportDate;
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (month < 1 || month > 12 || day < 1) return reportDate;
  return `${SHORT_MONTHS[month - 1]} ${day}`;
}

/** Date side of the earnings chip (no session). Exported for vitest. */
export function formatThetaEarningsDatePart(
  earnings: ThetaHarvesterEarnings | null | undefined,
): string | null {
  if (!earnings?.report_date) return null;
  if (earnings.days_until === 0) return "TODAY";
  return formatEarningsShortDate(earnings.report_date);
}

/** Display label for theta-harvester earnings cell. Exported for vitest. */
export function formatThetaEarningsLabel(earnings: ThetaHarvesterEarnings | null | undefined): string {
  const datePart = formatThetaEarningsDatePart(earnings);
  if (!datePart || !earnings) return "---";
  return `${datePart} · ${earningsSessionLabel(earnings.report_time)}`;
}

function earningsChipTone(
  earnings: ThetaHarvesterEarnings,
): "hot" | "warn" | "quiet" {
  if (!earnings.within_dte) return "quiet";
  if (earnings.days_until === 0) return "hot";
  return "warn";
}

function earningsTitle(earnings: ThetaHarvesterEarnings): string {
  const session = earningsSessionLabel(earnings.report_time);
  const sessionLong =
    session === "AMC" ? "after close" : session === "BMO" ? "before open" : "session TBD";
  const window = earnings.within_dte
    ? "Inside the short-strangle DTE window."
    : "Outside the short-strangle DTE window.";
  const move =
    earnings.expected_move_pct != null && Number.isFinite(earnings.expected_move_pct)
      ? ` Expected move ${earnings.expected_move_pct.toFixed(1)}%.`
      : "";
  return `${earnings.report_date} ${sessionLong}. ${window}${move}`;
}

function EarningsDisplay({ earnings }: { earnings: ThetaHarvesterEarnings | null | undefined }) {
  const datePart = formatThetaEarningsDatePart(earnings);
  if (!datePart || !earnings) {
    return (
      <span className="theta-earnings theta-earnings--empty" data-testid="theta-earnings-cell">
        ---
      </span>
    );
  }
  const session = earningsSessionLabel(earnings.report_time);
  const tone = earningsChipTone(earnings);
  return (
    <span
      className={`theta-earnings theta-earnings--${tone}`}
      data-testid="theta-earnings-cell"
      data-within-dte={earnings.within_dte ? "true" : "false"}
      title={earningsTitle(earnings)}
    >
      <span className="theta-earnings__date">{datePart}</span>
      <span className="theta-earnings__sep" aria-hidden="true">
        ·
      </span>
      <span className="theta-earnings__session">{session}</span>
    </span>
  );
}

function legStrikeParam(strike: number): string {
  return Number.isInteger(strike) ? String(strike) : String(strike).replace(/0+$/, "").replace(/\.$/, "");
}

export function thetaOrderHref(row: ThetaHarvesterResult): string {
  const ticker = row.ticker.toUpperCase();
  const put = row.structure.short_put;
  const call = row.structure.short_call;
  const expiry = row.structure.expiry || put.expiry || call.expiry;
  const params = new URLSearchParams();
  params.set("deck", "c");
  params.set("expiry", formatExpiry(expiry));
  params.set("strikes", "100");
  params.set("legs", `SELL:1x${legStrikeParam(put.strike)}P,SELL:1x${legStrikeParam(call.strike)}C`);
  return `/${encodeURIComponent(ticker)}?${params.toString()}`;
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
  const router = useRouter();
  const [tickerQuery, setTickerQuery] = useState("");
  const [tickerError, setTickerError] = useState<string | null>(null);
  const [minDteInput, setMinDteInput] = useState(String(THETA_DEFAULT_SCAN_PARAMS.minDte));
  const [maxDteInput, setMaxDteInput] = useState(String(THETA_DEFAULT_SCAN_PARAMS.maxDte));
  const [minCreditInput, setMinCreditInput] = useState(String(THETA_DEFAULT_SCAN_PARAMS.minCredit));
  const rows = data?.results ?? [];
  const { sorted, sort, toggle } = useSort(rows, extract);
  const normalizedTicker = tickerQuery.trim().toUpperCase();

  const runPresetScan = () => {
    if (!onScan || scanning) return;
    const minDte = clampNumber(minDteInput, 0, 400, THETA_DEFAULT_SCAN_PARAMS.minDte, true);
    const maxDte = Math.max(minDte, clampNumber(maxDteInput, 0, 400, THETA_DEFAULT_SCAN_PARAMS.maxDte, true));
    const minCredit = clampNumber(minCreditInput, 0, 1000, 0, false);
    onScan({ minDte, maxDte, minCredit });
  };

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

  const openThetaOrder = (href: string) => {
    router.push(href);
  };

  const onThetaOrderKeyDown = (event: KeyboardEvent<HTMLTableRowElement>, href: string) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    openThetaOrder(href);
  };

  const controls = (
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
        <div className="theta-params" role="group" aria-label="Theta search parameters" style={THETA_PARAMS_ROW}>
          <span style={THETA_PARAMS_LABEL}>DTE</span>
          <input
            type="number" min={0} max={400} step={1} value={minDteInput}
            onChange={(e) => setMinDteInput(e.target.value)}
            aria-label="Minimum days to expiration" style={THETA_PARAMS_INPUT}
            data-testid="theta-min-dte"
          />
          <span style={THETA_PARAMS_LABEL}>–</span>
          <input
            type="number" min={0} max={400} step={1} value={maxDteInput}
            onChange={(e) => setMaxDteInput(e.target.value)}
            aria-label="Maximum days to expiration" style={THETA_PARAMS_INPUT}
            data-testid="theta-max-dte"
          />
          <span style={{ ...THETA_PARAMS_LABEL, marginLeft: 6 }}>MIN CR</span>
          <input
            type="number" min={0} max={1000} step={0.05} value={minCreditInput}
            onChange={(e) => setMinCreditInput(e.target.value)}
            aria-label="Minimum credit per share" style={THETA_PARAMS_INPUT}
            data-testid="theta-min-credit"
          />
          <button
            type="button"
            className="theta-scan-button"
            onClick={runPresetScan}
            disabled={scanning}
          >
            <Loader2 size={12} className={scanning ? "spin" : ""} />
            {scanning ? "SCANNING" : "SCAN NDX"}
          </button>
        </div>
      )}
    </div>
  );

  const rail = [
    { k: "source", v: data?.source ?? "Unusual Whales" },
    { k: "universe", v: data?.universe ?? "—" },
    {
      k: "last.sample",
      v: lastSync ? new Date(lastSync).toLocaleTimeString() : "—",
    },
    {
      k: "true.theta",
      v: String(data?.theta_harvest_count ?? 0),
    },
  ];

  return (
    <ScannerInstrumentShell
      moduleId="THETA / 03"
      title="Theta Harvester"
      titleAccessory={
        <InfoTooltip
          text={THETA_SECTION_HELP}
          ariaLabel="Theta Harvester scanner details"
          triggerTestId="theta-harvester-title-tooltip"
          contentTestId="theta-harvester-title-tooltip-content"
        />
      }
      controls={controls}
      rail={rail}
      className="theta-harvester"
      testId="theta-harvester-section"
    >
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
                  <SortTh<ThetaSortKey> label="Score" sortKey="score" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} {...thetaHelpProps("Score", "score")} />
                  <SortTh<ThetaSortKey> label="Theta" sortKey="theta" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} {...thetaHelpProps("Theta", "theta")} />
                  <SortTh<ThetaSortKey> label="Net Delta" sortKey="delta" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} {...thetaHelpProps("Net Delta", "net-delta")} />
                  <SortTh<ThetaSortKey> label="IV/RV" sortKey="iv_edge" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} {...thetaHelpProps("IV/RV", "iv-rv")} />
                  <th><ThetaHeaderInfoLabel label="Dealer" helpKey="dealer" /></th>
                  <SortTh<ThetaSortKey> label="Range" sortKey="range" activeKey={sort.key} direction={sort.direction} onToggle={toggle} {...thetaHelpProps("Range", "range")} />
                  <SortTh<ThetaSortKey> label="DTE" sortKey="dte" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} {...thetaHelpProps("DTE", "dte")} />
                  <SortTh<ThetaSortKey> label="Credit" sortKey="credit" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} {...thetaHelpProps("Credit", "credit")} />
                  <th><ThetaHeaderInfoLabel label="Earnings" helpKey="earnings" /></th>
                  <th><ThetaHeaderInfoLabel label="Status" helpKey="status" /></th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((row) => {
                  const href = thetaOrderHref(row);
                  return (
                  <tr
                    key={`theta-${row.ticker}`}
                    className="theta-row theta-row--actionable"
                    role="link"
                    tabIndex={0}
                    aria-label={`Open ${row.ticker} theta order builder`}
                    title={`Open ${row.ticker} chain order builder`}
                    onClick={() => openThetaOrder(href)}
                    onKeyDown={(event) => onThetaOrderKeyDown(event, href)}
                  >
                    <td>
                      <Link
                        href={href}
                        className="theta-row__ticker-link"
                        onClick={(event) => event.stopPropagation()}
                      >
                        {row.ticker}
                      </Link>
                    </td>
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
                    <td><EarningsDisplay earnings={row.earnings} /></td>
                    <td><RowStatus row={row} /></td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="theta-harvester__cards" data-testid="theta-harvester-mobile-list">
            {sorted.map((row) => (
              <Link key={`theta-card-${row.ticker}`} href={thetaOrderHref(row)} className="theta-card">
                <div className="theta-card__head">
                  <span className="theta-card__ticker">{row.ticker}</span>
                  <RowStatus row={row} />
                </div>
                <div
                  className="theta-card__structure"
                  title={`SHORT ${structureLabel(row)} · ${row.structure.dte}D`}
                >
                  SHORT {structureLabel(row)} · {row.structure.dte}D
                </div>
                <div className="theta-card__stats">
                  <span><b>{row.score.toFixed(0)}</b><em>SCORE</em></span>
                  <span><b>{fmtTheta(row.structure.theta)}</b><em>THETA</em></span>
                  <span><b>{fmtDelta(row.structure.net_delta)}</b><em>DELTA</em></span>
                  <span><b>{signed(row.iv_rv_edge, 1)} pt</b><em>IV/RV</em></span>
                </div>
                <div className="theta-card__earnings" data-testid="theta-earnings-mobile">
                  <em>EARN</em> <EarningsDisplay earnings={row.earnings} />
                </div>
                <GateStrip row={row} />
              </Link>
            ))}
          </div>
        </>
      )}
    </ScannerInstrumentShell>
  );
}
