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
} from "@/lib/positionUtils";

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

function getOptionRtMv(pos: PortfolioPosition, prices?: Record<string, PriceData>): number | null {
  if (pos.structure_type === "Stock") return null;
  let rtMv = 0;
  for (const leg of pos.legs) {
    const key = legPriceKey(pos.ticker, pos.expiry, leg);
    const lp = key && prices ? prices[key] : null;
    const last = (lp?.last != null && lp.last > 0) ? lp.last : (leg.market_price != null && leg.market_price > 0 ? leg.market_price : null);
    if (last == null) return null;
    const sign = leg.direction === "LONG" ? 1 : -1;
    rtMv += sign * last * leg.contracts * 100;
  }
  return rtMv;
}

function getTodayPnlDollars(pos: PortfolioPosition, prices?: Record<string, PriceData>): number | null {
  if (pos.structure_type === "Stock") {
    const p = prices?.[pos.ticker];
    if (!p || p.last == null || p.last <= 0 || p.close == null || p.close <= 0) return null;
    return (p.last - p.close) * pos.contracts;
  }
  // Prefer IB's per-position daily P&L (handles intraday additions correctly)
  if (pos.ib_daily_pnl != null) return pos.ib_daily_pnl;
  // Fall back to WS close-based calculation
  let pnl = 0;
  let hasClose = false;
  for (const leg of pos.legs) {
    const key = legPriceKey(pos.ticker, pos.expiry, leg);
    const lp = key && prices ? prices[key] : null;
    const last = (lp?.last != null && lp.last > 0) ? lp.last : (leg.market_price != null && leg.market_price > 0 ? leg.market_price : null);
    if (last == null) return null;
    const close = lp?.close;
    if (close != null && close > 0) {
      const sign = leg.direction === "LONG" ? 1 : -1;
      pnl += sign * (last - close) * leg.contracts * 100;
      hasClose = true;
    }
  }
  return hasClose ? pnl : null;
}

/* ─── Sort extract factory ─────────────────────────────── */

export type PositionSortKey = "ticker" | "structure" | "qty" | "direction" | "underlying" | "avg_entry" | "last_price" | "daily_chg" | "today_pnl" | "entry_cost" | "market_value" | "pnl" | "expiry";

function makePositionExtract(prices?: Record<string, PriceData>) {
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
      case "daily_chg": return isStock ? getDailyChange(prices?.[pos.ticker]) : getOptionDailyChg(pos, prices);
      case "today_pnl": return getTodayPnlDollars(pos, prices);
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
  realtimeLegPrice,
  onLegClick,
}: {
  leg: PortfolioPosition["legs"][number];
  showExpiry: boolean;
  showUnderlying?: boolean;
  realtimeLegPrice?: PriceData | null;
  onLegClick?: (leg: PortfolioLeg) => void;
}) {
  const rtLast = realtimeLegPrice?.last != null && realtimeLegPrice.last > 0 ? realtimeLegPrice.last : null;
  const marketPrice = rtLast ?? (leg.market_price != null ? Math.abs(leg.market_price) : null);
  const isCalculated = rtLast != null ? Boolean(realtimeLegPrice?.lastIsCalculated) : Boolean(leg.market_price_is_calculated);
  const { direction: priceDirection, flashDirection } = usePriceDirection(marketPrice);

  // Per-leg P&L: sign-aware (MV - EC)
  const mult = leg.type === "Stock" ? 1 : 100;
  const legMv = rtLast != null ? rtLast * leg.contracts * mult : leg.market_value != null ? Math.abs(leg.market_value) : null;
  const legEc = Math.abs(leg.entry_cost);
  const sign = leg.direction === "LONG" ? 1 : -1;
  const legPnl = legMv != null ? sign * (legMv - legEc) : null;

  return (
    <tr className={flashDirection ? `last-price-${flashDirection}` : undefined}>
      <td></td>
      <td
        colSpan={3}
        className={`cell-indent cell-muted ${onLegClick ? "leg-clickable" : ""}`}
        onClick={onLegClick ? () => onLegClick(leg) : undefined}
      >
        {leg.direction} {leg.contracts}x {leg.type}{leg.strike ? ` $${leg.strike}` : ""}
      </td>
      {showUnderlying && <td></td>}
      <td className="right cell-muted">{fmtPrice(Math.abs(leg.avg_cost) / (leg.type === "Stock" ? 1 : 100))}</td>
      <td className="right last-price-cell">
        {marketPrice != null ? fmtPriceOrCalculated(marketPrice, isCalculated) : "—"}
        {priceDirection === "up" && <ArrowUp size={11} className="price-trend-icon price-trend-up" aria-label="price up" />}
        {priceDirection === "down" && <ArrowDown size={11} className="price-trend-icon price-trend-down" aria-label="price down" />}
      </td>
      <td></td>
      <td></td>
      <td className="right cell-muted">{fmtPrice(legEc)}</td>
      <td className="right cell-muted">{rtLast != null ? fmtUsd(rtLast * leg.contracts * mult) : leg.market_value != null ? fmtUsd(Math.abs(leg.market_value)) : "—"}</td>
      <td className={`right cell-muted ${legPnl != null ? (legPnl >= 0 ? "positive" : "negative") : ""}`}>
        {legPnl != null ? `${legPnl >= 0 ? "+" : "-"}${fmtUsd(Math.abs(legPnl))}` : "—"}
      </td>
      {showExpiry && <td></td>}
    </tr>
  );
}

