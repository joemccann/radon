"use client";

import { useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { useTickerDetail } from "@/lib/TickerDetailContext";
import { isDeckKey, legacyTabToDeck, type DeckKey } from "@/lib/legacyTabToDeck";
import TickerDetailContent from "./TickerDetailContent";

type TickerWorkspaceProps = {
  ticker: string;
  theme: "dark" | "light";
};

export default function TickerWorkspace({ ticker, theme }: TickerWorkspaceProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  // portfolio/orders are reactive context state (must re-render when WorkspaceShell
  // syncs after client navigation). High-frequency feeds stay ref snapshots.
  const {
    getPrices,
    getFundamentals,
    portfolio,
    orders,
    getDepths,
    getTape,
    setDepthSymbol,
  } = useTickerDetail();

  const prices = getPrices();
  const fundamentals = getFundamentals();
  const depths = getDepths();
  const tape = getTape();

  // Deck model: `?deck=<c|p|n|r|s|i>` opens a reference deck; no deck = book-first
  // landing (book + ticket + position docked, no overlay). Legacy `?tab=` values
  // are mapped through legacyTabToDeck so old links/bookmarks still resolve.
  const rawDeck = searchParams.get("deck");
  const activeDeck: DeckKey | null = isDeckKey(rawDeck)
    ? rawDeck
    : legacyTabToDeck(searchParams.get("tab"));
  const positionId = searchParams.get("posId") ? Number(searchParams.get("posId")) : null;

  // Deck change → router.replace (no history pollution). null clears the param
  // (lands on the bare book). Drop the legacy `tab` param on any deck change so
  // the deck param is the single source of truth going forward.
  const setDeck = useCallback((deck: DeckKey | null) => {
    const params = new URLSearchParams(searchParams.toString());
    params.delete("tab");
    if (deck == null) {
      params.delete("deck");
    } else {
      params.set("deck", deck);
    }
    const qs = params.toString();
    router.replace(`/${ticker}${qs ? `?${qs}` : ""}`, { scroll: false });
  }, [router, ticker, searchParams]);

  // Cross-track contract: TickerDetailContent's signature still takes
  // `activeTab`/`onTabChange`, but the components track treats the value as a
  // deck key. We feed deck semantics through those existing prop NAMES so neither
  // track breaks regardless of landing order:
  //   - activeTab carries the deck key, or "book" when no deck is open.
  //   - onTabChange receives a deck key (or "book"/"company"/"order" for the
  //     always-docked hot-path surfaces) and maps the docked ones back to null.
  const activeTabValue = activeDeck ?? "book";
  const onTabChange = useCallback((value: string) => {
    if (value === "book" || value === "company" || value === "order") {
      setDeck(null);
      return;
    }
    setDeck(isDeckKey(value) ? value : null);
  }, [setDeck]);

  return (
    <div className="ticker-detail-page">
      <button className="ticker-back-nav" onClick={() => router.back()}>
        <ArrowLeft size={14} /> Back
      </button>

      <TickerDetailContent
        ticker={ticker}
        positionId={positionId}
        activeTab={activeTabValue}
        onTabChange={onTabChange}
        prices={prices}
        fundamentals={fundamentals}
        portfolio={portfolio}
        orders={orders}
        depths={depths}
        tape={tape}
        onDepthSymbolChange={setDepthSymbol}
        theme={theme}
      />
    </div>
  );
}
