// Share-card link previews — link-preview bots (Twitter, Slack, iMessage)
// have no Clerk session and can't sign in. EXPLICIT list, not a pattern:
// the old `/^\/api(?:\/[^/]+)*\/share(?:\/.*)?$/` regex silently published
// any future `/api/**/share*` path the moment its route file shipped. A new
// share route must be added here deliberately (and to the filesystem pin in
// web/tests/middleware-share-allowlist.test.ts, which fails until it is).
//
// SPLIT BY METHOD: a preview bot only ever GETs a rendered card. The generator
// POSTs below (AUTHENTICATED_SHARE_GENERATOR_ROUTES) do the opposite — they run
// generate_*_share.py on the trading host through a loopback call FastAPI
// trusts as server-to-server — so publishing them handed an anonymous internet
// caller a 120s script execution behind nothing but an in-memory per-worker
// rate limit. Only the read-only routes below are public.
//
// Consumed by BOTH the middleware auth perimeter (public allowlist) and
// app/robots.ts (crawl Allow carve-outs — preview bots honor robots.txt, so
// the app's Disallow: / must not cover these or shared cards stop unfurling).
// Edge-safe: pure module, no imports.
export const PUBLIC_SHARE_API_ROUTES = [
  "/api/gex/share/content",
  "/api/internals/share/content",
  "/api/menthorq/cta/share/content",
  "/api/regime/share/content",
  "/api/share/pnl",
  "/api/vcg/share/content",
] as const;

// The card GENERATORS. Reachable only with a Clerk session — they are operator
// actions, not link previews, and no bot ever calls them. Enumerated (rather
// than merely absent) so the filesystem pin can prove every share route on disk
// was classified as either public-preview or authenticated-generator.
export const AUTHENTICATED_SHARE_GENERATOR_ROUTES = [
  "/api/gex/share",
  "/api/internals/share",
  "/api/menthorq/cta/share",
  "/api/regime/share",
  "/api/vcg/share",
] as const;

// Repo-relative pointer the */share/content GET resolves against the same cwd
// (Next.js route handlers run with cwd = web/, see lib/shareReportPath.ts).
const REPO_RELATIVE_REPORTS_PREFIX = "../reports/";

function toRepoRelativeReportPath(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  const name = value.split("/").pop();
  return name ? `${REPO_RELATIVE_REPORTS_PREFIX}${name}` : null;
}

/**
 * Rewrites a share-generator payload so it carries no absolute host path.
 *
 * The generators return `/home/<user>/<deploy>/reports/...`, which handed a
 * caller the exact base path lib/shareReportPath.ts hardened the content routes
 * against guessing. The preview fetch only needs a pointer the content route
 * can resolve, and nothing consumes the card/png path arrays, so they are
 * dropped outright.
 */
export function withoutAbsoluteReportPaths(payload: unknown): unknown {
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
    return payload;
  }
  const {
    preview_path: previewPath,
    card_paths: _cardPaths,
    png_paths: _pngPaths,
    ...rest
  } = payload as Record<string, unknown>;
  const relative = toRepoRelativeReportPath(previewPath);
  return relative ? { ...rest, preview_path: relative } : rest;
}
