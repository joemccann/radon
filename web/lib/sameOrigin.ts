/**
 * Same-origin gate for mutating Next.js route handlers.
 *
 * CSRF defense in depth on top of the Clerk session cookie. A browser attaches
 * `Sec-Fetch-Site` to every fetch; `same-origin` is the only value that proves
 * the document was served by this app. `none` is a user-typed navigation or a
 * bookmark, which is not how this app issues POST/PUT/PATCH/DELETE, so it is
 * rejected. FastAPI allows `none` only for trusted-local docs reads.
 *
 * When `Sec-Fetch-Site` is absent (non-browser HTTP clients), `Origin` must
 * match an origin derived from the first-party list plus the existing host
 * env. Callers that cannot send either header are exempt only by the explicit
 * list in `decideSameOriginMutation`.
 *
 * Edge-safe: Web APIs only. Imported by web/middleware.ts.
 */
import { NextResponse } from "next/server";
import { getRequestId, jsonApiError, setNoStoreResponseHeaders } from "@/lib/apiContracts";
import { isAuthorizedProbeRequest } from "@/lib/probeAuth";
import { SETUP_API_PREFIX } from "@/lib/setup/setupMode";

export const CROSS_SITE_MUTATION_MESSAGE =
  "Cross-site mutation blocked. Mutating requests must come from this app.";

/** Mirrors middleware PROBE_BEARER_API_ROUTES. Pinned by same-origin-mutation.test.ts. */
export const PROBE_BEARER_MUTATION_EXEMPT_PATHS = [
  "/api/probe/freshness",
  "/api/service-health",
] as const;

const MUTATING_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "::1"]);  // pragma: allowlist secret
const ASSISTANT_DISPATCH_HOST = "assistant.local";
const CLERK_WEBHOOK_PATH = "/api/webhooks/clerk";
const TRADINGVIEW_WEBHOOK_PREFIX = "/api/webhooks/tradingview/";

const FIRST_PARTY_ORIGINS = [
  "https://app.radon.run",
  "https://demo.radon.run",
  "http://localhost:3000",
  "http://127.0.0.1:3000",  // pragma: allowlist secret
  "http://[::1]:3000",
];

export type SameOriginEnv = {
  [key: string]: string | undefined;
  RADON_CORS_EXTRA_ORIGINS?: string;
  RADON_ALLOWED_HOSTS?: string;
  RADON_APP_ORIGIN?: string;
  DOMAIN?: string;
  VERCEL_URL?: string;
  RADON_PROBE_FRESHNESS_TOKEN?: string;
  RADON_AUTHLESS_TEST?: string;
  RADON_AUTHLESS_TEST_TOKEN?: string;
};

export type SameOriginExemption =
  | "webhook-clerk"
  | "webhook-tradingview"
  | "setup-token"
  | "probe-bearer"
  | "assistant-dispatch"
  | "loopback-direct"
  | "authless-test";

export type SameOriginDecision =
  | { ok: true; via: "read-only" | "fetch-metadata" | "origin" }
  | { ok: true; via: "exempt"; exemption: SameOriginExemption }
  | { ok: false; status: 403; message: string };

function reject(): SameOriginDecision {
  return { ok: false, status: 403, message: CROSS_SITE_MUTATION_MESSAGE };
}

function normalizeOrigin(value: string): string | null {
  try {
    const url = new URL(value);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    if (url.username || url.password) return null;
    if (url.pathname !== "/" || url.search || url.hash) return null;
    return url.origin.toLowerCase();
  } catch {
    return null;
  }
}

function bareHost(value: string): string {
  const first = value.split(",")[0]?.trim() ?? "";
  if (!first) return "";
  if (first.startsWith("[")) {
    const end = first.indexOf("]");
    return (end > 0 ? first.slice(1, end) : first).toLowerCase();
  }
  const host = first.startsWith("http://") || first.startsWith("https://")
    ? (normalizeOrigin(first) ? new URL(first).hostname : first)
    : first;
  return host.split(":")[0]?.toLowerCase() ?? "";
}

function isLoopbackHost(host: string): boolean {
  const normalized = host.toLowerCase().replace(/^\[|\]$/g, "").replace(/^::ffff:/, "");
  return LOOPBACK_HOSTS.has(normalized);
}

function addConfiguredOrigin(origins: Set<string>, raw: string | undefined): void {
  const value = (raw ?? "").trim();
  if (!value || value === "*") return;
  if (value.includes("://")) {
    const origin = normalizeOrigin(value);
    if (origin) origins.add(origin);
    return;
  }
  const host = bareHost(value);
  if (!host) return;
  const scheme = isLoopbackHost(host) ? "http" : "https";
  const origin = normalizeOrigin(`${scheme}://${value.trim()}`);
  if (origin) origins.add(origin);
}

