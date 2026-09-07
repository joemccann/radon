"use client";

import type { OpenOrder, PortfolioData, PortfolioPosition } from "@/lib/types";
import type { PriceData, FundamentalsData, DepthBook, Trade } from "@/lib/pricesProtocol";
import type { QuoteFallback } from "@/lib/quoteTelemetry";
import { useViewport } from "@/lib/useViewport";
import { useTickerDetailOptional, type OrderPrefill } from "@/lib/TickerDetailContext";
import { MetricCell } from "@/components/mobile/MetricCell";
import { resolveMarketValue, resolveEntryCost, getAvgEntry, fmtPrice } from "@/lib/positionUtils";
import { fmtMoneySigned } from "@/lib/format/money";
import { isIndexSymbol } from "@/lib/indexSymbols";
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
export type DeckKey = "c" | "p" | "n" | "r" | "s" | "i" | "h" | "f" | ":" | "o";

export type AssetCockpitProps = {
  ticker: string;
  position: PortfolioPosition | null;
  prices: Record<string, PriceData>;
  fundamentals: Record<string, FundamentalsData>;
  portfolio: PortfolioData | null;
  depths?: Record<string, DepthBook>;
  tape?: Record<string, Trade[]>;
  bookKey: string;
  bookKind: "stock" | "option" | "future" | "combo";
  /** L1 quote for the exact subject rendered in the Book pane. */
  bookPriceData?: PriceData | null;
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
  /** Instrument switch (held single-leg option ⟷ underlying stock). */
  instrumentView: "position" | "underlying";
  canSwitchInstrument: boolean;
  onInstrumentViewChange: (view: "position" | "underlying") => void;
  /** True while the underlying stock is in focus instead of the held option. */
  viewUnderlying: boolean;
};

/** Condensed 2x2 position summary shown on mobile above the tab strip. */
function MobilePositionSummary({ position }: { position: PortfolioPosition }) {
  const mv = resolveMarketValue(position);
  const ec = resolveEntryCost(position);
  const pnl = mv != null && ec != null ? mv - ec : null;
  const pnlTone = pnl == null ? "mut" : pnl > 0 ? "pos" : pnl < 0 ? "neg" : "mut";
  // Signed per the credit/debit convention: a credit combo's avg entry reads
  // NEGATIVE (the operator was paid to open). `getAvgEntry` owns the leg-count
  // and instrument scoping — never re-derive it with Math.abs here.
  const avgEntryRaw = position.contracts > 0 ? getAvgEntry(position) : null;
  const avgEntry = avgEntryRaw == null
    ? "---"
    : `${avgEntryRaw < 0 ? "-" : ""}${fmtPrice(Math.abs(avgEntryRaw))}`;

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
  bookPriceData,
  quotePriceData,
  priceData,
  isSpreadNet,
  tickerOrders,
  activeDeck,
  onDeckChange,
  instrumentView,
  canSwitchInstrument,
  onInstrumentViewChange,
  viewUnderlying,
}: AssetCockpitProps) {
  const live = (quotePriceData?.bid != null && quotePriceData?.ask != null) || quotePriceData?.last != null;
  const ticketPriceData = isIndexSymbol(ticker) ? prices[ticker] ?? null : priceData;

  // When the underlying is in focus, the order ticket acts on the STOCK (a
  // fresh order), not the held option — OrderTab renders its linear stock form
  // for a null position. The held option stays available everywhere else
  // (header chip, ACT summary, POSN deck).
  const ticketPosition = viewUnderlying ? null : position;

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
    <div className={`cockpit cockpit-host ${mobile ? "cockpit--mobile" : ""}`} data-testid="cockpit-host">
      <CockpitHeader
        ticker={ticker}
        kind={bookKind}
        quotePriceData={quotePriceData}
        isSpreadNet={isSpreadNet}
        position={position}
        live={Boolean(live)}
        onDeckChange={onDeckChange}
        instrumentView={instrumentView}
        canSwitchInstrument={canSwitchInstrument}
        onInstrumentViewChange={onInstrumentViewChange}
      />

      {/* Mobile: condensed 2x2 position summary just below the header strip,
          visible at a glance before the trader dives into the book. */}
      {mobile && position && <MobilePositionSummary position={position} />}

      {/* BOOK — montage/ladder + tape, full height; sole home of bid/ask depth. */}
      <div className="book-region">
        <BookTab
          ticker={ticker}
          position={ticketPosition}
          prices={prices}
          openOrders={tickerOrders}
          tickerPriceData={bookPriceData === undefined ? priceData : bookPriceData}
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
          <div className="act-ticket" data-testid="act-ticket">
            <OrderTab
              ticker={ticker}
              position={ticketPosition}
              portfolio={portfolio}
              prices={prices}
              openOrders={tickerOrders}
              tickerPriceData={ticketPriceData}
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
        tickerPriceData={ticketPriceData}
      />

      <GlyphRail activeDeck={activeDeck} onDeckChange={onDeckChange} includeOrder={mobile} />
    </div>
  );
}
