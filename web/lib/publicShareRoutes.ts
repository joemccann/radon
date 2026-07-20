// Share-card link previews — link-preview bots (Twitter, Slack, iMessage)
// have no Clerk session and can't sign in. EXPLICIT list, not a pattern:
// the old `/^\/api(?:\/[^/]+)*\/share(?:\/.*)?$/` regex silently published
// any future `/api/**/share*` path the moment its route file shipped. A new
// share route must be added here deliberately (and to the filesystem pin in
// web/tests/middleware-share-allowlist.test.ts, which fails until it is).
//
// Consumed by BOTH the middleware auth perimeter (public allowlist) and
// app/robots.ts (crawl Allow carve-outs — preview bots honor robots.txt, so
// the app's Disallow: / must not cover these or shared cards stop unfurling).
// Edge-safe: pure const module, no imports.
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
