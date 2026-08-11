"use client";

import { useEffect } from "react";
import type { OpenOrder, PortfolioData, PortfolioPosition } from "@/lib/types";
import type { PriceData, FundamentalsData } from "@/lib/pricesProtocol";
import type { DeckKey } from "./AssetCockpit";
import OptionsChainTab from "./OptionsChainTab";
import PositionTab from "./PositionTab";
import OrderTab from "./OrderTab";
import NewsTab from "./NewsTab";
import RatingsTab from "./RatingsTab";
import SeasonalityTab from "./SeasonalityTab";
import CompanyTab from "./CompanyTab";
import SmartMoney13FPanel from "@/components/equibles/SmartMoney13FPanel";
import FilingForensicsDossier from "@/components/equibles/FilingForensicsDossier";

const DECK_TITLE: Record<DeckKey, string> = {
  c: "Chain",
  p: "Position — Full",
  n: "News",
  r: "Ratings",
  s: "Seasonal",
  i: "Info / Company",
  h: "13F Smart Money",
  f: "Filing Forensics",
  ":": "Command Palette",
  o: "Order Ticket",
};

/** Keys that open a deck via single-keystroke. */
const OPEN_KEYS = new Set<string>(["c", "p", "n", "r", "s", "i", "h", "f", ":"]);

/**
 * Decks whose content is too wide for the 36% act column and so fly out across
 * the book + act cells (rail stays visible). The options chain has ~10 columns
 * (Δ / IV / Implied / Vol / Bid / Mid / Ask / Last / Strike, both sides) and was
 * truncating + forcing a horizontal scroll when pinned to the act column.
 */
const WIDE_DECKS = new Set<DeckKey>(["c"]);

function isTypingTarget(el: Element | null): boolean {
  if (!el) return false;
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  return (el as HTMLElement).isContentEditable === true;
}

type AssetDeckProps = {
  activeDeck: DeckKey | null;
  onDeckChange: (deck: DeckKey | null) => void;
  ticker: string;
  prices: Record<string, PriceData>;
  fundamentals: Record<string, FundamentalsData>;
  portfolio: PortfolioData | null;
  position: PortfolioPosition | null;
  quotePriceData: PriceData | null;
  /** Open orders + resolved price data for the `o` (order ticket) deck — the
   *  mobile entry to the ticket that lives in the desktop act column. */
  openOrders?: OpenOrder[];
  tickerPriceData?: PriceData | null;
};

export default function AssetDeck({
  activeDeck,
  onDeckChange,
  ticker,
  prices,
  fundamentals,
  portfolio,
  position,
  quotePriceData,
  openOrders,
  tickerPriceData,
}: AssetDeckProps) {
  // Single-key deck open + Esc close. Guarded so the keys never fire while the
  // user is typing in the order ticket fields (Qty / Limit / TIF).
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (isTypingTarget(document.activeElement)) return;

      if (e.key === "Escape") {
        if (activeDeck != null) {
          e.preventDefault();
          onDeckChange(null);
        }
        return;
      }

      if (OPEN_KEYS.has(e.key)) {
        e.preventDefault();
        onDeckChange(activeDeck === (e.key as DeckKey) ? null : (e.key as DeckKey));
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [activeDeck, onDeckChange]);

  const open = activeDeck != null;
  const wide = activeDeck != null && WIDE_DECKS.has(activeDeck);
  const title = activeDeck ? DECK_TITLE[activeDeck] : "";

  return (
    <div
      className={`asset-deck ${open ? "open" : ""} ${wide ? "asset-deck--wide" : ""}`}
      aria-hidden={!open}
    >
      <div className="asset-deck-hd">
        <span>{title}</span>
        <button type="button" className="asset-deck-x" onClick={() => onDeckChange(null)}>
          esc ✕
        </button>
      </div>
      <div className="asset-deck-body">
        {activeDeck === "c" && (
          <OptionsChainTab
            ticker={ticker}
            prices={prices}
            tickerPriceData={prices[ticker] ?? null}
            focusPosition={position ?? null}
            focusPositionRequested={position != null}
            portfolio={portfolio}
          />
        )}
        {activeDeck === "p" &&
          (position ? (
            <PositionTab position={position} prices={prices} portfolio={portfolio} />
          ) : (
            <div className="asset-deck-empty">No position</div>
          ))}
        {activeDeck === "o" && (
          <OrderTab
            ticker={ticker}
            position={position}
            portfolio={portfolio}
            prices={prices}
            openOrders={openOrders ?? []}
            tickerPriceData={tickerPriceData ?? null}
          />
        )}
        {activeDeck === "n" && <NewsTab ticker={ticker} active={open} />}
        {activeDeck === "r" && (
          <RatingsTab
            ticker={ticker}
            active={open}
            currentPrice={prices[ticker]?.last ?? quotePriceData?.last}
          />
        )}
        {activeDeck === "s" && <SeasonalityTab ticker={ticker} active={open} />}
        {activeDeck === "h" && <SmartMoney13FPanel ticker={ticker} />}
        {activeDeck === "f" && <FilingForensicsDossier ticker={ticker} active={open} />}
        {activeDeck === "i" && (
          <CompanyTab
            ticker={ticker}
            active={open}
            priceData={prices[ticker] ?? null}
            fundamentals={fundamentals[ticker] ?? null}
          />
        )}
        {activeDeck === ":" && (
          <div className="asset-deck-palette">
            <div className="asset-deck-ph">Command Palette</div>
            <p>
              Accelerator only: jump-to-ticker, quick orders, :chart. Never the sole path to
              any surface.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
