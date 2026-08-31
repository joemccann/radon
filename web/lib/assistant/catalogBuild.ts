/**
 * Derive the assistant runtime catalog from pin sources.
 *
 * Policy (adversarially chosen so list_apis cannot advertise a path
 * call_api will not honour, and a new pin cannot ship invisible):
 *
 * 1. FastAPI callable pins (read / read.spawn / mutate.workspace) enter
 *    unless the path is prefix-denied (knowledge, health, docs, ...).
 * 2. Next callable pins enter only when they have no FastAPI twin.
 *    /api/streaks is a thin proxy of GET /streaks/{ticker}; chat uses
 *    the FastAPI path. Watchlist has no FastAPI twin, so it stays Next.
 * 3. operatorOnly: Next method body that passes operatorOnly, FastAPI
 *    mutate.workspace, and FastAPI performance/portfolio sync twins.
 */

import type { Capability, CatalogOperation, HttpMethod, Surface } from "@/lib/assistant/catalog";
import {
  methodHasOperatorOnly,
  slotPath,
  type PinRecord,
  type PinSources,
} from "@/lib/assistant/pinSources";

export const CALLABLE_CAPS = new Set<Capability>(["read", "read.spawn", "mutate.workspace"]);

const DENY_PREFIXES = [
  "/knowledge",
  "/docs",
  "/redoc",
  "/openapi.json",
  "/health",
  "/share",
  "/ws-ticket",
  "/demo",
  "/admin",
  "/ib",
  "/trading",
  "/pi",
  "/paper",
  "/api/admin",
  "/api/pi",
  "/api/assistant",
  "/api/knowledge",
  "/api/webhooks",
];

const TICKER = "[A-Z0-9.\\-]+";

const SUMMARY_OVERRIDES: Record<string, string> = {
  "GET /api/watchlist": "List the signed-in user's watchlist",
  "POST /api/watchlist": "Add a symbol to the signed-in user's watchlist",
  "DELETE /api/watchlist/{symbol}": "Remove a symbol from the signed-in user's watchlist",
  "GET /streaks/{ticker}": "Consecutive daily win streak for one ticker",
};

const INPUT_OVERRIDES: Record<string, string> = {
  "POST /api/watchlist": "{symbol, sector?}",
  "DELETE /api/watchlist/{symbol}": "{symbol}",
  "GET /streaks/{ticker}": "{ticker}",
};

function prefixDenied(path: string): boolean {
  const lower = path.toLowerCase();
  return DENY_PREFIXES.some((prefix) => lower === prefix || lower.startsWith(`${prefix}/`));
}

export function isAdvertisedPin(pin: PinRecord): boolean {
  if (!CALLABLE_CAPS.has(pin.capability)) return false;
  if (prefixDenied(pin.path)) return false;
  return true;
}

function twinCandidates(nextPath: string): string[] {
  const stripped = nextPath.replace(/^\/api/, "") || "/";
  const base = stripped.startsWith("/") ? stripped : `/${stripped}`;
  const swapped = base.replace("{ticker}", "{symbol}").replace("{symbol}", "{ticker}");
  const out = [base];
  if (swapped !== base) out.push(swapped);
  if (!base.includes("{")) {
    out.push(`${base}/{ticker}`, `${base}/{symbol}`);
  }
  return out;
}

export function findFastApiTwin(nextPath: string, method: HttpMethod, fastapi: PinRecord[]): PinRecord | undefined {
  const slots = new Map(fastapi.map((pin) => [`${pin.method} ${slotPath(pin.path)}`, pin]));
  for (const candidate of twinCandidates(nextPath)) {
    const hit = slots.get(`${method} ${slotPath(candidate)}`);
    if (hit) return hit;
  }
  return undefined;
}

function autoSummary(method: HttpMethod, path: string): string {
  const key = `${method} ${path}`;
  if (SUMMARY_OVERRIDES[key]) return SUMMARY_OVERRIDES[key];
  const words = path
    .split("/")
    .filter((part) => part && !part.startsWith("{"))
    .join(" ")
    .replace(/-/g, " ");
  return words || path;
}

function autoInput(path: string, method: HttpMethod): string {
  const key = `${method} ${path}`;
  if (INPUT_OVERRIDES[key]) return INPUT_OVERRIDES[key];
  const params = [...path.matchAll(/\{([^}]+)\}/g)].map((hit) => hit[1]);
  return params.length ? `{${params.join(", ")}}` : "";
}

export function compilePath(template: string): RegExp {
  const source = template.replace(/\{ticker\}/g, TICKER).replace(/\{[^}]+\}/g, "[^/]+");
  return new RegExp(`^${source}$`);
}

function isOperatorOnly(pin: PinRecord): boolean {
  if (pin.surface === "next") return methodHasOperatorOnly(pin.source, pin.method);
  if (pin.capability === "mutate.workspace") return true;
  return (
    pin.path === "/performance"
    || pin.path === "/performance/background"
    || pin.path === "/portfolio/sync"
    || pin.path === "/portfolio/background-sync"
    || pin.path === "/orders/refresh"
  );
}

function toOperation(pin: PinRecord): CatalogOperation {
  return {
    method: pin.method,
    path: pin.path,
    capability: pin.capability,
    surface: pin.surface as Surface,
    summary: autoSummary(pin.method, pin.path),
    input: autoInput(pin.path, pin.method),
    pattern: compilePath(pin.path),
    ...(isOperatorOnly(pin) ? { operatorOnly: true } : {}),
  };
}

export function advertisedPins(sources: PinSources): PinRecord[] {
  const advertised: PinRecord[] = [];
  for (const pin of sources.fastapi) {
    if (isAdvertisedPin(pin)) advertised.push(pin);
  }
  for (const pin of sources.next) {
    if (!isAdvertisedPin(pin)) continue;
    if (findFastApiTwin(pin.path, pin.method, sources.fastapi)) continue;
    advertised.push(pin);
  }
  advertised.sort((a, b) => a.path.localeCompare(b.path) || a.method.localeCompare(b.method));
  return advertised;
}

export function buildRuntimeCatalog(sources: PinSources): CatalogOperation[] {
  return advertisedPins(sources).map(toOperation);
}

export function nextOnlyRouteIds(sources: PinSources): string[] {
  const ids = new Set<string>();
  for (const pin of advertisedPins(sources)) {
    if (pin.surface === "next" && pin.routeId) ids.add(pin.routeId);
  }
  return [...ids].sort();
}
