"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bell,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Circle,
  ClipboardList,
  ArrowDown,
  ArrowUp,
  Loader2,
  Search,
  Sparkles,
  TrendingDown,
  TriangleAlert,
  Wrench,
  XCircle,
} from "lucide-react";
import type { BlotterTrade, DiscoverCandidate, ExecutedOrder, FlowAnalysisPosition, OpenOrder, OrdersData, PortfolioData, ScannerSignal, WorkspaceSection } from "@/lib/types";
import { useOrderActions } from "@/lib/OrderActionsContext";
import type { PriceData } from "@/lib/pricesProtocol";
import { optionKey } from "@/lib/pricesProtocol";
import { useJournal } from "@/lib/useJournal";
import { useDiscover } from "@/lib/useDiscover";
import { useFlowAnalysis } from "@/lib/useFlowAnalysis";
import { useScanner } from "@/lib/useScanner";
import { useBlotter } from "@/lib/useBlotter";
import { useSort, type SortDirection } from "@/lib/useSort";
import { fmtPrice, fmtUsd, legPriceKey } from "@/lib/positionUtils";
import PositionTable from "./PositionTable";
import { TableSkeleton } from "@/components/ui/Skeleton";
import CancelOrderDialog from "./CancelOrderDialog";
import ModifyOrderModal from "./ModifyOrderModal";
import RegimePanel from "./RegimePanel";
import CtaPage from "./CtaPage";
import PerformancePanel from "./PerformancePanel";
import InfoTooltip from "./InfoTooltip";
import SharePnlButton, { type SharePnlData } from "./SharePnlButton";
import { SECTION_TOOLTIPS } from "@/lib/sectionTooltips";
import TickerLink from "./TickerLink";
import TickerWorkspace from "./TickerWorkspace";

/* ─── Re-exports for backward compat ──────────────────── */

export {
  fmtUsd,
  fmtPrice,
  fmtPriceOrCalculated,
  resolveMarketValue,
  resolveEntryCost,
  getAvgEntry,
  getMultiplier,
  getLastPriceIsCalculated,
  legPriceKey,
  getOptionDailyChg,
  getLastPrice,
} from "@/lib/positionUtils";

/* ─── Share P&L helpers ────────────────────────────────── */

/** Build a human-readable description from an ExecutedOrder.
 *  e.g. "Long AAOI 2026-04-17 Call $45.00" */
/** Build a human-readable description from an ExecutedOrder.
 *  When realizedPNL is present (closing trade), show the ORIGINAL position
 *  direction: BOT closing = was Short, SLD closing = was Long.
 *  When no realizedPNL (opening trade): BOT = Long, SLD = Short. */
function execOrderDescription(e: ExecutedOrder): string {
  const c = e.contract;
  const isClosing = e.realizedPNL != null;
  const side = e.side === "BOT"
    ? (isClosing ? "Short" : "Long")
    : e.side === "SLD"
      ? (isClosing ? "Long" : "Short")
      : e.side;
  if (c.secType === "OPT" && c.strike != null && c.right && c.expiry) {
    const right = c.right === "C" || c.right === "CALL" ? "Call" : c.right === "P" || c.right === "PUT" ? "Put" : c.right;
    return `${side} ${c.symbol} ${c.expiry} ${right} $${c.strike.toFixed(2)}`;
  }
  return `${side} ${c.symbol}`;
}

function execOrderShareData(e: ExecutedOrder): SharePnlData {
  return {
    description: execOrderDescription(e),
    pnl: e.realizedPNL ?? 0,
    pnlPct: e.realizedPNL != null && e.avgPrice != null && e.avgPrice > 0
      ? (e.realizedPNL / (e.avgPrice * e.quantity * (e.contract.secType === "OPT" ? 100 : 1))) * 100
      : null,
    commission: e.commission,
    fillPrice: e.avgPrice,
    time: e.time ? new Date(e.time).toLocaleString() : "",
  };
}

function blotterShareData(t: BlotterTrade): SharePnlData {
  const lastExec = t.executions.length > 0 ? t.executions[t.executions.length - 1] : null;
  const pnlPct = t.cost_basis !== 0 ? (t.realized_pnl / Math.abs(t.cost_basis)) * 100 : null;
  return {
    description: t.contract_desc || t.symbol,
    pnl: t.realized_pnl,
    pnlPct,
    commission: t.total_commission,
    fillPrice: lastExec?.price ?? null,
    time: lastExec?.time ? new Date(lastExec.time).toLocaleString() : "",
  };
}

/* ─── Sortable header cell ──────────────────────────────── */

function SortTh<K extends string>({
  label,
  sortKey,
  activeKey,
  direction,
  onToggle,
  className,
}: {
  label: string;
  sortKey: K;
  activeKey: K | null;
  direction: SortDirection;
  onToggle: (key: K) => void;
  className?: string;
}) {
  const active = activeKey === sortKey;
  const ariaSort = active ? (direction === "asc" ? "ascending" : "descending") : undefined;
  return (
    <th
      className={`sortable-th ${className ?? ""} ${active ? "sort-active" : ""}`}
      onClick={() => onToggle(sortKey)}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onToggle(sortKey); } }}
      tabIndex={0}
      role="columnheader"
      aria-sort={ariaSort}
    >
      <span className="sort-label">
        {label}
        <span className="sort-icon">
          {active ? (
            direction === "asc" ? <ChevronUp size={10} /> : <ChevronDown size={10} />
          ) : (
            <ChevronDown size={10} className="sort-icon-idle" />
          )}
        </span>
      </span>
    </th>
  );
}

/* ─── Price direction hook (local, used by OrderPriceCell) ── */

