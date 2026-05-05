"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp, ChevronDown, ChevronUp } from "lucide-react";
import type { PortfolioLeg, PortfolioPosition } from "@/lib/types";
import type { PriceData } from "@/lib/pricesProtocol";
import InstrumentDetailModal from "./InstrumentDetailModal";
import { useSort, type SortDirection } from "@/lib/useSort";
import TickerLink from "./TickerLink";
import {
  fmtUsd,
  fmtPrice,
  fmtPriceOrCalculated,
  resolveMarketValue,
  resolveEntryCost,
  getAvgEntry,
  getMultiplier,
  getLastPrice,
  getLastPriceIsCalculated,
  legPriceKey,
  getOptionDailyChg,
  getTodayPnlDollars,
  resolveRealtimePrice,
} from "@/lib/positionUtils";
import { computeLegImpliedValue, computePositionImpliedValue } from "@/lib/impliedValue";
import { useRiskFreeRate } from "@/lib/useRiskFreeRate";
import { useColumnVisibility } from "@/lib/useColumnVisibility";
import { useViewport } from "@/lib/useViewport";
import { ColumnsToggle, type ColumnsToggleEntry } from "./ColumnsToggle";
import MobilePositionList from "./mobile/MobilePositionList";

/* ─── Sortable header cell ─────────────────────────────── */

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

/* ─── Price direction hook ─────────────────────────────── */

