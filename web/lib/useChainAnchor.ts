"use client";

import { useCallback, useMemo, useState } from "react";
import { findAtmStrike } from "@/lib/optionsChainUtils";

type ChainAnchorInput = {
  ticker: string;
  expiry: string | null;
  strikes: number[];
  currentPrice: number | null;
  strikesPerSide: number;
  focusKey?: string;
  focusStrike?: number | null;
};

function validPrice(value: number | null | undefined): number | null {
  return value != null && Number.isFinite(value) && value > 0 ? value : null;
}

export function useChainAnchor({
  ticker, expiry, strikes, currentPrice, strikesPerSide, focusKey = "", focusStrike,
}: ChainAnchorInput) {
  const contextKey = JSON.stringify([ticker, expiry, strikesPerSide, focusKey]);
  const ready = expiry != null && strikes.length > 0;
  const initialPrice = validPrice(focusStrike) ?? validPrice(currentPrice);
  const [anchor, setAnchor] = useState(() => ({
    contextKey,
    price: ready ? initialPrice : null,
    initialized: ready,
    browsing: false,
    version: 0,
  }));

  if (anchor.contextKey !== contextKey || (ready && (!anchor.initialized || (
    anchor.price == null && initialPrice != null && !anchor.browsing
  )))) {
    setAnchor({
      contextKey,
      price: ready ? initialPrice : null,
      initialized: ready,
      browsing: false,
      version: anchor.version + 1,
    });
  }

  const markBrowsing = useCallback(() => {
    setAnchor(previous => previous.browsing ? previous : { ...previous, browsing: true });
  }, []);

  const recenter = useCallback(() => {
    const price = validPrice(currentPrice);
    if (!ready || price == null) return;
    setAnchor(previous => ({
      contextKey,
      price,
      initialized: true,
      browsing: false,
      version: previous.version + 1,
    }));
  }, [contextKey, currentPrice, ready]);

  const anchorStrike = useMemo(() => anchor.price != null ? findAtmStrike(strikes, anchor.price) : null, [strikes, anchor.price]);

  return {
    anchorPrice: anchor.price,
    anchorStrike,
    revision: `${anchor.contextKey}:${anchor.version}`,
    markBrowsing,
    recenter,
  };
}
