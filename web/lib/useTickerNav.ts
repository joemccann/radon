"use client";

import { useCallback } from "react";
import { useRouter } from "next/navigation";
import { tickerHref } from "@/lib/tickerRoute";

export function useTickerNav() {
  const router = useRouter();

  const navigateToTicker = useCallback(
    (ticker: string, positionId?: number) => {
      router.push(tickerHref(ticker, positionId));
    },
    [router],
  );

  return { navigateToTicker };
}
