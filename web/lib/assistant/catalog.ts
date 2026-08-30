/**
 * Runtime capability catalog for assistant list_apis / call_api.
 *
 * Self-contained: does not import route annotations. Chat keeps named tools
 * for quote / evaluate / portfolio / journal / place_order; everything else
 * goes through this catalog.
 */

import path from "node:path";

export type HttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
export type Capability =
  | "read"
  | "read.spawn"
  | "mutate.workspace"
  | "mutate.trading"
  | "admin"
  | "internal";
export type Surface = "fastapi" | "next";

export type CatalogOperation = {
  method: HttpMethod;
  path: string;
  capability: Capability;
  surface: Surface;
  summary: string;
  input: string;
  pattern: RegExp;
  /**
   * The Next twin of this backend action passes `operatorOnly` to
   * requireRouteAccess; the catalog client mirrors it (R-451).
   */
  operatorOnly?: boolean;
};

export type CatalogSearchHit = {
  method: HttpMethod;
  path: string;
  capability: Capability;
  summary: string;
  input: string;
};

export type AuthorizeOk = {
  ok: true;
  capability: Capability;
  surface: Surface;
  normalized: string;
  operation: CatalogOperation;
};

export type AuthorizeDenied = {
  ok: false;
  normalized: string;
  error: string;
  capability?: Capability;
  surface?: Surface;
};

export type AuthorizeResult = AuthorizeOk | AuthorizeDenied;

const HTTP_METHODS = new Set<string>(["GET", "POST", "PUT", "PATCH", "DELETE"]);
const CALLABLE = new Set<Capability>(["read", "read.spawn", "mutate.workspace"]);
const TICKER = "[A-Z0-9.\\-]+";

export const WATCHLIST_HINT =
  "Watchlist is GET /api/watchlist, POST /api/watchlist, DELETE /api/watchlist/{symbol}.";

function compile(template: string): RegExp {
  const source = template
    .replace(/\{ticker\}/g, TICKER)
    .replace(/\{symbol\}/g, "[^/]+");
  return new RegExp(`^${source}$`);
}

function op(
  method: HttpMethod,
  pathTemplate: string,
  capability: Capability,
  surface: Surface,
  summary: string,
  input = "",
): CatalogOperation {
  return {
    method,
    path: pathTemplate,
    capability,
    surface,
    summary,
    input,
    pattern: compile(pathTemplate),
  };
}

function operatorOnly(operation: CatalogOperation): CatalogOperation {
  return { ...operation, operatorOnly: true };
}

const OPERATIONS: CatalogOperation[] = [
  op("GET", "/api/watchlist", "read", "next", "List the signed-in user's watchlist"),
  op(
    "POST",
    "/api/watchlist",
    "mutate.workspace",
    "next",
    "Add a symbol to the signed-in user's watchlist",
    "{symbol, sector?}",
  ),
  op(
    "DELETE",
    "/api/watchlist/{symbol}",
    "mutate.workspace",
    "next",
    "Remove a symbol from the signed-in user's watchlist",
    "{symbol}",
  ),

  op("GET", "/quote/{ticker}", "read", "fastapi", "Live last/bid/ask for an underlying", "{ticker}"),
  op("GET", "/options/uw-chain", "read", "fastapi", "Priced Unusual Whales option chain", "symbol, expiry?, right?"),
  op("GET", "/options/chain", "read", "fastapi", "IB option chain", "symbol, expiry?"),
  op("GET", "/options/expirations", "read", "fastapi", "Listed option expirations", "symbol"),
  op("GET", "/options/exposure/{ticker}", "read", "fastapi", "Options exposure / GEX by strike", "{ticker}"),
  op("GET", "/options/rv-ratio/{ticker}", "read", "fastapi", "Realized vs implied vol ratio", "{ticker}"),
  op("GET", "/index-options/chain", "read", "fastapi", "Index option chain", "symbol"),
  op("GET", "/catalysts", "read", "fastapi", "Upcoming catalysts"),
  op("GET", "/earnings", "read", "fastapi", "Earnings calendar"),
  op("GET", "/earnings/{ticker}", "read", "fastapi", "Earnings for one ticker", "{ticker}"),
  op("GET", "/informed-flow/{ticker}", "read", "fastapi", "Informed-flow prints", "{ticker}"),
  op("GET", "/short-availability/{ticker}", "read", "fastapi", "Stock-loan availability", "{ticker}"),
  op("GET", "/ticker/ratings", "read", "fastapi", "Analyst ratings", "symbol"),
  op("GET", "/market-calendar", "read", "fastapi", "Session calendar"),
  op("GET", "/internals/skew-history", "read", "fastapi", "Skew history"),
  op("GET", "/cash-flows", "read", "fastapi", "Account cash flows"),
  op("GET", "/attribution", "read", "fastapi", "P&L attribution"),
  op("GET", "/llm-token-index", "read", "fastapi", "LLM token index"),
  op("GET", "/flow-analysis/{ticker}", "read", "fastapi", "Dark-pool / OTC flow analysis", "{ticker}"),

  op("POST", "/flow-analysis/{ticker}", "read.spawn", "fastapi", "Run flow analysis and persist a snapshot", "{ticker}"),
  op("POST", "/scan", "read.spawn", "fastapi", "Market-wide flow scan"),
  op("POST", "/discover", "read.spawn", "fastapi", "Discovery scan"),
  op("POST", "/gex/scan", "read.spawn", "fastapi", "Gamma exposure scan"),
  op("POST", "/vcg/scan", "read.spawn", "fastapi", "Vol-credit gap scan"),
  op("POST", "/regime/scan", "read.spawn", "fastapi", "Regime scan"),
  op("POST", "/breadth/scan", "read.spawn", "fastapi", "Breadth scan"),
  // Capabilities below follow scripts/api/assistant_catalog.py and the Next
  // twin's radonCapability (assistant-catalog-parity.test.ts).
  operatorOnly(op("POST", "/portfolio/sync", "mutate.workspace", "fastapi", "Refresh IB portfolio snapshot")),
  operatorOnly(op("POST", "/orders/refresh", "mutate.workspace", "fastapi", "Refresh open-order snapshot")),
  operatorOnly(op("POST", "/performance", "read.spawn", "fastapi", "Performance snapshot")),
  op("POST", "/leap/scan", "read.spawn", "fastapi", "LEAP IV-mispricing scan"),
  op("POST", "/garch-convergence/scan", "read.spawn", "fastapi", "GARCH convergence scan"),
  op("POST", "/theta-harvester/scan", "read.spawn", "fastapi", "Theta-harvester scan"),
  op("POST", "/strength-confirmation/scan", "read.spawn", "fastapi", "Strength-confirmation scan"),
  op("POST", "/gamma-rotation/scan", "read.spawn", "fastapi", "Gamma-rotation scan"),
  op("POST", "/bpi/scan", "read.spawn", "fastapi", "BPI scan"),
];

