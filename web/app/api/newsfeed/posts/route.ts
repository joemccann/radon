import { requireRouteAccess } from "@/lib/routeAccess";
import { NextResponse } from "next/server";
import { parseImageSources, parseResearchSource } from "@/lib/newsfeedSource";
import { cachedRead } from "@/lib/dbCache";
import { dbExecute } from "@/lib/dbExecute";
import { FEEDBACK_REASONS, type FeedbackReason, type PostFeedback } from "@/lib/researchFeedback";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// Heaviest single read in the app (500 wide rows). The scraper writes every
// ~120s, so a 30s TTL loses nothing while collapsing every dashboard tab's
// poll into one Turso read per window. staleWhileError keeps the feed up
// through a brief Turso blip (contract: tests/db-read-cache-contract.test.ts).
const POSTS_CACHE_TTL_MS = 30_000;

type PostRow = {
  provenance_json?: string | null;
  id: string;
  title: string;
  content: string | null;
  timestamp: string;
  images: string | null;
  raw_images: string | null;
  image_sources?: string | null;
  tags: string | null;
  tags_text: string | null;
  tags_vision: string | null;
  created_at: string;
  updated_at: string;
};

function parseStringArray(json: string | null): string[] {
  if (!json) return [];
  try {
    const parsed = JSON.parse(json);
    return Array.isArray(parsed) ? parsed.filter((t) => typeof t === "string") : [];
  } catch {
    return [];
  }
}

function rowToPost(row: PostRow) {
  const images = parseStringArray(row.images);
  return {
    id: row.id,
    ...(parseResearchSource(row.provenance_json) ? { source: parseResearchSource(row.provenance_json) } : {}),
    title: row.title,
    content: row.content ?? "",
    timestamp: row.timestamp,
    images,
    imageSources: parseImageSources(row.image_sources, images),
    rawImages: parseStringArray(row.raw_images),
    tags: parseStringArray(row.tags),
    tags_text: parseStringArray(row.tags_text),
    tags_vision: parseStringArray(row.tags_vision),
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

/** Demo and production databases apply additive migrations independently. */
function isMissingResearchSourceTable(error: unknown): boolean {
  return error instanceof Error
    && (error as Error & { code?: string }).code === "SQLITE_ERROR"
    && /^(?:SQLITE_ERROR:\s*)?(?:SQLite error:\s*)?no such table:\s*(?:main\.)?research_post_sources$/i.test(error.message);
}

async function fetchPosts() {
  // 500 wide rows — give the bounded chokepoint more headroom than the
  // default 3s single-row path without removing the hang ceiling.
  const options = { timeoutMs: 8_000, label: "newsfeed-posts" };
  // Select the available post columns during independent additive migrations.
  // rowToPost is the API field allowlist; absent image_sources becomes {}.
  let result;
  try {
    result = await dbExecute({
      sql: `SELECT p.*, r.provenance_json
            FROM posts p LEFT JOIN research_post_sources r ON r.post_id = p.id
            ORDER BY p.timestamp DESC
            LIMIT 500`,
      args: [],
    }, options);
  } catch (error) {
    // Only the absent additive table is compatible with a legacy read.
    // Network, authorization, and all other SQL failures retain normal errors
    // (or cachedRead's last-good result), never silently changing the feed.
    if (!isMissingResearchSourceTable(error)) throw error;
    result = await dbExecute({
      sql: `SELECT posts.*, NULL AS provenance_json
            FROM posts
            WHERE id NOT GLOB 'research-*'
            ORDER BY timestamp DESC
            LIMIT 500`,
      args: [],
    }, options);
  }
  const posts: (ReturnType<typeof rowToPost> & { feedback?: PostFeedback })[] =
    result.rows.map((r) => rowToPost(r as unknown as PostRow))
      .filter(post => !post.id.startsWith("research-") || post.source);
  // Feedback only exists for research posts; legacy and demo databases never pay for the overlay.
  if (!posts.some(post => post.source)) return posts;
  const feedback = await latestFeedback();
  return posts
    // The operator's latest thumbs-down retracts a research post; the row itself is untouched.
    .filter(post => feedback.get(post.id)?.vote !== "down")
    .map(post => feedback.has(post.id) ? { ...post, feedback: feedback.get(post.id) } : post);
}

let feedbackTableSeen = false;

/** Latest operator vote per research post. A missing table (unmigrated database) is an empty map, never a feed outage. */
async function latestFeedback(): Promise<Map<string, PostFeedback>> {
  const votes = new Map<string, PostFeedback>();
  try {
    if (!feedbackTableSeen) {
      // Probe without raising: a SQL error resets the pooled client, which a missing additive table must not do.
      const probe = await dbExecute({
        sql: "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'research_feedback'", args: [],
      }, { timeoutMs: 3_000, label: "newsfeed-feedback-probe" });
      if (!probe.rows.length) return votes;
      feedbackTableSeen = true;
    }
    const result = await dbExecute({
      sql: `SELECT f.post_id, f.vote, f.reasons, f.comment FROM research_feedback f
            WHERE f.target = 'post' AND f.rowid = (
              SELECT g.rowid FROM research_feedback g WHERE g.post_id = f.post_id AND g.target = 'post'
              ORDER BY g.created_at DESC, g.rowid DESC LIMIT 1)`,
      args: [],
    }, { timeoutMs: 3_000, label: "newsfeed-feedback" });
    for (const row of result.rows) {
      if (row.vote !== "up" && row.vote !== "down") continue;
      let reasons: FeedbackReason[] = [];
      try {
        const parsed = JSON.parse(String(row.reasons ?? "[]"));
        if (Array.isArray(parsed)) reasons = parsed.filter((r): r is FeedbackReason => typeof r === "string" && r in FEEDBACK_REASONS);
      } catch { /* keep the vote, drop unreadable reasons */ }
      votes.set(String(row.post_id), { vote: row.vote, reasons, comment: String(row.comment ?? "") });
    }
  } catch { /* feedback is an overlay; the feed must not depend on it */ }
  return votes;
}

export const radonCapability = "read";

export async function GET() {
  const access = await requireRouteAccess();
  if (!access.ok) return access.response;
  try {
    const posts = await cachedRead("newsfeed:posts", POSTS_CACHE_TTL_MS, fetchPosts, {
      staleWhileError: true,
    });
    // The feedback overlay carries operator research-triage votes and
    // free-text comments; non-operator principals get neither research posts
    // nor any post's feedback.
    const visible = access.principal.kind === "operator" || access.principal.kind === "test"
      ? posts
      : posts
          .filter(post => !post.id.startsWith("research-") && !post.source)
          .map(post => (post.feedback ? { ...post, feedback: undefined } : post));
    return NextResponse.json(visible, {
      headers: { "cache-control": "private, no-store", "vary": "Cookie, Authorization" },
    });
  } catch {
    return NextResponse.json(
      { error: "Newsfeed temporarily unavailable" },
      { status: 503, headers: { "cache-control": "private, no-store", "vary": "Cookie, Authorization" } },
    );
  }
}
