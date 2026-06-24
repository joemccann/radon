"use client";

import { useEffect, useMemo, useState } from "react";
import type { OpenOrder, PortfolioPosition, PortfolioData, OrdersData } from "@/lib/types";
import type { PriceData, FundamentalsData, DepthBook, Trade } from "@/lib/pricesProtocol";
import { legPriceKey, resolveSpreadPriceData } from "@/lib/positionUtils";
import { isIndexSymbol, hasFuturesSupport } from "@/lib/indexSymbols";
import { isFuturesRoot } from "@/lib/futuresSymbols";
import { deriveBookHeader } from "@/lib/book/depthDerivations";
import { isDeckKey } from "@/lib/legacyTabToDeck";
import AssetCockpit, { type DeckKey } from "./ticker-detail/AssetCockpit";
import { useStockState } from "@/lib/useStockState";
import { useTickerDetailOptional } from "@/lib/TickerDetailContext";

/**
 * Resolve the best price data for the shared ticker quote telemetry wrapper.
 * - Stock positions → underlying ticker price
 * - Single-leg option → option contract price (bid/ask from WS), with
 *   `last` falling back to IB's calculated mark (the position's
 *   `market_price`) when the option is illiquid and the WS stream
 *   delivers no last/bid (common for thin strikes — only ask reaches
 *   the relay, so BID/MID/LAST would otherwise read `---`).
 * - Multi-leg → net spread price computed from per-leg WS bid/ask (falls back to underlying)
 * - No position → underlying ticker price
 */
function mergeCalculatedMark(
  priceData: PriceData | undefined,
  position: PortfolioPosition,
): PriceData | null {
  const leg = position.legs[0];
  // `market_price` on the leg is IB's model mark (sync writes it on every
  // /portfolio/sync). `market_price_is_calculated=true` means it is NOT
  // a live trade. Surface it as `last` only when the live stream has no
  // last of its own; never overwrite a real last-trade tick.
  const fallbackLast =
    leg?.market_price != null && leg.market_price > 0 ? leg.market_price : null;

  if (!priceData) {
    if (fallbackLast == null) return null;
    return {
      symbol: position.ticker,
      last: fallbackLast,
      lastIsCalculated: true,
      bid: null,
      ask: null,
      bidSize: null,
      askSize: null,
      volume: null,
      high: null,
      low: null,
      open: null,
      close: null,
      timestamp: new Date().toISOString(),
    } as PriceData;
  }

  if (priceData.last != null && priceData.last > 0) return priceData;
  if (fallbackLast == null) return priceData;

  return { ...priceData, last: fallbackLast, lastIsCalculated: true };
}

function resolveTickerQuoteTelemetry(
  ticker: string,
  position: PortfolioPosition | null,
  prices: Record<string, PriceData>,
): { priceData: PriceData | null; label?: string; priceKey?: string; isSpreadNet?: boolean } {
  if (!position || position.structure_type === "Stock") {
    return { priceData: prices[ticker] ?? null };
  }

  // Single-leg option: use option-level prices, merged with the
  // portfolio's calculated mark as a `last` fallback for illiquid
  // contracts whose WS feed only delivers an ASK.
  if (position.legs.length === 1) {
    const leg = position.legs[0];
    const key = legPriceKey(ticker, position.expiry, leg);
    if (key) {
      const merged = mergeCalculatedMark(prices[key], position);
      if (merged) {
        const strike = leg.strike ? `$${leg.strike}` : "";
        const type = leg.type === "Call" ? "C" : leg.type === "Put" ? "P" : "";
        return {
          priceData: merged,
          priceKey: key,
          label: `${ticker} ${position.expiry} ${strike} ${type}`,
        };
      }
    }
  }

  // Multi-leg: compute net spread price from per-leg WS prices. The resolved
  // value is a SPREAD NET (signed: credit negative, debit positive per the
  // Sign Convention) — not a raw share/contract price — so flag it for the
  // chart and quote bar to label honestly.
  const spreadData = resolveSpreadPriceData(ticker, position, prices);
  if (spreadData) {
    return { priceData: spreadData, label: `${ticker} ${position.structure}`, isSpreadNet: true };
  }

  // Fallback to underlying if leg prices unavailable
  return { priceData: prices[ticker] ?? null, label: `${ticker} (underlying)` };
}