function usePriceDirection(price: number | null): {
  direction: "up" | "down" | null;
  flashDirection: "up" | "down" | null;
} {
  const [direction, setDirection] = useState<"up" | "down" | null>(null);
  const [flashDirection, setFlashDirection] = useState<"up" | "down" | null>(null);
  const previousPrice = useRef<number | null>(null);

  useEffect(() => {
    const previous = previousPrice.current;

    if (previous == null || price == null) {
      setDirection(null);
      setFlashDirection(null);
      previousPrice.current = price;
      return undefined;
    }

    if (price > previous) {
      setDirection("up");
      setFlashDirection("up");
    } else if (price < previous) {
      setDirection("down");
      setFlashDirection("down");
    } else {
      setFlashDirection(null);
    }

    previousPrice.current = price;

    if (price !== previous) {
      const timer = setTimeout(() => setFlashDirection(null), 2500);
      return () => clearTimeout(timer);
    }
    return undefined;
  }, [price]);

  return { direction, flashDirection };
}

/* ─── Flow tables ───────────────────────────────────────── */

type FlowPosKey = "ticker" | "position" | "flow_label" | "strength" | "note";

const flowPosExtract = (item: FlowAnalysisPosition, key: FlowPosKey): string | number => {
  if (key === "strength") return item.strength;
  return item[key];
};

function FlowSparkline({ ratios }: { ratios?: { date: string; buy_ratio: number | null }[] }) {
  if (!ratios || ratios.length === 0) return <div className="strength-value">---</div>;
  const maxH = 28;
  return (
    <div className="flow-sparkline">
      {ratios.map((d, i) => {
        const r = d.buy_ratio;
        if (r == null) return <div key={i} className="flow-spark-bar neutral" style={{ height: 2 }} />;
        const cls = r >= 0.55 ? "accum" : r <= 0.45 ? "distrib" : "neutral";
        const h = Math.max(2, Math.round(r * maxH));
        return <div key={i} className={`flow-spark-bar ${cls}`} style={{ height: h }} title={`${d.date}: ${Math.round(r * 100)}%`} />;
      })}
    </div>
  );
}

