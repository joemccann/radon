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

type FeedRow = PostRow & {
  provenance_json?: string | null;
  feedback_vote?: string | null;
  feedback_reasons?: string | null;
  feedback_comment?: string | null;
};

type FeedPost = ReturnType<typeof rowToPost> & { feedback?: PostFeedback };

const POSTS_WITH_FEEDBACK_SQL = `SELECT p.*, r.provenance_json,
       f.vote AS feedback_vote, f.reasons AS feedback_reasons, f.comment AS feedback_comment
FROM posts p
LEFT JOIN research_post_sources r ON r.post_id = p.id
LEFT JOIN (
  SELECT f.post_id, f.vote, f.reasons, f.comment
  FROM research_feedback f
  WHERE f.target = 'post' AND f.rowid = (
    SELECT g.rowid FROM research_feedback g
    WHERE g.post_id = f.post_id AND g.target = 'post'
    ORDER BY g.created_at DESC, g.rowid DESC LIMIT 1
  )
) f ON f.post_id = p.id
ORDER BY p.timestamp DESC
LIMIT 500`;

const POSTS_WITH_SOURCES_SQL = `SELECT p.*, r.provenance_json
FROM posts p LEFT JOIN research_post_sources r ON r.post_id = p.id
ORDER BY p.timestamp DESC
LIMIT 500`;

const POSTS_LEGACY_SQL = `SELECT posts.*, NULL AS provenance_json
FROM posts
WHERE id NOT GLOB 'research-*'
ORDER BY timestamp DESC
LIMIT 500`;

/** Demo and production databases apply additive migrations independently. */
function isMissingTable(error: unknown, table: string): boolean {
  return error instanceof Error
    && (error as Error & { code?: string }).code === "SQLITE_ERROR"
    && new RegExp(
      `^(?:SQLITE_ERROR:\\s*)?(?:SQLite error:\\s*)?no such table:\\s*(?:main\\.)?${table}$`,
      "i",
    ).test(error.message);
}

function isMissingResearchSourceTable(error: unknown): boolean {
  return isMissingTable(error, "research_post_sources");
}

function isMissingResearchFeedbackTable(error: unknown): boolean {
  return isMissingTable(error, "research_feedback");
}

function parseFeedback(row: FeedRow): PostFeedback | undefined {
  if (row.feedback_vote !== "up" && row.feedback_vote !== "down") return undefined;
  let reasons: FeedbackReason[] = [];
  try {
    const parsed = JSON.parse(String(row.feedback_reasons ?? "[]"));
    if (Array.isArray(parsed)) {
      reasons = parsed.filter((r): r is FeedbackReason => typeof r === "string" && r in FEEDBACK_REASONS);
    }
  } catch { /* keep the vote, drop unreadable reasons */ }
  return { vote: row.feedback_vote, reasons, comment: String(row.feedback_comment ?? "") };
}

function postsFromResult(rows: unknown[], { withFeedback }: { withFeedback: boolean }): FeedPost[] {
  const posts: FeedPost[] = rows.map((raw) => {
    const row = raw as FeedRow;
    const post = rowToPost(row);
    const feedback = withFeedback ? parseFeedback(row) : undefined;
    return feedback ? { ...post, feedback } : post;
  }).filter(post => !post.id.startsWith("research-") || post.source);
  if (!posts.some(post => post.source)) {
    return posts.map(({ feedback: _feedback, ...rest }) => rest);
  }
  return posts.filter(post => post.feedback?.vote !== "down");
}

async function fetchPosts() {
  // 500 wide rows — give the bounded chokepoint more headroom than the
  // default 3s single-row path without removing the hang ceiling.
  const options = { timeoutMs: 8_000, label: "newsfeed-posts" };
  // One Turso execute when both additive tables exist (PERF-006). Absent
  // tables fall back one hop; network/auth/SQL errors still fail the feed.
  try {
    const result = await dbExecute({ sql: POSTS_WITH_FEEDBACK_SQL, args: [] }, options);
    return postsFromResult(result.rows, { withFeedback: true });
  } catch (error) {
    if (isMissingResearchFeedbackTable(error)) {
      try {
        const result = await dbExecute({ sql: POSTS_WITH_SOURCES_SQL, args: [] }, options);
        return postsFromResult(result.rows, { withFeedback: false });
      } catch (inner) {
        if (!isMissingResearchSourceTable(inner)) throw inner;
        const result = await dbExecute({ sql: POSTS_LEGACY_SQL, args: [] }, options);
        return postsFromResult(result.rows, { withFeedback: false });
      }
    }
    if (!isMissingResearchSourceTable(error)) throw error;
    const result = await dbExecute({ sql: POSTS_LEGACY_SQL, args: [] }, options);
    return postsFromResult(result.rows, { withFeedback: false });
  }
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