function hasOptionLeg(position: PortfolioPosition | null): boolean {
  return Boolean(
    position?.legs.some((leg) => leg.type === "Call" || leg.type === "Put"),
  );
}

export type TickerDetailContentProps = {
  ticker: string;
  positionId?: number | null;
  activeTab: string;
  onTabChange: (tab: string) => void;
  prices: Record<string, PriceData>;
  fundamentals: Record<string, FundamentalsData>;
  portfolio: PortfolioData | null;
  orders: OrdersData | null;
  /** Depth-of-book keyed by symbol, from the same `usePrices` call. */
  depths?: Record<string, DepthBook>;
  /** Time & Sales tape keyed by symbol, from the same `usePrices` call. */
  tape?: Record<string, Trade[]>;
  /** Publish the resolved focused book key upstream so `usePrices` subscribes
   *  L2 depth for exactly the open subject (null releases the ticket). */
  onDepthSymbolChange?: (key: string | null) => void;
  theme: "dark" | "light";
};

export default function TickerDetailContent({
  ticker,
  positionId,
  activeTab,
  onTabChange,
  prices,
  fundamentals,
  portfolio,
  orders,
  depths,
  tape,
  onDepthSymbolChange,
  theme,
}: TickerDetailContentProps) {
  const position: PortfolioPosition | null = useMemo(() => {
    if (!portfolio) return null;
    if (positionId != null) {
      return portfolio.positions.find((p) => p.id === positionId) ?? null;
    }
    return portfolio.positions.find((p) => p.ticker === ticker) ?? null;
  }, [ticker, positionId, portfolio]);

  // Instrument switch: an equity option position otherwise locks the header /
  // quote surface to either the held option/spread or the underlying stock with
  // no obvious way back. Offer a STOCK ⟷ OPTION toggle for any held equity
  // option structure, including ratios/reversals. Index/futures options are
  // excluded because their "underlying" is not an equity stock. Resets to the
  // held view on every ticker change.
  const [instrumentView, setInstrumentView] = useState<"position" | "underlying">("position");
  useEffect(() => setInstrumentView("position"), [ticker]);
  const canSwitchInstrument =
    hasOptionLeg(position) &&
    !isFuturesRoot(ticker) &&
    !(isIndexSymbol(ticker) && hasFuturesSupport(ticker));
  const viewUnderlying = instrumentView === "underlying" && canSwitchInstrument;

  const tickerOrders: OpenOrder[] = useMemo(() => {
    if (!orders) return [];
    return orders.open_orders.filter((o) => o.contract.symbol === ticker);
  }, [ticker, orders]);

  const { priceData, priceKey: chartPriceKey, isSpreadNet } = useMemo(
    () =>
      viewUnderlying
        ? { priceData: prices[ticker] ?? null, priceKey: undefined, isSpreadNet: false }
        : resolveTickerQuoteTelemetry(ticker, position, prices),
    [ticker, position, prices, viewUnderlying],
  );

  // The focused subject's depth book key: a user-pinned leg (e.g. a combo leg's
  // option, set from the position deck) wins; else the single-leg option price
  // key; else the ticker. Published upstream so `usePrices` opens the one scarce
  // depth ticket for exactly this subject.
  const focusedBookKey = useTickerDetailOptional()?.focusedBookKey ?? null;
  // Viewing the underlying forces the stock subject, overriding the held
  // option's leg key (and any pinned combo leg).
  const bookKey = viewUnderlying ? ticker : focusedBookKey ?? chartPriceKey ?? ticker;
  const bookDepth = depths?.[bookKey] ?? null;

  // Prefer the depth book's NBBO for the quote bar when an entitled book is
  // streaming for this subject. The separate L1 priceData feed can deliver
  // corrupt scalars for some instruments (e.g. MU showed negative bid/ask)
  // while the depth book is correct; deriveBookHeader is the same source the
  // OrderBook header uses, so the two never diverge. Falls through to raw
  // priceData (incl. the closed-market fallback) when there is no entitled book.
  const quotePriceData = useMemo<PriceData | null>(() => {
    if (!priceData || isSpreadNet || !bookDepth || bookDepth.entitled !== true) return priceData;
    const head = deriveBookHeader(bookDepth, {
      bid: priceData.bid,
      ask: priceData.ask,
      last: priceData.last,
      lastLabel: priceData.lastIsCalculated ? "MARK" : "LAST",
    });
    return {
      ...priceData,
      bid: head.bid,
      ask: head.ask,
      last: head.last,
      lastIsCalculated: head.lastLabel !== "LAST",
    };
  }, [priceData, isSpreadNet, bookDepth]);

  // Resolve instrument kind for the depth panel. depth.kind wins when the relay
  // has classified it; else a relay-supported futures root (ES/NQ/...) or an
  // index with futures support is a future; else a single-leg non-stock
  // position is an option; else a stock. The static futures-root list is the
  // pre-depth hint so the page subscribes depth and routes to the ladder before
  // any DepthBook has arrived; once it does, depth.kind is authoritative.
  const bookKind: "stock" | "option" | "future" = bookDepth?.kind
    ?? (isFuturesRoot(ticker) || (isIndexSymbol(ticker) && hasFuturesSupport(ticker))
      ? "future"
      : !viewUnderlying && position && position.structure_type !== "Stock" && position.legs.length === 1
        ? "option"
        : "stock");

  useEffect(() => {
    onDepthSymbolChange?.(bookKey);
    return () => onDepthSymbolChange?.(null);
  }, [bookKey, onDepthSymbolChange]);

  // After-hours fallback for the quote bar: when the box shows the UNDERLYING
  // (no position, or a stock position) and the live WS feed is dark, source
  // OHLV/close from the UW stock-state instead of rendering "No real-time data".
  // Never applied to option/spread quotes — stock-state is the stock's own OHLV.
  const showsUnderlying = viewUnderlying || !position || position.structure_type === "Stock";
  const { fallback: stockFallback } = useStockState(ticker, showsUnderlying);

  // Deck-key contract (shared with TickerWorkspace via `@/lib/legacyTabToDeck`).
  // TickerWorkspace passes the DECK KEY straight through `activeTab` (the deck
  // from `?deck=`, or "book" when no deck is open) and its `onTabChange` accepts
  // a deck key (or "book" for the docked hot-path). So this side speaks deck keys
  // too — no word-form translation. Earlier this file translated to/from word
  // forms ("chain"/"position"/...), which TickerWorkspace's isDeckKey rejected,
  // so every glyph click resolved to setDeck(null) and the deck never opened.
  //
  // `:` (Cmd) and `o` (Order ticket, mobile) are the AssetCockpit decks NOT in
  // VALID_DECKS — they are not URL-addressable, so they live in local component
  // state. Every other key (c/p/n/r/s/i) flows through the URL via onTabChange.
  const [localDeck, setLocalDeck] = useState<DeckKey | null>(null);
  const urlDeck: DeckKey | null = isDeckKey(activeTab) ? activeTab : null;
  const activeDeck: DeckKey | null = urlDeck ?? localDeck;

  const onDeckChange = (deck: DeckKey | null) => {
    // Local-only decks have no URL form: drive them from local state and clear
    // any URL deck so the two can't both be "open".
    if (deck === ":" || deck === "o") {
      if (urlDeck) onTabChange("book");
      setLocalDeck(deck);
      return;
    }
    // URL-addressable decks (incl. close → null) flow straight through as deck
    // keys; TickerWorkspace.onTabChange maps "book"/null back to no deck.
    setLocalDeck(null);
    onTabChange(deck ?? "book");
  };

  // The cockpit is the single ticker-detail layout on every viewport. It adapts
  // internally: desktop = book + act column + glyph rail; mobile = book-first
  // with a horizontal glyph strip and full-screen decks (see AssetCockpit).
  return (
    <AssetCockpit
      ticker={ticker}
      position={position}
      prices={prices}
      fundamentals={fundamentals}
      portfolio={portfolio}
      depths={depths}
      tape={tape}
      bookKey={bookKey}
      bookKind={bookKind}
      quotePriceData={quotePriceData}
      priceData={priceData}
      isSpreadNet={isSpreadNet}
      tickerOrders={tickerOrders}
      stockFallback={stockFallback}
      theme={theme}
      activeDeck={activeDeck}
      onDeckChange={onDeckChange}
      instrumentView={instrumentView}
      canSwitchInstrument={canSwitchInstrument}
      onInstrumentViewChange={setInstrumentView}
      viewUnderlying={viewUnderlying}
    />
  );
}
