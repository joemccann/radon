"use client";

import { useMemo, useState } from "react";

export type SortDirection = "asc" | "desc";

export type SortState<K extends string = string> = {
  key: K | null;
  direction: SortDirection;
};

type ValueExtractor<T, K extends string> = (item: T, key: K) => string | number | null | undefined;

export function useSort<T, K extends string>(
  data: readonly T[] | T[],
  extractValue: ValueExtractor<T, K>,
  defaultKey?: NoInfer<K>,
  defaultDirection: SortDirection = "asc",
) {
  const [sort, setSort] = useState<SortState<K>>({
    key: defaultKey ?? null,
    direction: defaultDirection,
  });

  const toggle = (key: K) => {
    setSort((prev) => {
      if (prev.key === key) {
        return { key, direction: prev.direction === "asc" ? "desc" : "asc" };
      }
      return { key, direction: "asc" };
    });
  };

  const sorted = useMemo(() => {
    if (!sort.key) return [...data];

    const key = sort.key;
    const dir = sort.direction === "asc" ? 1 : -1;

    return [...data].sort((a, b) => {
      const va = extractValue(a, key);
      const vb = extractValue(b, key);

      // nulls last
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;

      if (typeof va === "number" && typeof vb === "number") {
        return (va - vb) * dir;
      }

      return String(va).localeCompare(String(vb)) * dir;
    });
  }, [data, sort.key, sort.direction, extractValue]);

  return { sorted, sort, toggle };
}
