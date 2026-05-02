import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

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

// API routes are public at the middleware level because server-side page
// fetches don't carry Clerk session cookies. External API access is still
// protected by FastAPI's Clerk JWT auth middleware.
const isPublicRoute = createRouteMatcher([
  "/sign-in(.*)",
  "/sign-up(.*)",
  "/api/(.*)",
]);

export default clerkMiddleware(async (auth, request) => {
  if (
    isLocalDevAuthBypassEnabled(request.nextUrl) ||
    isLocalAuthlessTestBypassEnabled(request.nextUrl)
  ) {
    return;
  }

  if (!isPublicRoute(request)) {
    await auth.protect();
  }
});

export const config = {
  matcher: [
    // Skip Next.js internals and all static files, unless found in search params
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    // Always run for API routes
    "/(api|trpc)(.*)",
  ],
};