export function allowedMutationOrigins(env: SameOriginEnv = process.env): string[] {
  const origins = new Set<string>(FIRST_PARTY_ORIGINS);
  for (const piece of (env.RADON_CORS_EXTRA_ORIGINS ?? "").split(",")) {
    addConfiguredOrigin(origins, piece);
  }
  for (const piece of (env.RADON_ALLOWED_HOSTS ?? "").split(",")) {
    addConfiguredOrigin(origins, piece);
  }
  addConfiguredOrigin(origins, env.RADON_APP_ORIGIN);
  addConfiguredOrigin(origins, env.DOMAIN);
  addConfiguredOrigin(origins, env.VERCEL_URL);
  return [...origins].sort();
}

function pathnameOf(request: Request): string {
  try {
    return new URL(request.url).pathname;
  } catch {
    return "";
  }
}

function hostnameOf(request: Request): string {
  try {
    return new URL(request.url).hostname.toLowerCase().replace(/^\[|\]$/g, "");
  } catch {
    return "";
  }
}

function tradingViewWebhook(pathname: string): boolean {
  if (!pathname.startsWith(TRADINGVIEW_WEBHOOK_PREFIX)) return false;
  const token = pathname.slice(TRADINGVIEW_WEBHOOK_PREFIX.length);
  return token.length > 0 && !token.includes("/");
}

function arrivedViaPublicProxy(headers: Headers): boolean {
  const forwardedHost = headers.get("x-forwarded-host");
  if (forwardedHost && !isLoopbackHost(bareHost(forwardedHost))) return true;
  const forwardedFor = headers.get("x-forwarded-for");
  if (forwardedFor && !isLoopbackHost(bareHost(forwardedFor))) return true;
  const forwarded = headers.get("forwarded");
  if (forwarded) {
    const match = /for=(?:"\[)?([^;",\]]+)/i.exec(forwarded);
    if (match && !isLoopbackHost(match[1].replace(/^\[|\]$/g, ""))) return true;
  }
  return false;
}

function authlessTestExempt(request: Request, env: SameOriginEnv): boolean {
  if (env.RADON_AUTHLESS_TEST !== "1") return false;
  const expected = env.RADON_AUTHLESS_TEST_TOKEN;
  if (!expected) return false;
  return request.headers.get("x-radon-authless-test") === expected;
}

function isApiPath(pathname: string): boolean {
  return pathname === "/api" || pathname.startsWith("/api/");
}

export async function decideSameOriginMutation(
  request: Request,
  env: SameOriginEnv = process.env,
): Promise<SameOriginDecision> {
  const method = request.method.toUpperCase();
  if (!MUTATING_METHODS.has(method)) return { ok: true, via: "read-only" };

  const pathname = pathnameOf(request);
  if (pathname === CLERK_WEBHOOK_PATH) {
    return { ok: true, via: "exempt", exemption: "webhook-clerk" };
  }
  if (tradingViewWebhook(pathname)) {
    return { ok: true, via: "exempt", exemption: "webhook-tradingview" };
  }
  if (pathname.startsWith(SETUP_API_PREFIX)) {
    return { ok: true, via: "exempt", exemption: "setup-token" };
  }
  if ((PROBE_BEARER_MUTATION_EXEMPT_PATHS as readonly string[]).includes(pathname)) {
    const authorized = await isAuthorizedProbeRequest(
      request.headers.get("authorization"),
      env.RADON_PROBE_FRESHNESS_TOKEN,
    );
    if (authorized) return { ok: true, via: "exempt", exemption: "probe-bearer" };
  }

  const fetchSite = (request.headers.get("sec-fetch-site") ?? "").trim().toLowerCase();
  if (fetchSite) {
    if (fetchSite === "same-origin") return { ok: true, via: "fetch-metadata" };
    return reject();
  }

  const originHeader = (request.headers.get("origin") ?? "").trim();
  if (originHeader) {
    const origin = normalizeOrigin(originHeader);
    if (origin && allowedMutationOrigins(env).includes(origin)) {
      return { ok: true, via: "origin" };
    }
    return reject();
  }

  if (hostnameOf(request) === ASSISTANT_DISPATCH_HOST) {
    return { ok: true, via: "exempt", exemption: "assistant-dispatch" };
  }
  if (isLoopbackHost(hostnameOf(request)) && !arrivedViaPublicProxy(request.headers)) {
    return { ok: true, via: "exempt", exemption: "loopback-direct" };
  }
  if (authlessTestExempt(request, env)) {
    return { ok: true, via: "exempt", exemption: "authless-test" };
  }
  return reject();
}

export async function sameOriginMutationResponse(
  request: Request,
  env: SameOriginEnv = process.env,
): Promise<NextResponse | null> {
  if (!isApiPath(pathnameOf(request))) return null;
  const decision = await decideSameOriginMutation(request, env);
  if (decision.ok) return null;
  const requestId = getRequestId();
  const response = jsonApiError({
    message: decision.message,
    status: decision.status,
    code: "FORBIDDEN",
    requestId,
  });
  return setNoStoreResponseHeaders(response, requestId);
}
