"use client";

import { useEffect, useMemo, useRef, useState, type CSSProperties, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2, Search, Sparkles } from "lucide-react";
import InfoTooltip from "./InfoTooltip";
import ScannerInstrumentShell from "./ScannerInstrumentShell";
import SectionEmptyState from "./SectionEmptyState";
import { useWatchlist } from "@/lib/useWatchlist";
import { formatExpiry } from "@/lib/optionsChainUtils";
import type { ThetaHarvesterData, ThetaHarvesterEarnings, ThetaHarvesterLeg, ThetaHarvesterResult } from "@/lib/types";

/** Sortable columns of the overhauled grid (Scanner Table Overhaul.dc.html). */
type ThetaSortKey = "score" | "theta" | "iv" | "credit" | "dte";

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

function ThetaInfo({ label, helpKey }: { label: string; helpKey: ThetaHelpKey }) {
  return (
    <InfoTooltip
      text={THETA_HEADER_HELP[helpKey]}
      ariaLabel={`${label} theta harvester details`}
      triggerTestId={`theta-harvester-tooltip-${helpKey}`}
      contentTestId={`theta-harvester-tooltip-content-${helpKey}`}
    />
  );
}

function ThetaSortHeader({
  column,
  label,
  helpKey,
  active,
  direction,
  right = false,
  onSort,
}: {
  column: ThetaSortKey;
  label: string;
  helpKey: ThetaHelpKey;
  active: boolean;
  direction: -1 | 1;
  right?: boolean;
  onSort: (column: ThetaSortKey) => void;
}) {
  return (
    <span className={`theta-grid__h theta-grid__h--sort${right ? " right" : ""}`}>
      <button type="button" className="theta-grid__sort-trigger" onClick={() => onSort(column)}>
        {label} {active ? (direction === -1 ? "▾" : "▴") : ""}
      </button>
      <ThetaInfo label={label} helpKey={helpKey} />
    </span>
  );
}

/* ── Overhaul: filter chips + criteria pips (exported for vitest) ───────── */

export type ThetaFilterChipId = "all" | "dte30" | "noearn" | "rich" | "s97";

export const THETA_FILTER_CHIPS: readonly {
  id: ThetaFilterChipId;
  label: (total: number) => string;
  test: (row: ThetaHarvesterResult) => boolean;
}[] = [
  { id: "all", label: (n) => `All ${n}`, test: () => true },
  { id: "dte30", label: () => "DTE ≤ 30", test: (r) => r.structure.dte <= 30 },
  { id: "noearn", label: () => "No earnings risk", test: (r) => !(r.earnings?.within_dte ?? false) },
  { id: "rich", label: () => "IV ≥ 1.80×", test: (r) => r.iv_rv_ratio >= 1.8 },
  { id: "s97", label: () => "Score ≥ 97", test: (r) => r.score >= 97 },
];

export const THETA_CRITERIA: readonly { k: string; gate: string; label: string }[] = [
  { k: "Δ", gate: "delta_near_zero", label: "Delta neutrality within band" },
  { k: "V", gate: "iv_rich_vs_rv", label: "IV rich vs realized" },
  { k: "D", gate: "dealer_support", label: "Dealer positioning supportive" },
  { k: "Θ", gate: "theta_positive", label: "Theta decay dominant" },
];

const SORT_VALUE: Record<ThetaSortKey, (row: ThetaHarvesterResult) => number> = {
  score: (r) => r.score,
  theta: (r) => r.structure.theta,
  iv: (r) => r.iv_rv_ratio,
  credit: (r) => r.structure.credit ?? Number.NEGATIVE_INFINITY,
  dte: (r) => r.structure.dte,
};

/** Score bar fill, relative to the visible cohort so comparison stays honest. */
function scorePct(score: number, lo: number, hi: number): number {
  if (!Number.isFinite(score)) return 0;
  if (hi <= lo) return 100;
  return Math.round(Math.max(0, Math.min(1, (score - lo) / (hi - lo))) * 100);
}

function legMidCredit(leg: ThetaHarvesterLeg): string {
  const { bid, ask } = leg;
  if (bid != null && ask != null && bid > 0 && ask > 0) return `${((bid + ask) / 2).toFixed(2)} CR`;
  return "— CR";
}