function fullyDecode(value: string): string {
  let current = value;
  for (let i = 0; i < 4; i += 1) {
    try {
      const next = decodeURIComponent(current);
      if (next === current) break;
      current = next;
    } catch {
      break;
    }
  }
  return current;
}

/**
 * SSRF-safe path: must start with /, reject :// and //host, POSIX-resolve
 * `.` / `..`, strip trailing slashes except root, drop the query before match.
 */
export function normalizePath(raw: string): string {
  if (typeof raw !== "string") return "";
  const trimmed = raw.trim();
  if (!trimmed.startsWith("/")) return "";
  if (trimmed.includes("://")) return "";
  const [withoutQuery] = trimmed.split("?");
  if (!withoutQuery.startsWith("/") || withoutQuery.startsWith("//")) return "";

  const decoded = fullyDecode(withoutQuery);
  if (decoded.includes("://") || decoded.startsWith("//") || decoded.includes("\0")) return "";

  const resolved = path.posix.normalize(decoded);
  if (!resolved.startsWith("/") || resolved.startsWith("//") || resolved.includes("://")) return "";
  if (resolved.includes("\\") || resolved.includes("\0")) return "";
  if (resolved.length > 1 && resolved.endsWith("/")) {
    return resolved.replace(/\/+$/, "") || "/";
  }
  return resolved;
}

function prefixMatch(normalized: string, exact: string): boolean {
  return normalized === exact || normalized.startsWith(`${exact}/`);
}

function classifyDenied(normalized: string): { capability: Capability; error: string } | null {
  const p = normalized.toLowerCase();
  if (
    /^\/orders\/(place|cancel|modify)$/.test(p)
    || prefixMatch(p, "/trading")
    || prefixMatch(p, "/paper")
    || p === "/api/orders/place"
  ) {
    return { capability: "mutate.trading", error: "Trading mutations are refused. Use place_order." };
  }
  if (prefixMatch(p, "/admin") || prefixMatch(p, "/ib") || prefixMatch(p, "/api/admin")) {
    return { capability: "admin", error: "Admin APIs are refused." };
  }
  if (p === "/pi/exec") {
    return { capability: "internal", error: "POST /pi/exec is refused. Use run_evaluate." };
  }
  if (
    prefixMatch(p, "/knowledge")
    || prefixMatch(p, "/api/pi")
    || prefixMatch(p, "/api/assistant")
  ) {
    return { capability: "internal", error: "Internal APIs are refused." };
  }
  return null;
}

function unknownPathError(raw: string, normalized: string): string {
  const blob = `${raw} ${normalized}`.toLowerCase();
  const base = `Unknown path ${raw}.`;
  if (blob.includes("watchlist")) return `${base} ${WATCHLIST_HINT}`;
  return `${base} Use list_apis to discover valid paths.`;
}

export function authorize(method: string, rawPath: string): AuthorizeResult {
  const verb = method.trim().toUpperCase();
  const normalized = normalizePath(rawPath);
  if (!normalized) {
    return { ok: false, normalized: "", error: "Invalid path." };
  }
  if (!HTTP_METHODS.has(verb)) {
    return { ok: false, normalized, error: `Unsupported method: ${verb}` };
  }

  const denied = classifyDenied(normalized);
  if (denied) {
    return { ok: false, normalized, capability: denied.capability, error: denied.error };
  }

  const operation = OPERATIONS.find((item) => item.method === verb && item.pattern.test(normalized));
  if (!operation) {
    return { ok: false, normalized, error: unknownPathError(rawPath, normalized) };
  }
  if (!CALLABLE.has(operation.capability)) {
    return {
      ok: false,
      normalized,
      capability: operation.capability,
      surface: operation.surface,
      error: "Path is not allowed.",
    };
  }
  return {
    ok: true,
    capability: operation.capability,
    surface: operation.surface,
    normalized,
    operation,
  };
}

export function search(q: string): CatalogSearchHit[] {
  const tokens = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const hits: CatalogSearchHit[] = [];
  for (const item of OPERATIONS) {
    if (!CALLABLE.has(item.capability)) continue;
    const hay = `${item.method} ${item.path} ${item.capability} ${item.summary} ${item.input}`.toLowerCase();
    if (tokens.length && !tokens.every((token) => hay.includes(token))) continue;
    hits.push({
      method: item.method,
      path: item.path,
      capability: item.capability,
      summary: item.summary,
      input: item.input,
    });
  }
  return hits.slice(0, 40);
}

export function catalogOperations(): CatalogOperation[] {
  return OPERATIONS.slice();
}
