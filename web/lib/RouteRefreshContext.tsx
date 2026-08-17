"use client";

import { createContext, useContext, useMemo } from "react";
import { usePathname } from "next/navigation";

/**
 * Pathname-only route key. Search-param churn (chain UI, newsfeed tags)
 * must not retrigger portfolio/orders/scanner producers.
 */
export const RouteRefreshContext = createContext<string>("");

export function RouteRefreshProvider({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "";
  const routeKey = useMemo(() => pathname, [pathname]);
  return (
    <RouteRefreshContext.Provider value={routeKey}>
      {children}
    </RouteRefreshContext.Provider>
  );
}

export function useRouteRefreshKey(): string {
  return useContext(RouteRefreshContext);
}
