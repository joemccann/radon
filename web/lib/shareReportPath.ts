import { open } from "fs/promises";
import { constants } from "fs";
import { resolve, dirname, basename } from "path";

// Share cards live in data/share_cards (scripts/utils/share_cards.py): data/ is the
// bind mount the API and Next.js containers share, and the only checkout path the
// app image lets them write. Next.js route handlers run with cwd = web/.
const PROJECT_ROOT = resolve(process.cwd(), "..");
export const SHARE_CARDS_DIR = resolve(PROJECT_ROOT, "data", "share_cards");

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
// route is scoped to its OWN card type, the file must live DIRECTLY in SHARE_CARDS_DIR
// (no traversal, no subdirectories, no sibling-dir prefix tricks), and the
// basename must match the card pattern exactly.
function shareCardPattern(type: ShareCardType): RegExp {
  return new RegExp(`^tweet-${type}-\\d{4}-\\d{2}-\\d{2}(?:-card-\\d+)?\\.html$`);
}

export function isAllowedShareCardPath(rawPath: string | null | undefined, type: ShareCardType): boolean {
  if (!rawPath) return false;
  const resolved = resolve(rawPath);
  // Strict containment: the file must sit immediately inside SHARE_CARDS_DIR. Comparing
  // the parent directory exactly avoids the startsWith() trailing-separator class
  // of bypass and blocks any nested path.
  if (dirname(resolved) !== SHARE_CARDS_DIR) return false;
  return shareCardPattern(type).test(basename(resolved));
}

/**
 * Reads a card as a REGULAR FILE only. SHARE_CARDS_DIR sits in the shared read-write
 * data/ mount, so a name that passes the lexical allowlist may still be a link some
 * other writer planted. The final component is opened with O_NOFOLLOW and the open
 * descriptor must be a regular file; anything else is treated as absent.
 */
export async function readRegularShareCardFile(path: string): Promise<string | null> {
  let handle;
  try {
    handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  } catch {
    return null;
  }
  try {
    const stat = await handle.stat();
    if (!stat.isFile()) return null;
    return await handle.readFile("utf-8");
  } catch {
    return null;
  } finally {
    await handle.close().catch(() => {});
  }
}

/** Returns the share-card HTML, or null if the path is not an allowed card or is missing. */
export async function readShareCard(
  rawPath: string | null | undefined,
  type: ShareCardType,
): Promise<string | null> {
  if (!isAllowedShareCardPath(rawPath, type)) return null;
  return readRegularShareCardFile(resolve(rawPath as string));
}
