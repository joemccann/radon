"use client";

import type { OpenOrder, PortfolioData, PortfolioPosition } from "@/lib/types";
import type { PriceData, FundamentalsData, DepthBook, Trade } from "@/lib/pricesProtocol";
import type { QuoteFallback } from "@/lib/quoteTelemetry";
import { useViewport } from "@/lib/useViewport";
import { useTickerDetailOptional, type OrderPrefill } from "@/lib/TickerDetailContext";
import { MetricCell } from "@/components/mobile/MetricCell";
import { resolveMarketValue, resolveEntryCost, fmtPrice } from "@/lib/positionUtils";
import { fmtMoneySigned } from "@/lib/format/money";
import BookTab from "./BookTab";
import OrderTab from "./OrderTab";
import ActHeldSummary from "./ActHeldSummary";
import CockpitHeader from "./CockpitHeader";
import GlyphRail from "./GlyphRail";
import AssetDeck from "./AssetDeck";

/** Deck keys map 1:1 to the glyph rail + URL deck param.
 *  `:` (command palette) and `o` (order ticket) are local-only — not in
 *  VALID_DECKS, so they never reach the URL. `o` is the mobile entry to the
 *  order ticket, which on desktop lives in the always-visible act column. */
export type DeckKey = "c" | "p" | "n" | "r" | "s" | "i" | ":" | "o";

export type AssetCockpitProps = {
  ticker: string;
  position: PortfolioPosition | null;
  prices: Record<string, PriceData>;
  fundamentals: Record<string, FundamentalsData>;
  portfolio: PortfolioData | null;
  depths?: Record<string, DepthBook>;
  tape?: Record<string, Trade[]>;
  bookKey: string;
  bookKind: "stock" | "option" | "future";
  /** Depth-NBBO-corrected quote; single source for the header scalars. */
  quotePriceData: PriceData | null;
  /** Resolved option/underlying price data threaded to the ticket + book. */
  priceData: PriceData | null;
  isSpreadNet?: boolean;
  tickerOrders: OpenOrder[];
  /** After-hours OHLV fallback (header already prefers depth-NBBO; reserved). */
  stockFallback?: QuoteFallback | null;
  theme: "dark" | "light";
  activeDeck: DeckKey | null;
  onDeckChange: (deck: DeckKey | null) => void;
};

/** Condensed 2x2 position summary shown on mobile above the tab strip. */
function MobilePositionSummary({ position }: { position: PortfolioPosition }) {
  const mv = resolveMarketValue(position);
  const ec = resolveEntryCost(position);
  const pnl = mv != null ? mv - ec : null;
  const pnlTone = pnl == null ? "mut" : pnl > 0 ? "pos" : pnl < 0 ? "neg" : "mut";
  const avgEntry = position.contracts > 0
    ? fmtPrice(Math.abs(ec) / (position.contracts * (position.structure_type === "Stock" ? 1 : 100)))
    : "---";

  return (
    <div className="ckp-pos-summary">
      <MetricCell label="Structure" value={position.structure} size="secondary" />
      <MetricCell label="Qty" value={`${position.direction} ${position.contracts}x`} size="secondary" />
      <MetricCell label="Avg Entry" value={avgEntry} size="secondary" />
      <MetricCell
        label="P&L"
        value={pnl != null ? fmtMoneySigned(pnl) : "---"}
        size="secondary"
        tone={pnlTone}
      />
    </div>
  );
}

