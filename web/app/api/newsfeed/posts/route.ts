import { requireRouteAccess } from "@/lib/routeAccess";
import { NextResponse } from "next/server";
import { parseResearchSource } from "@/lib/newsfeedSource";
import { cachedRead } from "@/lib/dbCache";
import { dbExecute } from "@/lib/dbExecute";

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
  return {
    id: row.id,
    ...(parseResearchSource(row.provenance_json) ? { source: parseResearchSource(row.provenance_json) } : {}),
    title: row.title,
    content: row.content ?? "",
    timestamp: row.timestamp,
    images: parseStringArray(row.images),
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
  let result;
  try {
    result = await dbExecute({
      sql: `SELECT p.id, p.title, p.content, p.timestamp, p.images, p.raw_images, p.tags, p.tags_text, p.tags_vision, p.created_at, p.updated_at, r.provenance_json
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
      sql: `SELECT id, title, content, timestamp, images, raw_images, tags, tags_text, tags_vision, created_at, updated_at, NULL AS provenance_json
            FROM posts
            WHERE id NOT GLOB 'research-*'
            ORDER BY timestamp DESC
            LIMIT 500`,
      args: [],
    }, options);
  }
  return result.rows.map((r) => rowToPost(r as unknown as PostRow))
    .filter(post => !post.id.startsWith("research-") || post.source);
}

export const radonCapability = "read";

export async function GET() {
  const access = await requireRouteAccess();
  if (!access.ok) return access.response;
  try {
    const posts = await cachedRead("newsfeed:posts", POSTS_CACHE_TTL_MS, fetchPosts, {
      staleWhileError: true,
    });
    const visible = access.principal.kind === "operator" || access.principal.kind === "test"
      ? posts : posts.filter(post => !post.id.startsWith("research-") && !post.source);
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
