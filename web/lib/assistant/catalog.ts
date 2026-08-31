/**
 * Runtime capability catalog for assistant list_apis / call_api.
 *
 * Derived from pin sources (FastAPI assistant_catalog.py + Next
 * radonCapability exports). Chat keeps named tools for quote / evaluate /
 * portfolio / journal / place_order; everything else goes through this catalog.
 */

import path from "node:path";

import { buildRuntimeCatalog, CALLABLE_CAPS } from "@/lib/assistant/catalogBuild";
import { loadPinSourcesFromDisk } from "@/lib/assistant/pinSources";

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

export const WATCHLIST_HINT =
  "Watchlist is GET /api/watchlist, POST /api/watchlist, DELETE /api/watchlist/{symbol}.";

const SEARCH_STOP = new Set([
  "the",
  "and",
  "for",
  "with",
  "any",
  "that",
  "this",
  "from",
  "more",
  "than",
  "days",
  "day",
  "one",
  "use",
  "api",
  "route",
]);

let cached: CatalogOperation[] | null = null;

function operations(): CatalogOperation[] {
  if (!cached) cached = buildRuntimeCatalog(loadPinSourcesFromDisk());
  return cached;
}

export function resetCatalogCache(): void {
  cached = null;
}

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

  const operation = operations().find((item) => item.method === verb && item.pattern.test(normalized));
  if (!operation) {
    return { ok: false, normalized, error: unknownPathError(rawPath, normalized) };
  }
  if (!CALLABLE_CAPS.has(operation.capability)) {
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

function searchTokens(q: string): string[] {
  return q
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter((token) => token.length >= 3 && !SEARCH_STOP.has(token));
}

function searchScore(hay: string, tokens: string[]): number {
  if (!tokens.length) return 1;
  let score = 0;
  for (const token of tokens) {
    if (hay.includes(token)) score += 1;
  }
  return score;
}

export function search(q: string): CatalogSearchHit[] {
  const tokens = searchTokens(q);
  const hits: Array<CatalogSearchHit & { score: number }> = [];
  for (const item of operations()) {
    if (!CALLABLE_CAPS.has(item.capability)) continue;
    const hay = `${item.method} ${item.path} ${item.capability} ${item.summary} ${item.input}`.toLowerCase();
    const score = searchScore(hay, tokens);
    if (!score) continue;
    hits.push({
      method: item.method,
      path: item.path,
      capability: item.capability,
      summary: item.summary,
      input: item.input,
      score,
    });
  }
  hits.sort((a, b) => b.score - a.score || a.path.localeCompare(b.path) || a.method.localeCompare(b.method));
  return hits.slice(0, 80).map(({ score: _score, ...hit }) => hit);
}

export function catalogOperations(): CatalogOperation[] {
  return operations().slice();
}
