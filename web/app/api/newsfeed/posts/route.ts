import { NextResponse } from "next/server";
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

async function fetchPosts() {
  // 500 wide rows — give the bounded chokepoint more headroom than the
  // default 3s single-row path without removing the hang ceiling.
  const result = await dbExecute({
    sql: `SELECT id, title, content, timestamp, images, raw_images, tags, tags_text, tags_vision, created_at, updated_at
          FROM posts
          ORDER BY timestamp DESC
          LIMIT 500`,
    args: [],
  }, { timeoutMs: 8_000, label: "newsfeed-posts" });
  return result.rows.map((r) => rowToPost(r as unknown as PostRow));
}

export async function GET() {
  try {
    const posts = await cachedRead("newsfeed:posts", POSTS_CACHE_TTL_MS, fetchPosts, {
      staleWhileError: true,
    });
    return NextResponse.json(posts, {
      headers: { "cache-control": "no-store" },
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json(
      { error: `newsfeed read failed: ${message}` },
      { status: 503, headers: { "cache-control": "no-store" } },
    );
  }
}
