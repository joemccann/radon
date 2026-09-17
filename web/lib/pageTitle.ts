import type { Metadata } from "next";
import { REGIME_TAB_LABEL, type RegimeTab } from "./regimeRail";
import { isTickerRouteSegment } from "./tickerRoute";

export const DEFAULT_DOCUMENT_TITLE = "Radon Terminal";
export const TITLE_TEMPLATE = "%s · Radon";

export const STATIC_ROUTE_TITLES: Record<string, string> = {
  "/": "Portfolio",
  "/dashboard": "Portfolio",
  "/portfolio": "Positions",
  "/performance": "Performance",
  "/orders": "Orders",
  "/scanner": "Scanner",
  "/discover": "Discover",
  "/watchlist": "Watchlist",
  "/research-workbench": "Research workbench",
  "/flow-analysis": "Flow Analysis",
  "/options": "Options",
  "/options/net-gex": "Net GEX",
  "/options/exposure": "Net GEX",
  "/options/rv-ratio": "Rel Vol",
  "/journal": "Journal",
  "/regime": "Regime",
  "/regime/vol-cone": "Vol Cone",
  "/cta": "CTA",
  "/alerts": "Alerts",
  "/admin": "Operator",
  "/preferences": "Preferences",
  "/profile": "Profile",
  "/kit": "Kit",
  "/setup": "Setup",
  "/sign-in": "Sign in",
  "/sign-up": "Sign up",
  "/demo-pending": "Setting up your demo",
  "/trial-expired": "Demo ended",
  "/internals": "Regime",
};

const SCANNER_MODE_TITLES: Record<string, string> = {
  flow: "Scanner",
  discover: "Discover",
  theta: "Theta Harvester",
  strength: "7-Step Strength",
  leap: "LEAP",
  garch: "GARCH",
  "vol-cone": "Vol Cone",
  "vol-skew-mr": "Vol/Skew MR",
};

// Match the Options pages: letter first, up to ten letters/digits/dots/hyphens.
const OPTIONS_SYMBOL_RE = /^[A-Za-z][A-Za-z0-9.-]{0,9}$/;

export function pageMetadata(title: string): Metadata {
  return { title };
}

export function routeMetadata(
  pathname: string,
  search?: { get(name: string): string | null },
): Metadata {
  return pageMetadata(titleForPathname(pathname, search));
}

export function documentTitle(page: string): string {
  return TITLE_TEMPLATE.replace("%s", page);
}

export function searchFromRecord(
  record?: Record<string, string | string[] | undefined>,
): URLSearchParams {
  const search = new URLSearchParams();
  if (!record) return search;
  for (const [key, raw] of Object.entries(record)) {
    const value = Array.isArray(raw) ? raw[0] : raw;
    if (value) search.set(key, value);
  }
  return search;
}

export async function metadataForPath(
  pathname: string,
  searchParams?: Promise<Record<string, string | string[] | undefined>>,
): Promise<Metadata> {
  const record = searchParams ? await searchParams : undefined;
  return routeMetadata(pathname, searchFromRecord(record));
}

export function titleForPathname(
  pathname: string,
  search?: { get(name: string): string | null },
): string {
  const cut = pathname.indexOf("?");
  const rawPath = cut >= 0 ? pathname.slice(0, cut) : pathname;
  const path = rawPath.replace(/\/+$/, "") || "/";

  const mode = search?.get("mode") ?? "";
  const rawSymbol = search?.get("symbol") ?? "";
  const symbol = OPTIONS_SYMBOL_RE.test(rawSymbol) ? rawSymbol.toUpperCase() : "";

  if (path === "/scanner" && Object.prototype.hasOwnProperty.call(SCANNER_MODE_TITLES, mode)) {
    return SCANNER_MODE_TITLES[mode];
  }

  if ((path === "/options/net-gex" || path === "/options/exposure") && symbol) {
    return `${symbol} Net GEX`;
  }
  if (path === "/options/rv-ratio" && symbol) {
    return `${symbol} Rel Vol`;
  }

  const flow = path.match(/^\/flow-analysis\/([A-Za-z]{1,5})$/);
  if (flow) return `${flow[1].toUpperCase()} Flow`;

  const regime = path.match(/^\/regime\/([^/]+)$/);
  if (regime && Object.prototype.hasOwnProperty.call(REGIME_TAB_LABEL, regime[1])) {
    return REGIME_TAB_LABEL[regime[1] as RegimeTab];
  }

  if (STATIC_ROUTE_TITLES[path]) return STATIC_ROUTE_TITLES[path];

  const segment = path.slice(1);
  if (
    segment.length > 0
    && !segment.includes("/")
    && isTickerRouteSegment(segment)
  ) {
    return segment.toUpperCase();
  }

  return DEFAULT_DOCUMENT_TITLE;
}