function legLine(leg: ThetaHarvesterLeg): { name: string; val: string } {
  const kind = leg.right === "P" ? "PUT" : "CALL";
  return {
    name: `SELL ${leg.strike.toFixed(0)} ${kind}`,
    val: `${legMidCredit(leg)} · Δ ${signed(leg.delta, 2)}`,
  };
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
  const { isWatched, toggleWatch } = useWatchlist();
  const [tickerQuery, setTickerQuery] = useState("");
  const [tickerError, setTickerError] = useState<string | null>(null);
  const [minDteInput, setMinDteInput] = useState(String(THETA_DEFAULT_SCAN_PARAMS.minDte));
  const [maxDteInput, setMaxDteInput] = useState(String(THETA_DEFAULT_SCAN_PARAMS.maxDte));
  const [minCreditInput, setMinCreditInput] = useState(String(THETA_DEFAULT_SCAN_PARAMS.minCredit));
  const rows = data?.results ?? [];

  // Overhaul state: local sort (desc-first per column), chip filter, one
  // expanded row, multi-select, and a keyboard cursor (J/K/Enter/Space).
  const [sortKey, setSortKey] = useState<ThetaSortKey>("score");
  const [sortDir, setSortDir] = useState<-1 | 1>(-1);
  const [filter, setFilter] = useState<ThetaFilterChipId>("all");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [cursor, setCursor] = useState(0);

  const setSort = (key: ThetaSortKey) => {
    setSortDir((dir) => (sortKey === key ? ((-dir) as -1 | 1) : -1));
    setSortKey(key);
  };

  const activeChip = THETA_FILTER_CHIPS.find((c) => c.id === filter) ?? THETA_FILTER_CHIPS[0];
  const sorted = useMemo(() => {
    const value = SORT_VALUE[sortKey];
    return rows
      .filter(activeChip.test)
      .slice()
      .sort((a, b) => (value(a) - value(b)) * sortDir);
  }, [rows, activeChip, sortKey, sortDir]);

  const scoreBounds = useMemo(() => {
    const scores = sorted.map((r) => r.score).filter((s) => Number.isFinite(s));
    return { lo: Math.min(...scores), hi: Math.max(...scores) };
  }, [sorted]);

  const toggleExpand = (ticker: string) =>
    setExpanded((current) => (current === ticker ? null : ticker));
  const toggleSelect = (ticker: string) =>
    setSelected((current) =>
      current.includes(ticker) ? current.filter((t) => t !== ticker) : [...current, ticker],
    );

  // J/K move the cursor, Enter expands, Space selects — ignored while typing.
  const keyContext = useRef({ sorted, cursor });
  keyContext.current = { sorted, cursor };
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        event.metaKey || event.ctrlKey || event.altKey ||
        (target && (/^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName) || target.isContentEditable))
      ) {
        return;
      }
      const { sorted: list, cursor: at } = keyContext.current;
      if (list.length === 0) return;
      const key = event.key.toLowerCase();
      if (key === "j" || key === "k") {
        event.preventDefault();
        setCursor(Math.max(0, Math.min(list.length - 1, at + (key === "j" ? 1 : -1))));
      } else if (key === "enter" || key === " ") {
        const row = list[at];
        if (!row) return;
        event.preventDefault();
        if (key === "enter") toggleExpand(row.ticker);
        else toggleSelect(row.ticker);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const selectedRows = selected
    .map((ticker) => rows.find((r) => r.ticker === ticker))
    .filter((r): r is ThetaHarvesterResult => Boolean(r));

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
          <div className="section-body theta-harvester__table-wrap theta-grid" data-testid="theta-grid">
            {/* filter rail */}
            <div className="theta-grid__filters">
              <span className="theta-grid__filters-label">FILTER</span>
              {THETA_FILTER_CHIPS.map((chip) => (
                <button
                  key={chip.id}
                  type="button"
                  className={`theta-chip${filter === chip.id ? " theta-chip--on" : ""}`}
                  aria-pressed={filter === chip.id}
                  onClick={() => {
                    setFilter(chip.id);
                    setCursor(0);
                  }}
                >
                  {chip.label(rows.length)}
                </button>
              ))}
              <span className="theta-grid__filters-spacer" />
              <span className="theta-grid__filters-summary" data-testid="theta-filter-summary">
                {filter === "all" ? "NO FILTER" : `${activeChip.label(rows.length).toUpperCase()} ACTIVE`}
              </span>
            </div>

            {/* column header */}
            <div className="theta-grid__head" role="row">
              <span className="theta-grid__h">#</span>
              <ThetaSortHeader column="score" label="SCORE" helpKey="score" active={sortKey === "score"} direction={sortDir} onSort={setSort} />
              <span className="theta-grid__h">TICKER · STRUCT.</span>
              <ThetaSortHeader column="theta" label="THETA/DAY" helpKey="theta" active={sortKey === "theta"} direction={sortDir} right onSort={setSort} />
              <ThetaSortHeader column="iv" label="IV / RV" helpKey="iv-rv" active={sortKey === "iv"} direction={sortDir} right onSort={setSort} />
              <ThetaSortHeader column="credit" label="CREDIT" helpKey="credit" active={sortKey === "credit"} direction={sortDir} right onSort={setSort} />
              <ThetaSortHeader column="dte" label="DTE" helpKey="dte" active={sortKey === "dte"} direction={sortDir} right onSort={setSort} />
              <span className="theta-grid__h right">CRITERIA</span>
              <span className="theta-grid__h" aria-hidden />
            </div>

            {/* rows */}
            {sorted.map((row, index) => {
              const href = thetaOrderHref(row);
              const isExpanded = expanded === row.ticker;
              const isSelected = selected.includes(row.ticker);
              const put = row.structure.short_put;
              const call = row.structure.short_call;
              const legs = [
                legLine(put),
                legLine(call),
                { name: "WIDTH", val: `${(call.strike - put.strike).toFixed(1)} pt · ${row.structure.dte} DTE` },
              ];
              return (
                <div
                  key={`theta-${row.ticker}`}
                  className={`theta-grid__rowwrap${isSelected ? " is-selected" : ""}`}
                  data-testid={`theta-grid-row-${row.ticker}`}
                >
                  {isSelected && <span className="theta-grid__selrule" aria-hidden />}
                  <div
                    className={`theta-grid__row${index === cursor ? " is-cursor" : ""}`}
                    role="button"
                    tabIndex={0}
                    aria-expanded={isExpanded}
                    aria-label={`${row.ticker} details`}
                    onClick={() => toggleExpand(row.ticker)}
                    onKeyDown={(event) => {
                      if (event.key !== "Enter" && event.key !== " ") return;
                      event.preventDefault();
                      toggleExpand(row.ticker);
                    }}
                  >
                    <div className="theta-grid__rank">
                      <span
                        className={`theta-grid__selbox${isSelected ? " is-on" : ""}`}
                        role="checkbox"
                        aria-checked={isSelected}
                        aria-label={`Select ${row.ticker}`}
                        tabIndex={0}
                        onClick={(event) => {
                          event.stopPropagation();
                          toggleSelect(row.ticker);
                        }}
                        onKeyDown={(event) => {
                          if (event.key !== "Enter" && event.key !== " ") return;
                          event.preventDefault();
                          event.stopPropagation();
                          toggleSelect(row.ticker);
                        }}
                      >
                        {isSelected && <span className="theta-grid__selbox-fill" />}
                      </span>
                      <span className="theta-grid__ranknum">{String(index + 1).padStart(2, "0")}</span>
                    </div>
                    <div className="theta-grid__score">
                      <span className="theta-grid__score-num">{row.score.toFixed(1)}</span>
                      <span className="theta-grid__score-track">
                        <span
                          className="theta-grid__score-fill"
                          style={{ width: `${scorePct(row.score, scoreBounds.lo, scoreBounds.hi)}%` }}
                        />
                      </span>
                    </div>
                    <div className="theta-grid__ident">
                      <Link
                        href={href}
                        className="theta-row__ticker-link theta-grid__ticker"
                        aria-label={`Open ${row.ticker} theta order builder`}
                        title={`Open ${row.ticker} chain order builder`}
                        onClick={(event) => event.stopPropagation()}
                      >
                        {row.ticker}
                      </Link>
                      <span className="theta-grid__struct">SHORT {structureLabel(row)}</span>
                      {(row.earnings?.within_dte ?? false) && (
                        <span className="theta-grid__earnbadge" title={earningsTitle(row.earnings!)}>
                          ER {formatThetaEarningsLabel(row.earnings).replace(" · ", " ")}
                        </span>
                      )}
                      <span className="theta-grid__peek">
                        · {legs[0].name} / {legs[1].name}
                      </span>
                    </div>
                    <span className="theta-grid__num theta-grid__num--theta">
                      {signed(row.structure.theta * 100, 2)}/d
                    </span>
                    <span className="theta-grid__ivcell">
                      <span className="theta-grid__num">{row.iv_rv_ratio.toFixed(2)}×</span>
                      <span className="theta-grid__num theta-grid__num--dim">{signed(row.iv_rv_edge, 1)} pt</span>
                    </span>
                    <span className="theta-grid__num">{fmtMoney(row.structure.credit)}</span>
                    <span className="theta-grid__num theta-grid__num--dim">{row.structure.dte}</span>
                    <span className="theta-grid__pips">
                      {THETA_CRITERIA.map((crit) => {
                        const pass = row.gates[crit.gate] === true;
                        return (
                          <span
                            key={crit.gate}
                            className={`theta-grid__pip${pass ? " is-pass" : ""}`}
                            title={`${pass ? "PASS" : "NOT MET"} · ${crit.label}`}
                          >
                            {crit.k}
                          </span>
                        );
                      })}
                    </span>
                    <span className="theta-grid__caret" aria-hidden>{isExpanded ? "▾" : "▸"}</span>
                  </div>

                  {isExpanded && (
                    <div className="theta-grid__detail" data-testid={`theta-grid-detail-${row.ticker}`}>
                      <div className="theta-grid__detail-col">
                        <div className="theta-grid__detail-label">RECONSTRUCTED POSITION</div>
                        <div className="theta-grid__legs">
                          {legs.map((leg) => (
                            <div key={leg.name} className="theta-grid__leg">
                              <span>{leg.name}</span>
                              <span className="theta-grid__leg-val">{leg.val}</span>
                            </div>
                          ))}
                        </div>
                        <div className="theta-grid__actions">
                          <button type="button" className="theta-scan-button" onClick={() => openThetaOrder(href)}>
                            Send to orders
                          </button>
                          <button
                            type="button"
                            className="theta-grid__btn"
                            onClick={() => router.push(`/${encodeURIComponent(row.ticker)}`)}
                          >
                            Open surface
                          </button>
                          <button
                            type="button"
                            className="theta-grid__btn theta-grid__btn--ghost"
                            onClick={() => void toggleWatch(row.ticker)}
                          >
                            {isWatched(row.ticker) ? "Watching ✓" : "Watchlist"}
                          </button>
                        </div>
                      </div>
                      <div className="theta-grid__detail-col">
                        <div className="theta-grid__detail-label">DEMOTED COLUMNS</div>
                        <div className="theta-grid__facts">
                          <div className="theta-grid__fact">
                            <span className="theta-grid__fact-k">NET DELTA<ThetaInfo label="Net Delta" helpKey="net-delta" /></span>
                            <span className="theta-grid__fact-v">{fmtDelta(row.structure.net_delta)}</span>
                          </div>
                          <div className="theta-grid__fact">
                            <span className="theta-grid__fact-k">DEALER<ThetaInfo label="Dealer" helpKey="dealer" /></span>
                            <span className="theta-grid__fact-v">{dealerLabel(row)}</span>
                          </div>
                          <div className="theta-grid__fact">
                            <span className="theta-grid__fact-k">RANGE CONTAINMENT<ThetaInfo label="Range" helpKey="range" /></span>
                            <span className="theta-grid__fact-v">{rangeLabel(row)} {Math.round(row.range_score * 100)}%</span>
                          </div>
                          <div className="theta-grid__fact">
                            <span className="theta-grid__fact-k">STATUS<ThetaInfo label="Status" helpKey="status" /></span>
                            <span className="theta-grid__fact-v"><RowStatus row={row} /></span>
                          </div>
                          <div className="theta-grid__fact">
                            <span className="theta-grid__fact-k">EARNINGS<ThetaInfo label="Earnings" helpKey="earnings" /></span>
                            <span className="theta-grid__fact-v"><EarningsDisplay earnings={row.earnings} /></span>
                          </div>
                          <div className="theta-grid__fact">
                            <span className="theta-grid__fact-k">IV / RV</span>
                            <span className="theta-grid__fact-v">
                              {row.iv_rv_ratio.toFixed(2)}× · {signed(row.iv_rv_edge, 1)} pt
                            </span>
                          </div>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}

            {selectedRows.length > 0 && (
              <div className="theta-grid__selectionbar" data-testid="theta-selection-bar">
                <span className="theta-grid__selection-label">
                  {selectedRows.length} structure{selectedRows.length === 1 ? "" : "s"} selected ·{" "}
                  {selectedRows.map((r) => r.ticker).join(" · ")}
                </span>
                <div className="theta-grid__actions">
                  <button
                    type="button"
                    className="theta-grid__btn theta-grid__btn--ghost"
                    onClick={() => setSelected([])}
                  >
                    Clear
                  </button>
                  <button
                    type="button"
                    className="theta-scan-button"
                    onClick={() => openThetaOrder(thetaOrderHref(selectedRows[0]))}
                  >
                    Send to orders
                  </button>
                </div>
              </div>
            )}

            <div className="theta-grid__foot">
              <span>
                ENGINE {(data?.source ?? "—").toUpperCase()} · WINDOW DTE {minDteInput}–{maxDteInput}
              </span>
              <span>J / K TO MOVE · ENTER TO EXPAND · SPACE TO SELECT</span>
            </div>
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
