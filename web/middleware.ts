import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse } from "next/server";
import {
  getRequestId,
  jsonApiError,
  setNoStoreResponseHeaders,
} from "@/lib/apiContracts";

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);

function isLocalHost(url: URL): boolean {
  return LOCAL_HOSTS.has(url.hostname);
}

// Explicit test-flag override (used by Playwright via RADON_AUTHLESS_TEST=1).
// Kept for parity with FastAPI's own bypass and to allow CI-driven authless runs.
export function isLocalAuthlessTestBypassEnabled(url: URL, flag = process.env.RADON_AUTHLESS_TEST): boolean {
  if (flag !== "1") return false;
  return isLocalHost(url);
}

// Local-dev auto-bypass: any time `next dev` runs against localhost we skip
// Clerk so the developer never sees the sign-in wall. Production builds set
// NODE_ENV=production so this is a no-op there even if someone reverse-proxies
// localhost. The FastAPI side already auto-skips for 127.0.0.1/::1 callers
// (see scripts/api/auth.py).
export function isLocalDevAuthBypassEnabled(
  url: URL,
  nodeEnv = process.env.NODE_ENV,
): boolean {
  if (nodeEnv === "production") return false;
  return isLocalHost(url);
}

// Share-card link previews — link-preview bots (Twitter, Slack, iMessage)
// have no Clerk session and can't sign in. EXPLICIT list, not a pattern:
// the old `/^\/api(?:\/[^/]+)*\/share(?:\/.*)?$/` regex silently published
// any future `/api/**/share*` path the moment its route file shipped. A new
// share route must be added here deliberately (and to the filesystem pin in
// web/tests/middleware-share-allowlist.test.ts, which fails until it is).
export const PUBLIC_SHARE_API_ROUTES = [
  "/api/gex/share",
  "/api/gex/share/content",
  "/api/internals/share",
  "/api/internals/share/content",
  "/api/menthorq/cta/share",
  "/api/menthorq/cta/share/content",
  "/api/regime/share",
  "/api/regime/share/content",
  "/api/share/pnl",
  "/api/vcg/share",
  "/api/vcg/share/content",
] as const;

// Public allowlist. Every other route — pages AND /api/* — requires a Clerk
// session. The narrow exemptions:
//
//   /sign-in, /sign-up                  — Clerk auth flow pages
//   PUBLIC_SHARE_API_ROUTES             — share-card link previews (above)
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
  ...PUBLIC_SHARE_API_ROUTES,
  "/api/service-health",
  "/api/health",
]);

export function isApiPath(pathname: string): boolean {
  return pathname === "/api" || pathname.startsWith("/api/");
}

export default clerkMiddleware(async (auth, request) => {
  if (
    isLocalDevAuthBypassEnabled(request.nextUrl) ||
    isLocalAuthlessTestBypassEnabled(request.nextUrl)
  ) {
    return NextResponse.next();
  }

  if (isPublicRoute(request)) return;

  // API routes: return a JSON 401 with the same shape as every other API
  // error response (see web/lib/apiContracts.ts). Clerk's default for a
  // protected route is to redirect to /sign-in, which is meaningless for an
  // API client and would also surface as a noisy 302 in the browser console
  // when the cookie expires mid-session.
  if (isApiPath(request.nextUrl.pathname)) {
    const { userId } = await auth();
    if (!userId) {
      const requestId = getRequestId();
      const response = jsonApiError({
        message: "Unauthorized",
        status: 401,
        code: "UNAUTHORIZED",
        requestId,
      });
      return setNoStoreResponseHeaders(response, requestId);
    }
    return;
  }

  // Page routes: keep Clerk's standard redirect-to-sign-in behavior.
  await auth.protect();
});

export const config = {
  matcher: [
    // Skip Next.js internals and all static files, unless found in search params
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    // Always run for API routes
    "/(api|trpc)(.*)",
  ],
};
