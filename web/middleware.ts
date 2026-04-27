import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

export function isLocalAuthlessTestBypassEnabled(url: URL, flag = process.env.RADON_AUTHLESS_TEST): boolean {
  if (flag !== "1") return false;
  return url.hostname === "localhost" || url.hostname === "127.0.0.1";
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
  if (isLocalAuthlessTestBypassEnabled(request.nextUrl)) {
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