/* ─── Position row ─────────────────────────────────────── */

function PositionRow({ pos, showExpiry = true, showStrike = false, showUnderlying = false, realtimePrice, prices, onLegClick }: { pos: PortfolioPosition; showExpiry?: boolean; showStrike?: boolean; showUnderlying?: boolean; realtimePrice?: PriceData | null; prices?: Record<string, PriceData>; onLegClick?: (leg: PortfolioLeg, pos: PortfolioPosition) => void }) {
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
    for (const leg of pos.legs) {
      const key = legPriceKey(pos.ticker, pos.expiry, leg);
      const lp = key && prices ? prices[key] : null;
      // Use WS last, fall back to synced market_price
      const last = (lp?.last != null && lp.last > 0) ? lp.last : (leg.market_price != null && leg.market_price > 0 ? leg.market_price : null);
      if (last == null) return null;
      const sign = leg.direction === "LONG" ? 1 : -1;
      rtMv += sign * last * leg.contracts * 100;
      const close = lp?.close;
      if (close != null && close > 0) {
        rtDailyPnl += sign * (last - close) * leg.contracts * 100;
        rtCloseValue += sign * close * leg.contracts * 100;
        hasCloseData = true;
      }
    }
    return { mv: rtMv, dailyPnl: hasCloseData ? rtDailyPnl : null, closeValue: rtCloseValue };
  }, [isStock, prices, pos.legs, pos.ticker, pos.expiry]);

  const mv = rtLast != null ? rtLast * pos.contracts : optionsRt?.mv ?? resolveMarketValue(pos);
  const entryCost = resolveEntryCost(pos);
  const pnl = mv != null ? mv - entryCost : null;
  const pnlPct = pnl != null && entryCost !== 0 ? (pnl / Math.abs(entryCost)) * 100 : null;
  const avgEntry = getAvgEntry(pos);
  const lastPrice = rtLast ?? (optionsRt ? mv! / (pos.contracts * getMultiplier(pos)) : getLastPrice(pos));
  const lastPriceIsCalculated = rtLast != null || optionsRt != null ? false : getLastPriceIsCalculated(pos);
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

  const dailyChg = isStock
    ? getDailyChange(realtimePrice)
    : effectiveDailyPnl != null && wsCloseValue !== 0
      ? (effectiveDailyPnl / Math.abs(wsCloseValue)) * 100
      : null;

  // Today's P&L in dollars — prefer IB's authoritative number
  const todayPnl = isStock
    ? (realtimePrice?.last != null && realtimePrice.last > 0 && realtimePrice?.close != null && realtimePrice.close > 0
        ? (realtimePrice.last - realtimePrice.close) * pos.contracts
        : null)
    : effectiveDailyPnl;

  // For single-leg options, show strike in structure column
  const isSingleLegOption = pos.legs.length === 1 && pos.structure_type !== "Stock";
  const singleLegStrike = isSingleLegOption && pos.legs[0]?.strike ? pos.legs[0].strike : null;
  const structureDisplay = showStrike && singleLegStrike
    ? `${pos.structure} $${singleLegStrike}`
    : pos.structure;

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
        <td>{structureDisplay}</td>
        <td className="right">{pos.contracts}</td>
        <td>
          <span className={`pill ${pos.risk_profile === "defined" ? "defined" : pos.risk_profile === "equity" ? "neutral" : "undefined"}`}>
            {pos.direction}
          </span>
        </td>
        {showUnderlying && (
          <td className={`right last-price-cell ${underlyingFlash ? `last-price-${underlyingFlash}` : ""}`}>
            {underlyingPrice != null ? fmtPrice(underlyingPrice) : "—"}
            {underlyingDirection === "up" && <ArrowUp size={11} className="price-trend-icon price-trend-up" aria-label="underlying up" />}
            {underlyingDirection === "down" && <ArrowDown size={11} className="price-trend-icon price-trend-down" aria-label="underlying down" />}
          </td>
        )}
        <td className="right">{fmtPrice(avgEntry)}</td>
        <td className={`right last-price-cell ${flashDirection ? `last-price-${flashDirection}` : ""}`}>
          {lastPrice != null ? fmtPriceOrCalculated(lastPrice, lastPriceIsCalculated) : "—"}
          {priceDirection === "up" && <ArrowUp size={11} className="price-trend-icon price-trend-up" aria-label="price up" />}
          {priceDirection === "down" && <ArrowDown size={11} className="price-trend-icon price-trend-down" aria-label="price down" />}
        </td>
        <td className={`right ${dailyChg != null ? (dailyChg >= 0 ? "positive" : "negative") : ""}`}>
          {dailyChg != null ? `${dailyChg >= 0 ? "+" : ""}${dailyChg.toFixed(2)}%` : "—"}
        </td>
        <td className={`right ${todayPnl != null ? (todayPnl >= 0 ? "positive" : "negative") : ""}`}>
          {todayPnl != null ? `${todayPnl >= 0 ? "+" : "-"}${fmtUsd(Math.abs(todayPnl))}` : "—"}
        </td>
        <td className="right">{fmtUsd(entryCost)}</td>
        <td className="right">{mv != null ? fmtUsd(mv) : "—"}</td>
        <td className={`right ${pnl != null ? (pnl >= 0 ? "positive" : "negative") : ""}`}>
          {pnl != null ? `${pnl >= 0 ? "+" : "-"}${fmtUsd(Math.abs(pnl))} (${pnlPct!.toFixed(1)}%)` : "—"}
        </td>
        {showExpiry && <td>{pos.expiry !== "N/A" ? pos.expiry : "—"}</td>}
      </tr>
      {hasMultipleLegs && legsExpanded && pos.legs.map((leg, i) => {
        const key = legPriceKey(pos.ticker, pos.expiry, leg);
        return (
          <LegRow
            key={`${pos.id}-leg-${i}`}
            leg={leg}
            showExpiry={showExpiry}
            showUnderlying={showUnderlying}
            realtimeLegPrice={key && prices ? prices[key] : null}
            onLegClick={onLegClick ? (l) => onLegClick(l, pos) : undefined}
          />
        );
      })}
    </>
  );
}

