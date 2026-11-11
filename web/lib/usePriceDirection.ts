"use client";

import { useEffect, useRef, useState } from "react";

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