function FlowTable({ rows, lastColumn }: { rows: FlowAnalysisPosition[]; lastColumn: string }) {
  const { sorted, sort, toggle } = useSort(rows, flowPosExtract);
  return (
    <table>
      <thead>
        <tr>
          <SortTh<FlowPosKey> label="Ticker" sortKey="ticker" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <SortTh<FlowPosKey> label="Position" sortKey="position" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <SortTh<FlowPosKey> label="Flow" sortKey="flow_label" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <SortTh<FlowPosKey> label="Strength" sortKey="strength" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
          <SortTh<FlowPosKey> label={lastColumn} sortKey="note" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
        </tr>
      </thead>
      <tbody>
        {sorted.map((item) => (
          <tr key={`${item.ticker}-${item.position}`}>
            <td><TickerLink ticker={item.ticker} /></td>
            <td>{item.position}</td>
            <td><span className={`pill ${item.flow_class}`}>{item.flow_label}</span></td>
            <td>
              <FlowSparkline ratios={item.daily_buy_ratios} />
              <div className="strength-value">{item.strength}</div>
            </td>
            <td>{item.note}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function FlowSections() {
  const { data, syncing, error, lastSync } = useFlowAnalysis(true);

  const supportsArr = data?.supports ?? [];
  const againstArr = data?.against ?? [];
  const watchArr = data?.watch ?? [];
  const neutralArr = data?.neutral ?? [];
  const totalScanned = data?.positions_scanned ?? 0;

  // Action items = against positions (flow contradicts trade direction)
  const actionItems = againstArr.filter((p) => p.strength >= 15);

  return (
    <>
      {actionItems.length > 0 && (
        <div className="section">
          <div className="alert-box">
            <div className="alert-title">
              <TriangleAlert size={14} />
              ACTION ITEMS
            </div>
            {actionItems.map((item) => (
              <div key={`${item.ticker}-${item.position}`} className="alert-item">
                <span className="alert-ticker">{item.ticker}</span> — {item.position}: {item.note}
              </div>
            ))}
          </div>
        </div>
      )}

      {error && (
        <div className="section">
          <div className="section-body"><div className="alert-item bearish">{error}</div></div>
        </div>
      )}

      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <CheckCircle2 size={14} />
            Flow Supports Position
            <InfoTooltip text={SECTION_TOOLTIPS["Flow Supports Position"]} />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
            {lastSync && (
              <span className="report-meta" style={{ margin: 0 }}>
                {new Date(lastSync).toLocaleTimeString()}
              </span>
            )}
            <span className="pill defined">
              {syncing ? "SYNCING..." : `${supportsArr.length} POSITIONS`}
            </span>
          </div>
        </div>
        <div className="section-body">
          {supportsArr.length > 0 ? (
            <FlowTable rows={supportsArr} lastColumn="Signal" />
          ) : (
            <div className="alert-item">{syncing ? "Scanning portfolio flow..." : "No supporting flow detected"}</div>
          )}
        </div>
      </div>

      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <TrendingDown size={14} />
            Flow Against Position
            <InfoTooltip text={SECTION_TOOLTIPS["Flow Against Position"]} />
          </div>
          <span className="pill distrib">{againstArr.length} POSITIONS</span>
        </div>
        <div className="section-body">
          {againstArr.length > 0 ? (
            <FlowTable rows={againstArr} lastColumn="Concern" />
          ) : (
            <div className="alert-item">No contradicting flow detected</div>
          )}
        </div>
      </div>

      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Circle size={14} />
            Neutral / Low Signal
            <InfoTooltip text={SECTION_TOOLTIPS["Neutral / Low Signal"]} />
          </div>
          <span className="pill neutral">{neutralArr.length} POSITIONS</span>
        </div>
        <div className="section-body">
          {neutralArr.length > 0 ? (
            <FlowTable rows={neutralArr} lastColumn="Note" />
          ) : (
            <div className="alert-item">No neutral positions</div>
          )}
        </div>
      </div>

      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Bell size={14} />
            Watch Closely
            <InfoTooltip text={SECTION_TOOLTIPS["Watch Closely"]} />
          </div>
          <span className="pill undefined">{watchArr.length} POSITIONS</span>
        </div>
        <div className="section-body">
          {watchArr.length > 0 ? (
            <FlowTable rows={watchArr} lastColumn="Note" />
          ) : (
            <div className="alert-item">No watch items</div>
          )}
        </div>
      </div>

      <div className="section">
        <div className="report-meta">
          {lastSync
            ? `Report Generated: ${new Date(lastSync).toLocaleString()} • Source: UW API • Dark Pool Lookback: 5 Trading Days • ${totalScanned} Positions Scanned`
            : "Awaiting initial flow analysis..."}
        </div>
      </div>
    </>
  );
}

/* ─── Portfolio sections ──────────────────────────────────── */

function PortfolioSections({ portfolio, prices }: { portfolio: PortfolioData | null; prices?: Record<string, PriceData> }) {
  if (!portfolio) {
    return (
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Circle size={14} />
            Portfolio
            <InfoTooltip text={SECTION_TOOLTIPS["Defined Risk Positions"]} />
          </div>
          <span className="pill neutral">LOADING</span>
        </div>
        <div className="section-body">
          <div className="alert-item">Waiting for portfolio data...</div>
        </div>
      </div>
    );
  }

  const definedPositions = portfolio.positions.filter((p) => p.risk_profile === "defined");
  const equityPositions = portfolio.positions.filter((p) => p.risk_profile === "equity");
  const undefinedPositions = portfolio.positions.filter((p) => p.risk_profile === "undefined" || p.risk_profile === "complex");

  return (
    <>
      {definedPositions.length > 0 && (
        <div className="section">
          <div className="section-header">
            <div className="section-title">
              <CheckCircle2 size={14} />
              Defined Risk Positions
              <InfoTooltip text={SECTION_TOOLTIPS["Defined Risk Positions"]} />
            </div>
            <span className="pill defined">{definedPositions.length} POSITIONS</span>
          </div>
          <div className="section-body">
            <PositionTable positions={definedPositions} showStrike={true} showUnderlying={true} prices={prices} />
          </div>
        </div>
      )}

      {undefinedPositions.length > 0 && (
        <div className="section">
          <div className="section-header">
            <div className="section-title">
              <TriangleAlert size={14} />
              Undefined Risk Positions
              <InfoTooltip text={SECTION_TOOLTIPS["Undefined Risk Positions"]} />
            </div>
            <span className="pill undefined">{undefinedPositions.length} POSITIONS</span>
          </div>
          <div className="section-body">
            <PositionTable positions={undefinedPositions} showUnderlying={true} prices={prices} />
          </div>
        </div>
      )}

      {equityPositions.length > 0 && (
        <div className="section">
          <div className="section-header">
            <div className="section-title">
              <Circle size={14} />
              Equity Positions
              <InfoTooltip text={SECTION_TOOLTIPS["Equity Positions"]} />
            </div>
            <span className="pill neutral">{equityPositions.length} POSITIONS</span>
          </div>
          <div className="section-body">
            <PositionTable positions={equityPositions} showExpiry={false} prices={prices} />
          </div>
        </div>
      )}

      <div className="section">
        <div className="report-meta">
          Last Sync: {new Date(portfolio.last_sync).toLocaleString()} • Source: IB Gateway (4001)
        </div>
      </div>
    </>
  );
}

/* ─── Scanner table ─────────────────────────────────────── */

type ScannerSortKey = "ticker" | "signal" | "direction" | "score" | "strength" | "buy_ratio" | "sustained_days" | "num_prints";

const scannerSigExtract = (item: ScannerSignal, key: ScannerSortKey): string | number | null => {
  switch (key) {
    case "ticker": return item.ticker;
    case "signal": return item.signal;
    case "direction": return item.direction;
    case "score": return item.score;
    case "strength": return item.strength;
    case "buy_ratio": return item.buy_ratio;
    case "sustained_days": return item.sustained_days;
    case "num_prints": return item.num_prints;
    default: return null;
  }
};

function ScannerSections() {
  const { data, syncing, error, lastSync } = useScanner(true);
  const signals = data?.top_signals ?? [];
  const { sorted, sort, toggle } = useSort(signals, scannerSigExtract);

  const signalClass = (signal: string) => {
    if (signal === "STRONG") return "bullish";
    if (signal === "MODERATE") return "neutral";
    return "bearish";
  };

  const dirClass = (dir: string) => {
    if (dir === "ACCUMULATION") return "accum";
    if (dir === "DISTRIBUTION") return "distrib";
    return "neutral";
  };

  return (
    <>
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Sparkles size={14} />
            Scanner Signals
            <InfoTooltip text={SECTION_TOOLTIPS["Scanner Signals"]} />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
            {lastSync && (
              <span className="report-meta" style={{ margin: 0 }}>
                {new Date(lastSync).toLocaleTimeString()}
              </span>
            )}
            <span className="pill defined">
              {syncing ? "SYNCING..." : `${data?.signals_found ?? 0} SIGNALS`}
            </span>
          </div>
        </div>
        {error && <div className="section-body"><div className="alert-item bearish">{error}</div></div>}
        {signals.length === 0 && !syncing && !error && (
          <div className="section-body"><div className="alert-item">No scanner signals. Waiting for initial scan...</div></div>
        )}
        {signals.length > 0 && (
          <div className="section-body table-wrap">
            <table>
              <thead>
                <tr>
                  <SortTh<ScannerSortKey> label="Ticker" sortKey="ticker" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<ScannerSortKey> label="Signal" sortKey="signal" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<ScannerSortKey> label="Direction" sortKey="direction" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<ScannerSortKey> label="Score" sortKey="score" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<ScannerSortKey> label="Strength" sortKey="strength" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<ScannerSortKey> label="Buy Ratio" sortKey="buy_ratio" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<ScannerSortKey> label="Sustained" sortKey="sustained_days" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<ScannerSortKey> label="Prints" sortKey="num_prints" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                </tr>
              </thead>
              <tbody>
                {sorted.map((row) => (
                  <tr key={`scanner-${row.ticker}`}>
                    <td><TickerLink ticker={row.ticker} /></td>
                    <td><span className={signalClass(row.signal)}>{row.signal}</span></td>
                    <td><span className={`pill ${dirClass(row.direction)}`}>{row.direction}</span></td>
                    <td className="right">{row.score.toFixed(1)}</td>
                    <td className="right">{row.strength.toFixed(1)}</td>
                    <td className="right">{row.buy_ratio != null ? `${(row.buy_ratio * 100).toFixed(1)}%` : "—"}</td>
                    <td className="right">{row.sustained_days > 0 ? `${row.sustained_days}d` : "—"}</td>
                    <td className="right">{row.num_prints.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {lastSync && (
        <div className="section">
          <div className="report-meta">
            Last Scan: {new Date(lastSync).toLocaleString()} • {data?.tickers_scanned ?? 0} Tickers Scanned
          </div>
        </div>
      )}
    </>
  );
}

/* ─── Non-table sections ────────────────────────────────── */

type DiscoverSortKey = "ticker" | "score" | "dp_direction" | "dp_strength" | "dp_buy_ratio" | "options_bias" | "alerts" | "total_premium" | "sweeps" | "sector";

const discoverExtract = (item: DiscoverCandidate, key: DiscoverSortKey): string | number | null => {
  switch (key) {
    case "ticker": return item.ticker;
    case "score": return item.score;
    case "dp_direction": return item.dp_direction;
    case "dp_strength": return item.dp_strength;
    case "dp_buy_ratio": return item.dp_buy_ratio;
    case "options_bias": return item.options_bias;
    case "alerts": return item.alerts;
    case "total_premium": return item.total_premium;
    case "sweeps": return item.sweeps;
    case "sector": return item.sector || item.issue_type || "";
    default: return null;
  }
};

function DiscoverSections() {
  const { data, syncing, error, lastSync } = useDiscover(true);
  const candidates = data?.candidates ?? [];
  const { sorted, sort, toggle } = useSort<DiscoverCandidate, DiscoverSortKey>(candidates, discoverExtract, "score", "desc");

  const fmtPremium = (v: number) => {
    if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
    if (v >= 1_000) return `$${(v / 1_000).toFixed(0)}K`;
    return `$${v.toFixed(0)}`;
  };

  const biasClass = (bias: string) => {
    if (bias === "BULLISH" || bias === "CALLS") return "bullish";
    if (bias === "BEARISH" || bias === "PUTS") return "bearish";
    return "neutral";
  };

  const dpClass = (dir: string) => {
    if (dir === "ACCUMULATION") return "bullish";
    if (dir === "DISTRIBUTION") return "bearish";
    return "neutral";
  };

  const scoreClass = (score: number) => {
    if (score >= 60) return "bullish";
    if (score >= 40) return "neutral";
    return "bearish";
  };

  return (
    <>
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Search size={14} />
            Discovery Candidates
            <InfoTooltip text={SECTION_TOOLTIPS["Discovery Candidates"]} />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
            {lastSync && (
              <span className="report-meta" style={{ margin: 0 }}>
                {new Date(lastSync).toLocaleTimeString()}
              </span>
            )}
            <span className="pill defined">
              {syncing ? "SYNCING..." : `${candidates.length} FOUND`}
            </span>
          </div>
        </div>
        {error && <div className="section-body"><div className="alert-item bearish">{error}</div></div>}
        {candidates.length === 0 && !syncing && !error && (
          <div className="section-body"><div className="alert-item">No candidates found. Waiting for initial scan...</div></div>
        )}
        {candidates.length > 0 && (
          <div className="section-body table-wrap">
            <table>
              <thead>
                <tr>
                  <SortTh<DiscoverSortKey> label="Ticker" sortKey="ticker" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<DiscoverSortKey> label="Score" sortKey="score" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<DiscoverSortKey> label="DP Direction" sortKey="dp_direction" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<DiscoverSortKey> label="DP Strength" sortKey="dp_strength" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<DiscoverSortKey> label="Buy Ratio" sortKey="dp_buy_ratio" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<DiscoverSortKey> label="Options Bias" sortKey="options_bias" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<DiscoverSortKey> label="Alerts" sortKey="alerts" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<DiscoverSortKey> label="Premium" sortKey="total_premium" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<DiscoverSortKey> label="Sweeps" sortKey="sweeps" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<DiscoverSortKey> label="Sector" sortKey="sector" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                </tr>
              </thead>
              <tbody>
                {sorted.map((c) => (
                  <tr key={c.ticker}>
                    <td><TickerLink ticker={c.ticker} /></td>
                    <td className="right">
                      <span className={scoreClass(c.score)}>{c.score.toFixed(1)}</span>
                    </td>
                    <td><span className={dpClass(c.dp_direction)}>{c.dp_direction}</span></td>
                    <td className="right">{c.dp_strength.toFixed(1)}</td>
                    <td className="right">{(c.dp_buy_ratio * 100).toFixed(1)}%</td>
                    <td><span className={biasClass(c.options_bias)}>{c.options_bias}</span></td>
                    <td className="right">{c.alerts}</td>
                    <td className="right">{fmtPremium(c.total_premium)}</td>
                    <td className="right">{c.sweeps}</td>
                    <td className="cell-muted">{c.sector || c.issue_type || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}

function JournalSections() {
  const { data, loading, error, syncWithIB, syncing, lastSyncResult } = useJournal();
  const [syncError, setSyncError] = useState<string | null>(null);
  const trades = useMemo(() => {
    if (!data?.trades) return [];
    return [...data.trades].sort((a, b) => b.id - a.id);
  }, [data]);

  const handleSync = useCallback(async () => {
    setSyncError(null);
    try {
      await syncWithIB();
    } catch (e) {
      setSyncError(e instanceof Error ? e.message : "Sync failed");
    }
  }, [syncWithIB]);

  const fmtJournalUsd = (v: number | undefined | null) => {
    if (v == null) return "—";
    const abs = Math.abs(v);
    const formatted = abs >= 1000 ? `$${abs.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}` : `$${abs.toFixed(2)}`;
    return v < 0 ? `-${formatted}` : formatted;
  };

  const decisionClass = (d: string) => {
    if (d === "EXECUTED" || d === "OPEN") return "bullish";
    if (d === "CLOSED") return "neutral";
    if (d === "FREED" || d === "CONVERTED") return "lean-bullish";
    if (d === "IB_AUTO_IMPORT") return "ib-import";
    return "bearish";
  };

  const pnlClass = (v: number | undefined | null) => {
    if (v == null) return "";
    return v >= 0 ? "bullish" : "bearish";
  };

  return (
    <>
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <Wrench size={14} />
            Trade Journal
            <InfoTooltip text={SECTION_TOOLTIPS["Trade Journal"]} />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <button
              className="btn-sync"
              onClick={handleSync}
              disabled={syncing}
              title="Sync unreconciled IB trades into journal"
            >
              {syncing ? "SYNCING..." : "SYNC IB"}
            </button>
            {lastSyncResult && (
              <span className="pill defined" style={{ fontSize: "9px" }}>
                {lastSyncResult.imported > 0
                  ? `+${lastSyncResult.imported} IMPORTED`
                  : "UP TO DATE"}
              </span>
            )}
            <span className="pill defined">{trades.length} TRADES</span>
          </div>
        </div>
        {error && <div className="section-body"><div className="alert-item bearish">{error}</div></div>}
        {syncError && <div className="section-body"><div className="alert-item bearish">IB Sync: {syncError}</div></div>}
        {loading && <div className="section-body p-6"><TableSkeleton rows={4} columns={6} /></div>}
        {!loading && trades.length === 0 && !error && (
          <div className="section-body"><div className="alert-item">No trades in journal.</div></div>
        )}
        {trades.length > 0 && (
          <div className="section-body table-wrap">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Date</th>
                  <th>Ticker</th>
                  <th>Structure</th>
                  <th>Status</th>
                  <th className="right">Qty</th>
                  <th className="right">Entry Cost</th>
                  <th className="right">Max Risk</th>
                  <th className="right">Realized P&L</th>
                  <th className="right">RoR</th>
                  <th>Gates</th>
                  <th>Edge</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t) => {
                  const qty = t.contracts ?? t.shares ?? t.quantity ?? null;
                  const cost = t.total_cost ?? t.entry_cost ?? null;
                  return (
                    <tr key={t.id}>
                      <td className="cell-muted">{t.id}</td>
                      <td>{t.date}</td>
                      <td><TickerLink ticker={t.ticker} /></td>
                      <td>{t.structure}</td>
                      <td><span className={decisionClass(t.decision)}>{t.decision}</span></td>
                      <td className="right">{qty ?? "—"}</td>
                      <td className="right">{fmtJournalUsd(cost)}</td>
                      <td className="right">{fmtJournalUsd(t.max_risk)}</td>
                      <td className="right"><span className={pnlClass(t.realized_pnl)}>{fmtJournalUsd(t.realized_pnl)}</span></td>
                      <td className="right">{t.return_on_risk != null ? `${(t.return_on_risk * 100).toFixed(1)}%` : "—"}</td>
                      <td className="cell-muted">{t.gates_passed?.join(", ") || t.gates_failed?.join(", ") || "—"}</td>
                      <td className="cell-muted">{t.edge_analysis?.edge_type ?? "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}

/* ─── Orders tables ────────────────────────────────────── */

type OpenOrderKey = "symbol" | "action" | "orderType" | "totalQuantity" | "limitPrice" | "lastPrice" | "status" | "tif" | "actions";

/** Build the prices-map key for an order's contract (option key for options, symbol for stocks). */
function orderPriceKey(contract: OpenOrder["contract"]): string | null {
  if (contract.secType === "BAG") return null;

  if (
    contract.secType === "OPT" &&
    contract.strike != null &&
    contract.right &&
    contract.expiry
  ) {
    const right = contract.right === "C" || contract.right === "P"
      ? contract.right
      : contract.right === "CALL" ? "C" : contract.right === "PUT" ? "P" : null;
    if (right) {
      const expiryClean = contract.expiry.replace(/-/g, "");
      if (expiryClean.length === 8) {
        return optionKey({ symbol: contract.symbol.toUpperCase(), expiry: expiryClean, strike: contract.strike, right });
      }
    }
  }
  return contract.symbol;
}

/**
 * Resolve the "last price" for an order.
 * For STK/OPT: use the WS price directly.
 * For BAG (spread): find the matching portfolio position and compute
 * the net mid from each leg's WS bid/ask (long leg mid − short leg mid).
 */
function resolveOrderLastPrice(
  order: OpenOrder,
  prices: Record<string, PriceData> | undefined,
  portfolio: PortfolioData | null | undefined,
): number | null {
  if (!prices) return null;
  const pk = orderPriceKey(order.contract);
  if (pk) return prices[pk]?.last ?? null;

  // BAG: compute net mid from portfolio legs
  if (order.contract.secType !== "BAG" || !portfolio) return null;
  const pos = portfolio.positions.find((p) => p.ticker === order.contract.symbol && p.legs.length > 1);
  if (!pos) return null;

  let netMid = 0;
  for (const leg of pos.legs) {
    const key = legPriceKey(pos.ticker, pos.expiry, leg);
    if (!key) return null;
    const lp = prices[key];
    if (!lp || lp.bid == null || lp.ask == null) return null;
    const mid = (lp.bid + lp.ask) / 2;
    const sign = leg.direction === "LONG" ? 1 : -1;
    netMid += sign * mid;
  }
  return Math.round(netMid * 100) / 100;
}

function makeOpenOrderExtract(prices?: Record<string, PriceData>, portfolio?: PortfolioData | null) {
  return (item: OpenOrder, key: OpenOrderKey): string | number | null => {
    switch (key) {
      case "symbol": return item.symbol;
      case "action": return item.action;
      case "orderType": return item.orderType;
      case "totalQuantity": return item.totalQuantity;
      case "limitPrice": return item.limitPrice;
      case "lastPrice": return resolveOrderLastPrice(item, prices, portfolio);
      case "status": return item.status;
      case "tif": return item.tif;
      case "actions": return null;
      default: return null;
    }
  };
}

/** Wrapper so usePriceDirection can be called per-order row (hooks can't go in map callbacks). */
function OrderPriceCell({ price }: { price: number | null }) {
  const { direction, flashDirection } = usePriceDirection(price);
  return (
    <td className={`right last-price-cell ${flashDirection ? `last-price-${flashDirection}` : ""}`}>
      {price != null ? fmtPrice(price) : "—"}
      {direction === "up" && <ArrowUp size={11} className="price-trend-icon price-trend-up" aria-label="price up" />}
      {direction === "down" && <ArrowDown size={11} className="price-trend-icon price-trend-down" aria-label="price down" />}
    </td>
  );
}

type ExecOrderKey = "symbol" | "side" | "quantity" | "avgPrice" | "commission" | "realizedPNL" | "time";

const execOrderExtract = (item: ExecutedOrder, key: ExecOrderKey): string | number | null => {
  switch (key) {
    case "symbol": return item.symbol;
    case "side": return item.side;
    case "quantity": return item.quantity;
    case "avgPrice": return item.avgPrice;
    case "commission": return item.commission;
    case "realizedPNL": return item.realizedPNL;
    case "time": return item.time;
    default: return null;
  }
};

function OrdersSections({
  orders,
  prices,
  portfolio,
}: {
  orders: OrdersData | null;
  prices?: Record<string, PriceData>;
  portfolio?: PortfolioData | null;
}) {
  const { pendingCancels, pendingModifies, cancelledOrders, requestCancel, requestModify } = useOrderActions();
  const openOrderExtract = useMemo(() => makeOpenOrderExtract(prices, portfolio), [prices, portfolio]);
  const openSort = useSort(orders?.open_orders ?? [], openOrderExtract);

  const [cancelTarget, setCancelTarget] = useState<OpenOrder | null>(null);
  const [modifyTarget, setModifyTarget] = useState<OpenOrder | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  const handleCancel = useCallback(async () => {
    if (!cancelTarget) return;
    setActionLoading(true);
    await requestCancel(cancelTarget);
    setActionLoading(false);
    setCancelTarget(null);
  }, [cancelTarget, requestCancel]);

  const handleModify = useCallback(async (newPrice: number, outsideRth?: boolean) => {
    if (!modifyTarget) return;
    setActionLoading(true);
    await requestModify(modifyTarget, newPrice, outsideRth);
    setActionLoading(false);
    setModifyTarget(null);
  }, [modifyTarget, requestModify]);

  // Merge cancelled orders into executed list for display (dedupe by permId)
  const allExecutedRows = useMemo(() => {
    const seen = new Set<number>();
    const cancelRows: ExecutedOrder[] = [];
    for (const c of cancelledOrders) {
      if (seen.has(c.permId)) continue;
      seen.add(c.permId);
      cancelRows.push({
        execId: `cancelled-${c.permId}`,
        symbol: c.symbol,
        contract: { conId: null, symbol: c.symbol, secType: "", strike: null, right: null, expiry: null },
        side: "CANCELLED",
        quantity: c.totalQuantity,
        avgPrice: c.limitPrice,
        commission: null,
        realizedPNL: null,
        time: c.cancelledAt,
        exchange: "",
      });
    }
    return [...cancelRows, ...(orders?.executed_orders ?? [])];
  }, [cancelledOrders, orders?.executed_orders]);

  const execSortWithCancelled = useSort<ExecutedOrder, ExecOrderKey>(allExecutedRows, execOrderExtract, "time", "desc");

  if (!orders) {
    return (
      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <ClipboardList size={14} />
            Orders
            <InfoTooltip text={SECTION_TOOLTIPS["Open Orders"]} />
          </div>
          <span className="pill neutral">LOADING</span>
        </div>
        <div className="section-body">
          <div className="alert-item">Waiting for orders data...</div>
        </div>
      </div>
    );
  }

  const canModify = (o: OpenOrder) => o.orderType === "LMT" || o.orderType === "STP LMT";
  const execCount = orders.executed_count + cancelledOrders.length;

  return (
    <>
      <CancelOrderDialog
        order={cancelTarget}
        loading={actionLoading}
        onConfirm={handleCancel}
        onClose={() => setCancelTarget(null)}
      />
      <ModifyOrderModal
        order={modifyTarget}
        loading={actionLoading}
        prices={prices}
        portfolio={portfolio}
        onConfirm={handleModify}
        onClose={() => setModifyTarget(null)}
      />

      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <ClipboardList size={14} />
            Open Orders
            <InfoTooltip text={SECTION_TOOLTIPS["Open Orders"]} />
          </div>
          <span className="pill defined">{orders.open_count} ORDERS</span>
        </div>
        <div className="section-body">
          {orders.open_orders.length === 0 ? (
            <div className="alert-item">No open orders</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <SortTh<OpenOrderKey> label="Symbol" sortKey="symbol" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />
                  <SortTh<OpenOrderKey> label="Action" sortKey="action" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />
                  <SortTh<OpenOrderKey> label="Type" sortKey="orderType" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />
                  <SortTh<OpenOrderKey> label="Quantity" sortKey="totalQuantity" className="right" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />
                  <SortTh<OpenOrderKey> label="Limit Price" sortKey="limitPrice" className="right" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />
                  <SortTh<OpenOrderKey> label="Last Price" sortKey="lastPrice" className="right" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />
                  <SortTh<OpenOrderKey> label="Status" sortKey="status" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />
                  <SortTh<OpenOrderKey> label="TIF" sortKey="tif" activeKey={openSort.sort.key} direction={openSort.sort.direction} onToggle={openSort.toggle} />
                  <th className="actions-th">Actions</th>
                </tr>
              </thead>
              <tbody>
                {openSort.sorted.map((o, i) => {
                  const isPendingCancel = pendingCancels.has(o.permId);
                  const isPendingModify = pendingModifies.has(o.permId);
                  const isPending = isPendingCancel || isPendingModify;
                  return (
                    <tr key={`${o.orderId}-${i}`} className={isPendingCancel ? "row-pending-cancel" : isPendingModify ? "row-pending-modify" : undefined}>
                      <td>
                        <TickerLink ticker={o.symbol} />
                        {isPending && <Loader2 size={12} className="cancel-spinner" />}
                      </td>
                      <td>
                        <span className={`pill ${o.action === "BUY" ? "accum" : "distrib"}`}>
                          {o.action}
                        </span>
                      </td>
                      <td>{o.orderType}</td>
                      <td className="right">{o.totalQuantity}</td>
                      <td className="right">
                        {isPendingModify ? (
                          <span className="status-modifying">Modifying...</span>
                        ) : (
                          o.limitPrice != null ? fmtPrice(o.limitPrice) : "—"
                        )}
                      </td>
                      <OrderPriceCell price={resolveOrderLastPrice(o, prices, portfolio)} />
                      <td>
                        {isPendingCancel ? (
                          <span className="status-cancelling">Cancelling...</span>
                        ) : isPendingModify ? (
                          <span className="status-modifying">Modifying...</span>
                        ) : (
                          o.status
                        )}
                      </td>
                      <td>{o.tif}</td>
                      <td className="actions-cell">
                        {isPending ? (
                          <span className="cancel-pending-label">PENDING</span>
                        ) : (
                          <>
                            <button
                              className="btn-order-action btn-modify"
                              disabled={!canModify(o)}
                              title={canModify(o) ? "Modify limit price" : "Only LMT orders can be modified"}
                              onClick={() => setModifyTarget(o)}
                            >
                              MODIFY
                            </button>
                            <button
                              className="btn-order-action btn-cancel"
                              onClick={() => setCancelTarget(o)}
                            >
                              CANCEL
                            </button>
                          </>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="section">
        <div className="section-header">
          <div className="section-title">
            <CheckCircle2 size={14} />
            Today&apos;s Executed Orders
            <InfoTooltip text={SECTION_TOOLTIPS["Today's Executed Orders"]} />
          </div>
          <span className="pill neutral">{execCount} {execCount === 1 ? "ENTRY" : "ENTRIES"}</span>
        </div>
        <div className="section-body">
          {allExecutedRows.length === 0 ? (
            <div className="alert-item">No fills this session</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <SortTh<ExecOrderKey> label="Symbol" sortKey="symbol" activeKey={execSortWithCancelled.sort.key} direction={execSortWithCancelled.sort.direction} onToggle={execSortWithCancelled.toggle} />
                  <SortTh<ExecOrderKey> label="Action" sortKey="side" activeKey={execSortWithCancelled.sort.key} direction={execSortWithCancelled.sort.direction} onToggle={execSortWithCancelled.toggle} />
                  <SortTh<ExecOrderKey> label="Quantity" sortKey="quantity" className="right" activeKey={execSortWithCancelled.sort.key} direction={execSortWithCancelled.sort.direction} onToggle={execSortWithCancelled.toggle} />
                  <SortTh<ExecOrderKey> label="Avg Fill Price" sortKey="avgPrice" className="right" activeKey={execSortWithCancelled.sort.key} direction={execSortWithCancelled.sort.direction} onToggle={execSortWithCancelled.toggle} />
                  <SortTh<ExecOrderKey> label="Commission" sortKey="commission" className="right" activeKey={execSortWithCancelled.sort.key} direction={execSortWithCancelled.sort.direction} onToggle={execSortWithCancelled.toggle} />
                  <SortTh<ExecOrderKey> label="Realized P&L" sortKey="realizedPNL" className="right" activeKey={execSortWithCancelled.sort.key} direction={execSortWithCancelled.sort.direction} onToggle={execSortWithCancelled.toggle} />
                  <SortTh<ExecOrderKey> label="Time" sortKey="time" activeKey={execSortWithCancelled.sort.key} direction={execSortWithCancelled.sort.direction} onToggle={execSortWithCancelled.toggle} />
                  <th style={{ width: "32px" }}></th>
                </tr>
              </thead>
              <tbody>
                {execSortWithCancelled.sorted.map((e, i) => {
                  const isCancelled = e.side === "CANCELLED";
                  const displaySide = isCancelled ? "CANCELLED" : e.side === "BOT" ? "BUY" : e.side === "SLD" ? "SELL" : e.side;
                  return (
                    <tr key={`${e.execId}-${i}`} className={isCancelled ? "row-cancelled" : undefined}>
                      <td>
                        <TickerLink ticker={e.symbol} />
                        {isCancelled && <XCircle size={12} className="cancelled-icon" />}
                      </td>
                      <td>
                        <span className={`pill ${isCancelled ? "cancelled" : displaySide === "BUY" ? "accum" : "distrib"}`}>
                          {displaySide}
                        </span>
                      </td>
                      <td className="right">{e.quantity}</td>
                      <td className="right">{e.avgPrice != null ? fmtPrice(e.avgPrice) : "—"}</td>
                      <td className="right">{e.commission != null ? fmtPrice(e.commission) : "—"}</td>
                      <td className={`right ${e.realizedPNL != null ? (e.realizedPNL >= 0 ? "positive" : "negative") : ""}`}>
                        {e.realizedPNL != null ? `${e.realizedPNL >= 0 ? "+" : ""}${fmtPrice(e.realizedPNL)}` : "—"}
                      </td>
                      <td>{new Date(e.time).toLocaleTimeString()}</td>
                      <td>
                        {!isCancelled && e.realizedPNL != null && (
                          <SharePnlButton data={execOrderShareData(e)} />
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {orders.last_sync && (
        <div className="section">
          <div className="report-meta">
            Last Sync: {new Date(orders.last_sync).toLocaleString()} • Source: IB Gateway (4001)
          </div>
        </div>
      )}

      <HistoricalTradesSection />
    </>
  );
}

/* ─── Historical Trades (Flex Query) ───────────────────── */

const BLOTTER_PAGE_SIZE = 15;

type BlotterSortKey = "date" | "symbol" | "contract_desc" | "sec_type" | "status" | "net_quantity" | "total_commission" | "realized_pnl" | "cost_basis" | "proceeds";

function getTradeDate(item: BlotterTrade): string {
  if (item.executions.length === 0) return "";
  return item.executions[item.executions.length - 1].time;
}

const blotterExtract = (item: BlotterTrade, key: BlotterSortKey): string | number | null => {
  switch (key) {
    case "date": return getTradeDate(item);
    case "symbol": return item.symbol;
    case "contract_desc": return item.contract_desc;
    case "sec_type": return item.sec_type;
    case "status": return item.is_closed ? "Closed" : "Open";
    case "net_quantity": return item.net_quantity;
    case "total_commission": return item.total_commission;
    case "realized_pnl": return item.realized_pnl;
    case "cost_basis": return item.cost_basis;
    case "proceeds": return item.proceeds;
    default: return null;
  }
};

function HistoricalTradesSection() {
  const { data, loading, syncing, error, syncNow } = useBlotter();
  const [page, setPage] = useState(0);

  const allTrades = useMemo(() => {
    if (!data) return [];
    // Merge closed + open trades, sorted by most recent execution date desc
    const merged = [...(data.closed_trades ?? []), ...(data.open_trades ?? [])];
    merged.sort((a, b) => {
      const aDate = a.executions.length > 0 ? a.executions[a.executions.length - 1].time : "";
      const bDate = b.executions.length > 0 ? b.executions[b.executions.length - 1].time : "";
      return bDate.localeCompare(aDate);
    });
    return merged;
  }, [data]);

  const { sorted, sort, toggle } = useSort(allTrades, blotterExtract);

  const totalPages = Math.max(1, Math.ceil(sorted.length / BLOTTER_PAGE_SIZE));
  const safePage = Math.min(page, totalPages - 1);
  const pageRows = sorted.slice(safePage * BLOTTER_PAGE_SIZE, (safePage + 1) * BLOTTER_PAGE_SIZE);

  // Reset page when data changes
  useEffect(() => { setPage(0); }, [data]);

  const totalCount = allTrades.length;
  const hasData = data && (data.as_of || totalCount > 0);

  return (
    <div className="section">
      <div className="section-header">
        <div className="section-title">
          <ClipboardList size={14} />
          Historical Trades (30 Days)
          <InfoTooltip text={SECTION_TOOLTIPS["Historical Trades (30 Days)"]} />
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          {data?.as_of && (
            <span className="report-meta" style={{ margin: 0, padding: 0, border: "none" }}>
              {new Date(data.as_of).toLocaleDateString()}
            </span>
          )}
          <span className="pill neutral">{totalCount} TRADES</span>
          <button
            className="sync-button"
            disabled={syncing}
            onClick={() => syncNow()}
          >
            {syncing ? <><Loader2 size={12} className="spin" /> Syncing...</> : "Refresh"}
          </button>
        </div>
      </div>
      <div className="section-body">
        {error && <div className="alert-item section-message bearish">{error}</div>}
        {loading && <div className="p-6"><TableSkeleton rows={5} columns={8} /></div>}
        {!loading && !hasData && !error && (
          <div className="alert-item section-message">No historical trades. Click REFRESH to fetch from IB.</div>
        )}
        {!loading && pageRows.length > 0 && (
          <>
            <table>
              <thead>
                <tr>
                  <SortTh<BlotterSortKey> label="Date" sortKey="date" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Symbol" sortKey="symbol" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Description" sortKey="contract_desc" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Type" sortKey="sec_type" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Side" sortKey="status" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Qty" sortKey="net_quantity" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Commission" sortKey="total_commission" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Realized P&L" sortKey="realized_pnl" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Cost Basis" sortKey="cost_basis" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <SortTh<BlotterSortKey> label="Proceeds" sortKey="proceeds" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
                  <th style={{ width: "32px" }}></th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((t, i) => (
                  <tr key={`${t.symbol}-${t.contract_desc}-${i}`}>
                    <td>{getTradeDate(t) ? new Date(getTradeDate(t)).toLocaleDateString() : "—"}</td>
                    <td><TickerLink ticker={t.symbol} /></td>
                    <td>{t.contract_desc}</td>
                    <td>{t.sec_type}</td>
                    <td>
                      <span className={`pill ${t.is_closed ? "neutral" : "defined"}`}>
                        {t.is_closed ? "Closed" : "Open"}
                      </span>
                    </td>
                    <td className="right">{t.net_quantity}</td>
                    <td className="right">{fmtPrice(t.total_commission)}</td>
                    <td className={`right ${t.realized_pnl >= 0 ? "positive" : "negative"}`}>
                      {t.realized_pnl >= 0 ? "+" : ""}{fmtPrice(t.realized_pnl)}
                    </td>
                    <td className="right">{fmtPrice(t.cost_basis)}</td>
                    <td className="right">{fmtPrice(t.proceeds)}</td>
                    <td>
                      {t.is_closed && <SharePnlButton data={blotterShareData(t)} />}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {totalPages > 1 && (
              <div className="pagination">
                <button disabled={safePage === 0} onClick={() => setPage(safePage - 1)}>
                  &larr; Prev
                </button>
                <span className="page-info">Page {safePage + 1} of {totalPages}</span>
                <button disabled={safePage >= totalPages - 1} onClick={() => setPage(safePage + 1)}>
                  Next &rarr;
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/* ─── Root switch ───────────────────────────────────────── */

type WorkspaceSectionsProps = {
  section: WorkspaceSection;
  portfolio?: PortfolioData | null;
  portfolioLastSync?: string | null;
  orders?: OrdersData | null;
  prices?: Record<string, PriceData>;
  tickerParam?: string;
  theme?: "dark" | "light";
};

export default function WorkspaceSections({ section, portfolio, portfolioLastSync, orders, prices, tickerParam, theme }: WorkspaceSectionsProps) {
  switch (section) {
    case "dashboard":
      return null;
    case "flow-analysis":
      return <FlowSections />;
    case "portfolio":
      return <PortfolioSections portfolio={portfolio ?? null} prices={prices} />;
    case "performance":
      return <PerformancePanel portfolioLastSync={portfolioLastSync} />;
    case "orders":
      return <OrdersSections orders={orders ?? null} prices={prices} portfolio={portfolio} />;
    case "scanner":
      return <ScannerSections />;
    case "discover":
      return <DiscoverSections />;
    case "journal":
      return <JournalSections />;
    case "regime":
      return <RegimePanel prices={prices ?? {}} />;
    case "cta":
      return <CtaPage />;
    case "ticker-detail":
      return tickerParam ? (
        <TickerWorkspace ticker={tickerParam} theme={theme ?? "dark"} />
      ) : null;
    default:
      return <FlowSections />;
  }
}