/* ─── Position table ───────────────────────────────────── */

export default function PositionTable({ positions, showExpiry = true, showStrike = false, showUnderlying = false, prices }: { positions: PortfolioPosition[]; showExpiry?: boolean; showStrike?: boolean; showUnderlying?: boolean; prices?: Record<string, PriceData> }) {
  const positionExtract = useMemo(() => makePositionExtract(prices), [prices]);
  const { sorted, sort, toggle } = useSort(positions, positionExtract);

  // Instrument detail modal state
  const [activeInstrument, setActiveInstrument] = useState<{ leg: PortfolioLeg; ticker: string; expiry: string } | null>(null);

  const handleLegClick = useCallback((leg: PortfolioLeg, pos: PortfolioPosition) => {
    setActiveInstrument({ leg, ticker: pos.ticker, expiry: pos.expiry });
  }, []);

  return (
    <>
      <table>
        <thead>
          <tr>
            <SortTh<PositionSortKey> label="Ticker" sortKey="ticker" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
            <SortTh<PositionSortKey> label="Structure" sortKey="structure" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
            <SortTh<PositionSortKey> label="Qty" sortKey="qty" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
            <SortTh<PositionSortKey> label="Direction" sortKey="direction" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
            {showUnderlying && <SortTh<PositionSortKey> label="Underlying" sortKey="underlying" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />}
            <SortTh<PositionSortKey> label="Avg Entry" sortKey="avg_entry" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
            <SortTh<PositionSortKey> label="Last Price" sortKey="last_price" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
            <SortTh<PositionSortKey> label="Day Chg" sortKey="daily_chg" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
            <SortTh<PositionSortKey> label="Today P&L" sortKey="today_pnl" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
            <SortTh<PositionSortKey> label="Entry Cost" sortKey="entry_cost" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
            <SortTh<PositionSortKey> label="Market Value" sortKey="market_value" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
            <SortTh<PositionSortKey> label="P&L" sortKey="pnl" className="right" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />
            {showExpiry && <SortTh<PositionSortKey> label="Expiry" sortKey="expiry" activeKey={sort.key} direction={sort.direction} onToggle={toggle} />}
          </tr>
        </thead>
        <tbody>
          {sorted.map((pos) => (
            <PositionRow key={pos.id} pos={pos} showExpiry={showExpiry} showStrike={showStrike} showUnderlying={showUnderlying} realtimePrice={prices?.[pos.ticker]} prices={prices} onLegClick={handleLegClick} />
          ))}
        </tbody>
      </table>

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