export function usePriceDirection(price: number | null): {
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

/* ─── Helpers ──────────────────────────────────────────── */

function getDailyChange(realtimePrice?: PriceData | null): number | null {
  if (!realtimePrice) return null;
  const { last, close } = realtimePrice;
  if (last == null || last <= 0 || close == null || close <= 0) return null;
  return ((last - close) / close) * 100;
}

function getLegMultiplier(leg: { type: string }): number {
  return leg.type === "Stock" ? 1 : 100;
}

function getOptionRtMv(pos: PortfolioPosition, prices?: Record<string, PriceData>): number | null {
  if (pos.structure_type === "Stock") return null;
  let rtMv = 0;
  for (const leg of pos.legs) {
    const key = legPriceKey(pos.ticker, pos.expiry, leg);
    const lp = key && prices ? prices[key] : null;
    const current = resolveRealtimePrice(lp, leg.market_price, Boolean(leg.market_price_is_calculated)).price;
    if (current == null) return null;
    const sign = leg.direction === "LONG" ? 1 : -1;
    rtMv += sign * current * leg.contracts * getLegMultiplier(leg);
  }
  return rtMv;
}

/* ─── Sort extract factory ─────────────────────────────── */

export type PositionSortKey = "ticker" | "structure" | "qty" | "direction" | "underlying" | "avg_entry" | "last_price" | "implied" | "implied_market_value" | "daily_chg" | "today_pnl" | "initial_value" | "entry_cost" | "market_value" | "pnl" | "expiry";

/** User-toggleable columns. `ticker` is the only always-on identity column —
 *  rows are keyed off it. `structure`/`direction`/`pnl`/`expiry` default ON
 *  but are user-toggleable; `underlying` is caller-controlled via the
 *  showUnderlying prop. */
export type PositionToggleableColumnKey =
  | "structure"
  | "qty"
  | "direction"
  | "avg_entry"
  | "last_price"
  | "implied"
  | "implied_market_value"
  | "daily_chg"
  | "today_pnl"
  | "initial_value"
  | "entry_cost"
  | "market_value"
  | "pnl"
  | "expiry";

// Internal alias retained so the existing JSX/cell gating reads the same.
type ToggleableColumnKey = PositionToggleableColumnKey;

export const POSITION_COLUMNS: readonly ColumnsToggleEntry<PositionToggleableColumnKey>[] = [
  { key: "structure", label: "Structure" },
  { key: "qty", label: "Qty" },
  { key: "direction", label: "Direction" },
  { key: "avg_entry", label: "Avg Entry" },
  { key: "last_price", label: "Last Price" },
  { key: "implied", label: "Implied" },
  { key: "implied_market_value", label: "Implied MV" },
  { key: "daily_chg", label: "Day Chg" },
  { key: "today_pnl", label: "Today P&L" },
  { key: "market_value", label: "Market Value" },
  { key: "entry_cost", label: "Entry Cost" },
  { key: "initial_value", label: "Initial Value" },
  { key: "pnl", label: "P&L" },
  { key: "expiry", label: "Expiry" },
];

/** Default visibility — chosen so a fresh-install user fits the table on a
 *  ~1280px viewport. Less-essential columns (Implied MV, Entry Cost) start
 *  hidden and can be toggled on. */
export const POSITION_COLUMN_DEFAULTS: Record<PositionToggleableColumnKey, boolean> = {
  structure: true,
  qty: true,
  direction: true,
  avg_entry: true,
  last_price: true,
  implied: true,
  implied_market_value: false,
  daily_chg: true,
  today_pnl: true,
  initial_value: true,
  entry_cost: false,
  market_value: true,
  pnl: true,
  expiry: true,
};

export type PositionColumnVisibility = Record<PositionToggleableColumnKey, boolean>;

function makePositionExtract(prices?: Record<string, PriceData>, riskFreeRate = 0) {
  return (pos: PortfolioPosition, key: PositionSortKey): string | number | null => {
    const isStock = pos.structure_type === "Stock";
    const _stockLast = prices?.[pos.ticker]?.last;
    const rtStockLast = _stockLast != null && _stockLast > 0 ? _stockLast : null;
    const optRtMv = getOptionRtMv(pos, prices);
    const mv = isStock && rtStockLast != null ? rtStockLast * pos.contracts : optRtMv ?? resolveMarketValue(pos);
    switch (key) {
      case "ticker": return pos.ticker;
      case "structure": return pos.structure;
      case "qty": return pos.contracts;
      case "direction": return pos.direction;
      case "underlying": return rtStockLast;
      case "avg_entry": return getAvgEntry(pos);
      case "last_price": {
        if (isStock && rtStockLast != null) return rtStockLast;
        if (optRtMv != null) return optRtMv / (pos.contracts * getMultiplier(pos));
        return getLastPrice(pos);
      }
      case "implied": {
        if (isStock || !prices) return null;
        return computePositionImpliedValue(pos, prices, { riskFreeRate }).netPerContract;
      }
      case "implied_market_value": {
        if (isStock || !prices) return null;
        return computePositionImpliedValue(pos, prices, { riskFreeRate }).netNotional;
      }
      case "daily_chg": return isStock ? getDailyChange(prices?.[pos.ticker]) : getOptionDailyChg(pos, prices);
      case "today_pnl": return getTodayPnlDollars(pos, prices);
      case "initial_value": return Math.abs(resolveEntryCost(pos));
      case "entry_cost": return resolveEntryCost(pos);
      case "market_value": return mv;
      case "pnl": return mv != null ? mv - resolveEntryCost(pos) : null;
      case "expiry": return pos.expiry === "N/A" ? null : pos.expiry;
      default: return null;
    }
  };
}

/* ─── Leg row ──────────────────────────────────────────── */

function LegRow({
  leg,
  showExpiry,
  showUnderlying,
  showImplied,
  columns,
  realtimeLegPrice,
  legImpliedPerContract,
  legImpliedMv,
  onLegClick,
}: {
  leg: PortfolioPosition["legs"][number];
  showExpiry: boolean;
  showUnderlying?: boolean;
  showImplied?: boolean;
  columns: PositionColumnVisibility;
  realtimeLegPrice?: PriceData | null;
  legImpliedPerContract?: number | null;
  legImpliedMv?: number | null;
  onLegClick?: (leg: PortfolioLeg) => void;
}) {
  const resolvedPrice = resolveRealtimePrice(
    realtimeLegPrice,
    leg.market_price != null ? Math.abs(leg.market_price) : null,
    Boolean(leg.market_price_is_calculated),
  );
  const marketPrice = resolvedPrice.price;
  const isCalculated = resolvedPrice.isCalculated;
  const { direction: priceDirection, flashDirection } = usePriceDirection(marketPrice);

  // Per-leg P&L: sign-aware (MV - EC)
  const mult = leg.type === "Stock" ? 1 : 100;
  const legMv = marketPrice != null ? marketPrice * leg.contracts * mult : leg.market_value != null ? Math.abs(leg.market_value) : null;
  const legEc = Math.abs(leg.entry_cost);
  const sign = leg.direction === "LONG" ? 1 : -1;
  const legPnl = legMv != null ? sign * (legMv - legEc) : null;

  // The leg-description cell spans across the columns to the right of Ticker
  // up through Direction: Structure, optional Qty, Direction (each gated).
  const descColSpan =
    (columns.structure ? 1 : 0) + (columns.qty ? 1 : 0) + (columns.direction ? 1 : 0);

  return (
    <tr className={flashDirection ? `last-price-${flashDirection}` : undefined}>
      <td></td>
      {descColSpan > 0 && (
        <td
          colSpan={descColSpan}
          className={`cell-indent cell-muted ${onLegClick ? "leg-clickable" : ""}`}
          onClick={onLegClick ? () => onLegClick(leg) : undefined}
        >
          {leg.direction} {leg.contracts}x {leg.type}{leg.strike ? ` $${leg.strike}` : ""}
        </td>
      )}
      {showUnderlying && <td></td>}
      {columns.avg_entry && (
        <td className="right cell-muted">{fmtPrice(Math.abs(leg.avg_cost) / (leg.type === "Stock" ? 1 : 100))}</td>
      )}
      {columns.last_price && (
        <td className="right last-price-cell">
          {marketPrice != null ? fmtPriceOrCalculated(marketPrice, isCalculated) : "—"}
          {priceDirection === "up" && <ArrowUp size={11} className="price-trend-icon price-trend-up" aria-label="price up" />}
          {priceDirection === "down" && <ArrowDown size={11} className="price-trend-icon price-trend-down" aria-label="price down" />}
        </td>
      )}
      {showImplied && columns.implied && (
        <td className="right cell-muted" title="Black-Scholes implied value at current spot">
          {legImpliedPerContract != null ? fmtPrice(legImpliedPerContract) : "—"}
        </td>
      )}
      {showImplied && columns.implied_market_value && (
        <td
          className={`right cell-muted ${legImpliedMv != null ? (legImpliedMv >= 0 ? "positive" : "negative") : ""}`}
          title="Implied market value: BS price × contracts × 100, signed"
        >
          {legImpliedMv != null
            ? `${legImpliedMv >= 0 ? "+" : "-"}${fmtUsd(Math.abs(legImpliedMv))}`
            : "—"}
        </td>
      )}
      {columns.daily_chg && <td></td>}
      {columns.today_pnl && <td></td>}
      {columns.market_value && (
        <td className="right cell-muted">{legMv != null ? fmtUsd(legMv) : "—"}</td>
      )}
      {columns.entry_cost && (
        <td className="right cell-muted">{fmtPrice(legEc)}</td>
      )}
      {columns.initial_value && (
        <td className="right cell-muted">{fmtUsd(legEc)}</td>
      )}
      {columns.pnl && (
        <td className={`right cell-muted ${legPnl != null ? (legPnl >= 0 ? "positive" : "negative") : ""}`}>
          {legPnl != null ? `${legPnl >= 0 ? "+" : "-"}${fmtUsd(Math.abs(legPnl))}` : "—"}
        </td>
      )}
      {showExpiry && columns.expiry && <td></td>}
    </tr>
  );
}

/* ─── Position row ─────────────────────────────────────── */

function PositionRow({ pos, showExpiry = true, showUnderlying = false, showImplied = false, columns, realtimePrice, prices, riskFreeRate = 0, onLegClick }: { pos: PortfolioPosition; showExpiry?: boolean; showUnderlying?: boolean; showImplied?: boolean; columns: PositionColumnVisibility; realtimePrice?: PriceData | null; prices?: Record<string, PriceData>; riskFreeRate?: number; onLegClick?: (leg: PortfolioLeg, pos: PortfolioPosition) => void }) {
  const [legsExpanded, setLegsExpanded] = useState(false);
  const hasMultipleLegs = pos.legs.length > 1;

  // For stock positions, prefer the real-time WS price over the stale sync price
  const isStock = pos.structure_type === "Stock";
  const rtLast = isStock && realtimePrice?.last != null && realtimePrice.last > 0 ? realtimePrice.last : null;

  // For options: compute real-time MV and daily change from leg-level WS prices
  const optionsRt = useMemo(() => {
    if (isStock) return null;
    let rtMv = 0;
    let rtDailyPnl = 0;
    let rtCloseValue = 0;
    let hasCloseData = false;
    let priceIsCalculated = false;
    for (const leg of pos.legs) {
      const key = legPriceKey(pos.ticker, pos.expiry, leg);
      const lp = key && prices ? prices[key] : null;
      const resolved = resolveRealtimePrice(lp, leg.market_price, Boolean(leg.market_price_is_calculated));
      const current = resolved.price;
      if (current == null) return null;
      priceIsCalculated = priceIsCalculated || resolved.isCalculated;
      const sign = leg.direction === "LONG" ? 1 : -1;
      const multiplier = getLegMultiplier(leg);
      rtMv += sign * current * leg.contracts * multiplier;
      const close = lp?.close;
      if (close != null && close > 0) {
        rtDailyPnl += sign * (current - close) * leg.contracts * multiplier;
        rtCloseValue += sign * close * leg.contracts * multiplier;
        hasCloseData = true;
      }
    }
    return {
      mv: rtMv,
      dailyPnl: hasCloseData ? rtDailyPnl : null,
      closeValue: rtCloseValue,
      priceIsCalculated,
    };
  }, [isStock, prices, pos.legs, pos.ticker, pos.expiry]);

  const mv = rtLast != null ? rtLast * pos.contracts : optionsRt?.mv ?? resolveMarketValue(pos);
  const entryCost = resolveEntryCost(pos);
  const pnl = mv != null ? mv - entryCost : null;
  const pnlPct = pnl != null && entryCost !== 0 ? (pnl / Math.abs(entryCost)) * 100 : null;
  const avgEntry = getAvgEntry(pos);
  const lastPrice = rtLast ?? (optionsRt ? mv! / (pos.contracts * getMultiplier(pos)) : getLastPrice(pos));
  const lastPriceIsCalculated = rtLast != null ? false : optionsRt ? optionsRt.priceIsCalculated : getLastPriceIsCalculated(pos);
  const { direction: priceDirection, flashDirection } = usePriceDirection(lastPrice);
  // Stock: daily change from underlying WS price
  // Options: prefer IB's per-position daily P&L (handles intraday additions correctly)
  //          then fall back to WS close-based calculation
  const wsDailyPnl = optionsRt?.dailyPnl ?? null;
  const wsCloseValue = optionsRt?.closeValue ?? 0;
  // IB's reqPnLSingle daily P&L — correctly handles blended positions
  // (overnight contracts use yesterday's close, intraday adds use fill price)
  const ibDailyPnl = (!isStock && pos.ib_daily_pnl != null) ? pos.ib_daily_pnl : null;
  const effectiveDailyPnl = ibDailyPnl ?? wsDailyPnl;

  // Same-day positions opened today: yesterday's close is meaningless.
  // Use entry-cost-based P&L instead (Today's P&L = Total P&L).
  const dailyChg = isStock
    ? getDailyChange(realtimePrice)
    : getOptionDailyChg(pos, prices);

  // Black-Scholes implied per-share, signed-summed across legs. null for stocks
  // or when any leg lacks IV / spot.
  const impliedNet = useMemo(() => {
    if (isStock || !prices) return null;
    return computePositionImpliedValue(pos, prices, { riskFreeRate }).netPerContract;
  }, [isStock, pos, prices, riskFreeRate]);

  // Black-Scholes implied dollar notional (= netNotional). Signed: long debit
  // positions positive, short/credit positions negative.
  const impliedNotional = useMemo(() => {
    if (isStock || !prices) return null;
    return computePositionImpliedValue(pos, prices, { riskFreeRate }).netNotional;
  }, [isStock, pos, prices, riskFreeRate]);

  // Today's P&L in dollars
  const todayPnl = isStock
    ? (realtimePrice?.last != null && realtimePrice.last > 0 && realtimePrice?.close != null && realtimePrice.close > 0
        ? (realtimePrice.last - realtimePrice.close) * pos.contracts
        : null)
    : getTodayPnlDollars(pos, prices);

  // Structure already includes strike from ib_sync format_structure_description()
  const structureDisplay = pos.structure;

  // Underlying price (for options positions)
  const underlyingPrice = realtimePrice?.last != null && realtimePrice.last !== 0 ? realtimePrice.last : null;
  const { direction: underlyingDirection, flashDirection: underlyingFlash } = usePriceDirection(underlyingPrice);

  return (
    <>
      <tr className={flashDirection ? `last-price-${flashDirection}` : undefined}>
        <td>
          {hasMultipleLegs ? (
            <span className="ticker-with-chevron">
              <TickerLink ticker={pos.ticker} positionId={pos.id} />
              <button
                className="leg-toggle-btn"
                onClick={() => setLegsExpanded((v) => !v)}
                aria-expanded={legsExpanded}
                aria-label={`${legsExpanded ? "Collapse" : "Expand"} legs for ${pos.ticker}`}
              >
                {legsExpanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
              </button>
            </span>
          ) : (
            <TickerLink ticker={pos.ticker} positionId={pos.id} />
          )}
        </td>
        {columns.structure && <td>{structureDisplay}</td>}
        {columns.qty && <td className="right">{pos.contracts}</td>}
        {columns.direction && (
          <td>
            <span className={`pill ${pos.risk_profile === "defined" ? "defined" : pos.risk_profile === "equity" ? "neutral" : "undefined"}`}>
              {pos.direction}
            </span>
          </td>
        )}
        {showUnderlying && (
          <td className={`right last-price-cell ${underlyingFlash ? `last-price-${underlyingFlash}` : ""}`}>
            {underlyingPrice != null ? fmtPrice(underlyingPrice) : "—"}
            {underlyingDirection === "up" && <ArrowUp size={11} className="price-trend-icon price-trend-up" aria-label="underlying up" />}
            {underlyingDirection === "down" && <ArrowDown size={11} className="price-trend-icon price-trend-down" aria-label="underlying down" />}
          </td>
        )}
        {columns.avg_entry && <td className="right">{fmtPrice(avgEntry)}</td>}
        {columns.last_price && (
          <td className={`right last-price-cell ${flashDirection ? `last-price-${flashDirection}` : ""}`}>
            {lastPrice != null ? fmtPriceOrCalculated(lastPrice, lastPriceIsCalculated) : "—"}
            {priceDirection === "up" && <ArrowUp size={11} className="price-trend-icon price-trend-up" aria-label="price up" />}
            {priceDirection === "down" && <ArrowDown size={11} className="price-trend-icon price-trend-down" aria-label="price down" />}
          </td>
        )}
        {showImplied && columns.implied && (
          <td className="right cell-muted" title="Black-Scholes implied value at current spot">
            {impliedNet != null ? fmtPrice(impliedNet) : "—"}
          </td>
        )}
        {showImplied && columns.implied_market_value && (
          <td
            className={`right ${impliedNotional != null ? (impliedNotional >= 0 ? "positive" : "negative") : ""}`}
            title="Implied market value: BS price × contracts × multiplier, signed"
          >
            {impliedNotional != null
              ? `${impliedNotional >= 0 ? "+" : "-"}${fmtUsd(Math.abs(impliedNotional))}`
              : "—"}
          </td>
        )}
        {columns.daily_chg && (
          <td className={`right ${dailyChg != null ? (dailyChg >= 0 ? "positive" : "negative") : ""}`}>
            {dailyChg != null ? `${dailyChg >= 0 ? "+" : ""}${dailyChg.toFixed(2)}%` : "—"}
          </td>
        )}
        {columns.today_pnl && (
          <td className={`right ${todayPnl != null ? (todayPnl >= 0 ? "positive" : "negative") : ""}`}>
            {todayPnl != null ? `${todayPnl >= 0 ? "+" : "-"}${fmtUsd(Math.abs(todayPnl))}` : "—"}
          </td>
        )}
        {columns.market_value && <td className="right">{mv != null ? fmtUsd(mv) : "—"}</td>}
        {columns.entry_cost && <td className="right">{fmtUsd(entryCost)}</td>}
        {columns.initial_value && <td className="right">{fmtUsd(Math.abs(entryCost))}</td>}
        {columns.pnl && (
          <td className={`right ${pnl != null ? (pnl >= 0 ? "positive" : "negative") : ""}`}>
            {pnl != null ? `${pnl >= 0 ? "+" : "-"}${fmtUsd(Math.abs(pnl))} (${pnlPct!.toFixed(1)}%)` : "—"}
          </td>
        )}
        {showExpiry && columns.expiry && <td>{pos.expiry !== "N/A" ? pos.expiry : "—"}</td>}
      </tr>
      {hasMultipleLegs && legsExpanded && pos.legs.map((leg, i) => {
        const key = legPriceKey(pos.ticker, pos.expiry, leg);
        const legResult =
          leg.type === "Stock" || leg.strike == null || leg.strike === 0 || !prices
            ? null
            : computeLegImpliedValue(
                {
                  ticker: pos.ticker,
                  expiry: pos.expiry,
                  strike: leg.strike,
                  type: leg.type,
                  direction: leg.direction,
                  contracts: leg.contracts,
                },
                prices,
                { riskFreeRate },
              );
        const legImplied = legResult?.perContract ?? null;
        const legSign = leg.direction === "LONG" ? 1 : -1;
        const legImpliedMv = legResult?.notional != null ? legSign * legResult.notional : null;
        return (
          <LegRow
            key={`${pos.id}-leg-${i}`}
            leg={leg}
            showExpiry={showExpiry}
            showUnderlying={showUnderlying}
            showImplied={showImplied}
            columns={columns}
            realtimeLegPrice={key && prices ? prices[key] : null}
            legImpliedPerContract={legImplied}
            legImpliedMv={legImpliedMv}
            onLegClick={onLegClick ? (l) => onLegClick(l, pos) : undefined}
          />
        );
      })}
    </>
  );
}

/* ─── Position table ───────────────────────────────────── */

export default function PositionTable({
  positions,
  showExpiry = true,
  showUnderlying = false,
  prices,
  tableId = "positions",
  columnVisibility: controlledVisibility,
}: {
  positions: PortfolioPosition[];
  showExpiry?: boolean;
  showUnderlying?: boolean;
  prices?: Record<string, PriceData>;
  tableId?: string;
  /** When provided, the table is "controlled" — the parent owns the column
   *  visibility state and renders the toggle widget itself (e.g. inside a
   *  shared section header). When omitted, the table falls back to its own
   *  internal hook + toolbar above the table. */
  columnVisibility?: PositionColumnVisibility;
}) {
  const { isMobile, hasMounted } = useViewport();
  const riskFreeRate = useRiskFreeRate();
  const positionExtract = useMemo(() => makePositionExtract(prices, riskFreeRate), [prices, riskFreeRate]);
  const { sorted, sort, toggle } = useSort(positions, positionExtract);
  // Implied columns are only meaningful for option positions. Hide them entirely
  // when this table renders an all-stock list (e.g. the Equity Positions section).
  const showImplied = useMemo(
    () => positions.some((p) => p.structure_type !== "Stock"),
    [positions],
  );
  // Internal fallback hook — only renders the toolbar when the parent didn't
  // provide a controlled `columnVisibility` map.
  const internal = useColumnVisibility<ToggleableColumnKey>(tableId, POSITION_COLUMN_DEFAULTS);
  const isControlled = controlledVisibility != null;
  const columns: PositionColumnVisibility = isControlled ? controlledVisibility! : internal.visible;
  // Hide Implied / Implied MV from the toggle menu when no option rows exist.
  const visibleColumnEntries = useMemo<readonly ColumnsToggleEntry<ToggleableColumnKey>[]>(
    () =>
      showImplied
        ? POSITION_COLUMNS
        : POSITION_COLUMNS.filter((c) => c.key !== "implied" && c.key !== "implied_market_value"),
    [showImplied],
  );

  // Instrument detail modal state
  const [activeInstrument, setActiveInstrument] = useState<{ leg: PortfolioLeg; ticker: string; expiry: string } | null>(null);

  const handleLegClick = useCallback((leg: PortfolioLeg, pos: PortfolioPosition) => {
    setActiveInstrument({ leg, ticker: pos.ticker, expiry: pos.expiry });
  }, []);

  if (isMobile && hasMounted) {
    return <MobilePositionList positions={sorted} prices={prices} showExpiry={showExpiry} />;
  }

  return (
    <>
      {!isControlled && (
        <div className="position-table-toolbar">
          <ColumnsToggle<ToggleableColumnKey>
            columns={visibleColumnEntries}
            visible={columns}
            onToggle={internal.toggle}
            onReset={internal.reset}
          />
        </div>
      )}
      <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <SortTh<PositionSortKey> label="Ticker" sortKey="ticker" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
            {columns.structure && <SortTh<PositionSortKey> label="Structure" sortKey="structure" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />}
            {columns.qty && <SortTh<PositionSortKey> label="Qty" sortKey="qty" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />}
            {columns.direction && <SortTh<PositionSortKey> label="Direction" sortKey="direction" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />}
            {showUnderlying && <SortTh<PositionSortKey> label="Underlying" sortKey="underlying" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />}
            {columns.avg_entry && <SortTh<PositionSortKey> label="Avg Entry" sortKey="avg_entry" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />}
            {columns.last_price && <SortTh<PositionSortKey> label="Last Price" sortKey="last_price" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />}
            {showImplied && columns.implied && <SortTh<PositionSortKey> label="Implied" sortKey="implied" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />}
            {showImplied && columns.implied_market_value && <SortTh<PositionSortKey> label="Implied MV" sortKey="implied_market_value" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />}
            {columns.daily_chg && <SortTh<PositionSortKey> label="Day Chg" sortKey="daily_chg" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />}
            {columns.today_pnl && <SortTh<PositionSortKey> label="Today P&L" sortKey="today_pnl" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />}
            {columns.market_value && <SortTh<PositionSortKey> label="Market Value" sortKey="market_value" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />}
            {columns.entry_cost && <SortTh<PositionSortKey> label="Entry Cost" sortKey="entry_cost" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />}
            {columns.initial_value && <SortTh<PositionSortKey> label="Initial Value" sortKey="initial_value" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />}
            {columns.pnl && <SortTh<PositionSortKey> label="P&L" sortKey="pnl" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />}
            {showExpiry && columns.expiry && <SortTh<PositionSortKey> label="Expiry" sortKey="expiry" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />}
          </tr>
        </thead>
        <tbody>
          {sorted.map((pos) => (
            <PositionRow key={pos.id} pos={pos} showExpiry={showExpiry} showUnderlying={showUnderlying} showImplied={showImplied} columns={columns} realtimePrice={prices?.[pos.ticker]} prices={prices} riskFreeRate={riskFreeRate} onLegClick={handleLegClick} />
          ))}
        </tbody>
      </table>
      </div>

      {activeInstrument && prices && (
        <InstrumentDetailModal
          leg={activeInstrument.leg}
          ticker={activeInstrument.ticker}
          expiry={activeInstrument.expiry}
          prices={prices}
          onClose={() => setActiveInstrument(null)}
        />
      )}
    </>
  );
}
