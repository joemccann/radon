/**
 * Pin sources the assistant runtime catalog is derived from.
 *
 * FastAPI: scripts/api/assistant_catalog.py (pytest pins every app.routes
 * entry). Next: export const radonCapability on each App Router route file
 * (vitest pins every disk route). There is no third handwritten OPERATIONS
 * list; list_apis / call_api read whatever these sources currently say.
 */

import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import type { Capability, HttpMethod } from "@/lib/assistant/catalog";

export type PinRecord = {
  method: HttpMethod;
  path: string;
  capability: Capability;
  surface: "fastapi" | "next";
  /** Next App Router folder under app/api, e.g. watchlist/[symbol]. */
  routeId?: string;
  /** Raw route.ts(x) source; used to detect operatorOnly per method. */
  source?: string;
};

export type PinSources = {
  fastapi: PinRecord[];
  next: PinRecord[];
  root: string;
};

const HTTP_METHODS: readonly HttpMethod[] = ["GET", "POST", "PUT", "PATCH", "DELETE"];
const METHOD_SET = new Set<string>(HTTP_METHODS);
const CAPABILITY_RE =
  /^(read|read\.spawn|mutate\.workspace|mutate\.trading|admin|internal)$/;

const PIN_ENTRY =
  /\(\s*"([A-Z]+)"\s*,\s*"([^"]+)"\s*\)\s*:\s*"([a-z.]+)"/g;
const CAPABILITY_EXPORT =
  /export\s+const\s+radonCapability(?:\s*:\s*[^=;]+)?\s*=\s*([^;]+);/;
const HANDLER_EXPORT =
  /export\s+(?:async\s+)?(?:function|const)\s+(GET|POST|PUT|PATCH|DELETE)\b/g;

export function slotPath(template: string): string {
  return template.replace(/\{[^}]+\}/g, "{}");
}

export function nextPathFromRouteId(routeId: string): string {
  return `/api/${routeId.replace(/\[([^\]]+)\]/g, "{$1}")}`;
}

export function routeIdFromNextPath(pathTemplate: string): string {
  return pathTemplate.replace(/^\/api\//, "").replace(/\{([^}]+)\}/g, "[$1]");
}

export function parseFastApiCatalog(source: string): PinRecord[] {
  const out: PinRecord[] = [];
  const seen = new Set<string>();
  PIN_ENTRY.lastIndex = 0;
  let hit: RegExpExecArray | null;
  while ((hit = PIN_ENTRY.exec(source))) {
    const method = hit[1];
    const path = hit[2];
    const capability = hit[3];
    if (!METHOD_SET.has(method) || !CAPABILITY_RE.test(capability)) continue;
    const key = `${method} ${path}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({
      method: method as HttpMethod,
      path,
      capability: capability as Capability,
      surface: "fastapi",
    });
  }
  return out;
}

function parseCapabilityExpr(expr: string): Capability | Partial<Record<HttpMethod, Capability>> | null {
  const trimmed = expr.trim().replace(/\s+as const$/, "").trim();
  const asString = trimmed.match(/^["']([a-z.]+)["']$/);
  if (asString && CAPABILITY_RE.test(asString[1])) {
    return asString[1] as Capability;
  }
  if (!trimmed.startsWith("{") || !trimmed.endsWith("}")) return null;
  const map: Partial<Record<HttpMethod, Capability>> = {};
  const pair =
    /\b(GET|POST|PUT|PATCH|DELETE)\s*:\s*["'](read|read\.spawn|mutate\.workspace|mutate\.trading|admin|internal)["']/g;
  let hit: RegExpExecArray | null;
  while ((hit = pair.exec(trimmed))) {
    map[hit[1] as HttpMethod] = hit[2] as Capability;
  }
  return Object.keys(map).length ? map : null;
}

function exportedHandlers(source: string): HttpMethod[] {
  const found = new Set<HttpMethod>();
  HANDLER_EXPORT.lastIndex = 0;
  let hit: RegExpExecArray | null;
  while ((hit = HANDLER_EXPORT.exec(source))) {
    found.add(hit[1] as HttpMethod);
  }
  return HTTP_METHODS.filter((method) => found.has(method));
}

export function parseNextRoute(routeId: string, source: string): PinRecord[] {
  const match = source.match(CAPABILITY_EXPORT);
  if (!match) return [];
  const parsed = parseCapabilityExpr(match[1]);
  if (!parsed) return [];
  const path = nextPathFromRouteId(routeId);
  const handlers = exportedHandlers(source);
  const methods: Array<{ method: HttpMethod; capability: Capability }> = [];
  if (typeof parsed === "string") {
    const used = handlers.length ? handlers : (["GET"] as HttpMethod[]);
    for (const method of used) methods.push({ method, capability: parsed });
  } else {
    for (const method of HTTP_METHODS) {
      const capability = parsed[method];
      if (capability) methods.push({ method, capability });
    }
  }
  return methods.map((item) => ({
    ...item,
    path,
    surface: "next" as const,
    routeId,
    source,
  }));
}

export function collectNextRouteIds(apiRoot: string): string[] {
  const found: string[] = [];
  function walk(dir: string, routePath: string): void {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        walk(join(dir, entry.name), routePath ? `${routePath}/${entry.name}` : entry.name);
      } else if (/^route\.(ts|tsx)$/.test(entry.name)) {
        found.push(routePath);
      }
    }
  }
  walk(apiRoot, "");
  return found.sort();
}

function readNextRouteFile(apiRoot: string, routeId: string): string {
  const ts = join(apiRoot, routeId, "route.ts");
  const tsx = join(apiRoot, routeId, "route.tsx");
  if (existsSync(ts)) return readFileSync(ts, "utf8");
  return readFileSync(tsx, "utf8");
}

export function parseNextApp(apiRoot: string): PinRecord[] {
  const out: PinRecord[] = [];
  for (const routeId of collectNextRouteIds(apiRoot)) {
    out.push(...parseNextRoute(routeId, readNextRouteFile(apiRoot, routeId)));
  }
  return out;
}

function isRepoRoot(root: string): boolean {
  return (
    existsSync(join(root, "scripts/api/assistant_catalog.py"))
    && existsSync(join(root, "web/app/api"))
  );
}

export function resolveRepoRoot(): string {
  const here = dirname(fileURLToPath(import.meta.url));
  const candidates = [
    resolve(here, "../../.."),
    resolve(process.cwd(), ".."),
    process.cwd(),
    resolve(process.cwd(), "../.."),
  ];
  for (const root of candidates) {
    if (isRepoRoot(root)) return root;
  }
  throw new Error("assistant catalog pin sources not found (assistant_catalog.py + web/app/api)");
}

export function loadPinSourcesFromDisk(root = resolveRepoRoot()): PinSources {
  const python = readFileSync(join(root, "scripts/api/assistant_catalog.py"), "utf8");
  return {
    fastapi: parseFastApiCatalog(python),
    next: parseNextApp(join(root, "web/app/api")),
    root,
  };
}

export function methodHasOperatorOnly(source: string | undefined, method: HttpMethod): boolean {
  if (!source) return false;
  const marker = `export async function ${method}`;
  const alt = `export function ${method}`;
  const start = source.indexOf(marker) >= 0 ? source.indexOf(marker) : source.indexOf(alt);
  if (start < 0) return source.includes("operatorOnly: true");
  const rest = source.slice(start);
  const nextExport = rest.search(/\nexport\s+/);
  const body = nextExport >= 0 ? rest.slice(0, nextExport) : rest;
  return body.includes("operatorOnly: true");
}
