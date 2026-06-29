import { readFile } from "fs/promises";
import { resolve, dirname, basename } from "path";

// reports/ lives at the repo root; Next.js route handlers run with cwd = web/.
const PROJECT_ROOT = resolve(process.cwd(), "..");
export const REPORTS_DIR = resolve(PROJECT_ROOT, "reports");

export type ShareCardType = "gex" | "vcg" | "regime" | "internals" | "cta";

// SECURITY: the */share/content routes are UNAUTHENTICATED (PUBLIC_SHARE_API_ROUTES
// in web/middleware.ts) so link-preview bots can render share cards. The original
// implementation served ANY file under reports/ via a caller-supplied ?path=,
// exposing operator-private reports (portfolio-*.html with real account figures,
// *-evaluation-*.html, daily-scan-*.html, garch-convergence-*.html). The public
// POST /api/*/share even leaked the absolute reports/ base path, removing all
// guesswork.
//
// The only files these routes may serve are the public share cards the generators
// (scripts/generate_*_share.py) emit: tweet-<type>-YYYY-MM-DD[-card-N].html. Each
// route is scoped to its OWN card type, the file must live DIRECTLY in reports/
// (no traversal, no subdirectories, no sibling-dir prefix tricks), and the
// basename must match the card pattern exactly.
function shareCardPattern(type: ShareCardType): RegExp {
  return new RegExp(`^tweet-${type}-\\d{4}-\\d{2}-\\d{2}(?:-card-\\d+)?\\.html$`);
}

export function isAllowedShareCardPath(rawPath: string | null | undefined, type: ShareCardType): boolean {
  if (!rawPath) return false;
  const resolved = resolve(rawPath);
  // Strict containment: the file must sit immediately inside reports/. Comparing
  // the parent directory exactly avoids the startsWith() trailing-separator class
  // of bypass and blocks any nested path.
  if (dirname(resolved) !== REPORTS_DIR) return false;
  return shareCardPattern(type).test(basename(resolved));
}

/** Returns the share-card HTML, or null if the path is not an allowed card or is missing. */
export async function readShareCard(
  rawPath: string | null | undefined,
  type: ShareCardType,
): Promise<string | null> {
  if (!isAllowedShareCardPath(rawPath, type)) return null;
  try {
    return await readFile(resolve(rawPath as string), "utf-8");
  } catch {
    return null;
  }
}