export default function AssetCockpit({
  ticker,
  position,
  prices,
  fundamentals,
  portfolio,
  depths,
  tape,
  bookKey,
  bookKind,
  quotePriceData,
  priceData,
  isSpreadNet,
  tickerOrders,
  activeDeck,
  onDeckChange,
}: AssetCockpitProps) {
  const live = (quotePriceData?.bid != null && quotePriceData?.ask != null) || quotePriceData?.last != null;

  // Mobile folds the act column into the deck system: there is no room for a
  // permanent ticket beside the book on a phone, so the book fills the screen,
  // the glyph rail runs horizontally along the bottom (thumb-reachable) with an
  // added order glyph, and the ticket / position open as full-screen decks.
  // Gate on `isMobile && hasMounted` (the app-wide convention) so the SSR /
  // desktop-fallback markup never flips layout mid-hydration.
  const { isMobile, hasMounted } = useViewport();
  const mobile = isMobile && hasMounted;

  // Click-to-fill: a depth level / tape print click publishes its price (and an
  // unambiguous side) to the order ticket via TickerDetailContext. The ticket
  // (act column on desktop, `o`-deck on mobile) consumes it on a nonce-keyed
  // effect. On mobile the ticket isn't visible beside the book, so also open
  // the `o` deck. Optional context → no-op when rendered outside the provider.
  const ticker_ctx = useTickerDetailOptional();
  const onBookPriceClick = (p: Omit<OrderPrefill, "nonce">) => {
    ticker_ctx?.setOrderPrefill(p);
    if (mobile) onDeckChange("o");
  };

  return (
    <div className={`cockpit cockpit-host ${mobile ? "cockpit--mobile" : ""}`}>
      <CockpitHeader
        ticker={ticker}
        kind={bookKind}
        quotePriceData={quotePriceData}
        isSpreadNet={isSpreadNet}
        position={position}
        live={Boolean(live)}
        onDeckChange={onDeckChange}
      />

      {/* Mobile: condensed 2x2 position summary just below the header strip,
          visible at a glance before the trader dives into the book. */}
      {mobile && position && <MobilePositionSummary position={position} />}

      {/* BOOK — montage/ladder + tape, full height; sole home of bid/ask depth. */}
      <div className="book-region">
        <BookTab
          ticker={ticker}
          position={position}
          prices={prices}
          openOrders={tickerOrders}
          tickerPriceData={priceData}
          depths={depths}
          tape={tape}
          bookKey={bookKey}
          bookKind={bookKind}
          portfolio={portfolio}
          bookOnly
          onPriceClick={onBookPriceClick}
        />
      </div>

      {/* ACT — desktop only. Ticket-focused, mirroring the flat futures view: the
          order ticket fills the top; below it a centered affordance (the "No
          position" cue when flat, or a one-line held summary linking to the
          p-deck). On mobile this column is dropped — the ticket opens as the `o`
          deck and the position as the `p` deck instead. Full position detail
          (legs / P&L cards / close-out) always lives in the p-deck. */}
      {!mobile && (
        <div className="act-region">
          <div className="act-ticket">
            <OrderTab
              ticker={ticker}
              position={position}
              portfolio={portfolio}
              prices={prices}
              openOrders={tickerOrders}
              tickerPriceData={priceData}
            />
          </div>
          <div className="act-position">
            {position ? (
              <ActHeldSummary position={position} onOpenDeck={() => onDeckChange("p")} />
            ) : (
              <div className="act-flat">
                <span>No position</span>
                <span className="act-flat-hint">Ticket opens one ↑</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Deck is a .cockpit grid child (sibling of book/act/rail). On desktop,
          narrow decks pin to the `act` cell and the wide chain deck spans book +
          act (rail stays visible); on mobile every deck is a full-screen overlay
          over the book. The order ticket is threaded so the `o` deck can host it
          on mobile. Grid children fill their cell — no transform / inset math; the
          reveal is opacity-only. */}
      <AssetDeck
        activeDeck={activeDeck}
        onDeckChange={onDeckChange}
        ticker={ticker}
        prices={prices}
        fundamentals={fundamentals}
        portfolio={portfolio}
        position={position}
        quotePriceData={quotePriceData}
        openOrders={tickerOrders}
        tickerPriceData={priceData}
      />

      <GlyphRail activeDeck={activeDeck} onDeckChange={onDeckChange} includeOrder={mobile} />
    </div>
  );
}
