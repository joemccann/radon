// Shared libSQL writer for Node scrapers (newsfeed, future schedulers).
// Mirrors web/lib/db.ts but constructed for use from arbitrary Node scripts
// — the scraper runs outside Next.js and needs its own client instance.

import path from "node:path";
import { fileURLToPath } from "node:url";
import { createClient } from "@libsql/client";

const __filename = fileURLToPath(import.meta.url);
const PROJECT_ROOT = path.resolve(path.dirname(__filename), "..", "..");
const REPLICA_PATH = path.join(PROJECT_ROOT, "data", "replica.db");

let cached = null;

const MEDIA_ORIGIN = "https://media.radon.run";

// posts.json stores relative `/media/<file>.png` paths because the local dev
// dashboard serves them out of web/public/media/. The Turso row is shared
// with app.radon.run (Hetzner), which has no local web/public — so we
// promote them to absolute URLs that resolve identically on both peers.
function absolutizeMedia(images) {
  if (!Array.isArray(images)) return [];
  return images.map((src) => {
    if (typeof src !== "string") return src;
    if (src.startsWith("https://") || src.startsWith("http://")) return src;
    // Caddy roots media.radon.run at /home/radon/radon-cloud/media/ so we
    // strip the leading /media/ — the rest of the path is the bare filename.
    if (src.startsWith("/media/")) return `${MEDIA_ORIGIN}/${src.slice("/media/".length)}`;
    return src;
  });
}

export function getDb() {
  if (cached) return cached;

  const url = process.env.TURSO_DB_URL;
  const authToken = process.env.TURSO_AUTH_TOKEN;
  if (!url) {
    throw new Error(
      "scripts/db/writer.getDb: TURSO_DB_URL is unset. Load web/.env or root .env first.",
    );
  }

  const useReplica = !process.env.RADON_DB_NO_REPLICA;

  cached = createClient(
    useReplica
      ? { url: `file:${REPLICA_PATH}`, syncUrl: url, authToken, syncInterval: 60 }
      : { url, authToken },
  );

  return cached;
}

// Idempotent — same id reapplies the row. updated_at always advances so the
// dashboard can reorder by recency if needed.
export async function upsertPost(post) {
  const db = getDb();
  const now = new Date().toISOString();
  await db.execute({
    sql: `INSERT INTO posts (id, title, content, timestamp, images, raw_images, tags, tags_text, tags_vision, created_at, updated_at)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
          ON CONFLICT(id) DO UPDATE SET
            title       = excluded.title,
            content     = excluded.content,
            timestamp   = excluded.timestamp,
            images      = excluded.images,
            raw_images  = excluded.raw_images,
            tags        = excluded.tags,
            tags_text   = excluded.tags_text,
            tags_vision = excluded.tags_vision,
            updated_at  = excluded.updated_at`,
    args: [
      post.id,
      post.title,
      post.content ?? null,
      post.timestamp,
      JSON.stringify(absolutizeMedia(post.images ?? [])),
      JSON.stringify(post.rawImages ?? []),
      JSON.stringify(post.tags ?? []),
      JSON.stringify(post.tags_text ?? []),
      JSON.stringify(post.tags_vision ?? []),
      post.createdAt ?? now,
      post.updatedAt ?? now,
    ],
  });
}

export async function upsertPosts(posts) {
  if (!posts || posts.length === 0) return;
  const db = getDb();
  const now = new Date().toISOString();

  // Batch in chunks so a few hundred posts don't blow up the WAL frame.
  const CHUNK = 50;
  for (let i = 0; i < posts.length; i += CHUNK) {
    const slice = posts.slice(i, i + CHUNK);
    const stmts = slice.map((post) => ({
      sql: `INSERT INTO posts (id, title, content, timestamp, images, raw_images, tags, tags_text, tags_vision, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              title       = excluded.title,
              content     = excluded.content,
              timestamp   = excluded.timestamp,
              images      = excluded.images,
              raw_images  = excluded.raw_images,
              tags        = excluded.tags,
              tags_text   = excluded.tags_text,
              tags_vision = excluded.tags_vision,
              updated_at  = excluded.updated_at`,
      args: [
        post.id,
        post.title,
        post.content ?? null,
        post.timestamp,
        JSON.stringify(absolutizeMedia(post.images ?? [])),
        JSON.stringify(post.rawImages ?? []),
        JSON.stringify(post.tags ?? []),
        JSON.stringify(post.tags_text ?? []),
        JSON.stringify(post.tags_vision ?? []),
        post.createdAt ?? now,
        post.updatedAt ?? now,
      ],
    }));
    await db.batch(stmts, "write");
  }
}

// COLLATE NOCASE on the PK makes BTC/btc/Btc collide. INSERT OR IGNORE
// preserves the first canonical form seen.
export async function appendTaxonomy(tags) {
  if (!tags || tags.length === 0) return 0;
  const db = getDb();
  const now = new Date().toISOString();
  let added = 0;
  for (const tag of tags) {
    const result = await db.execute({
      sql: `INSERT OR IGNORE INTO tag_taxonomy (tag, created_at) VALUES (?, ?)`,
      args: [tag, now],
    });
    if (result.rowsAffected > 0) added += 1;
  }
  return added;
}

export async function recordServiceHealth(service, state, extra = {}) {
  const db = getDb();
  const now = new Date().toISOString();
  await db.execute({
    sql: `INSERT INTO service_health (service, state, last_attempt_started_at, last_attempt_finished_at, last_error, updated_at)
          VALUES (?, ?, ?, ?, ?, ?)
          ON CONFLICT(service) DO UPDATE SET
            state                    = excluded.state,
            last_attempt_started_at  = COALESCE(excluded.last_attempt_started_at, service_health.last_attempt_started_at),
            last_attempt_finished_at = COALESCE(excluded.last_attempt_finished_at, service_health.last_attempt_finished_at),
            last_error               = excluded.last_error,
            updated_at               = excluded.updated_at`,
    args: [
      service,
      state,
      extra.startedAt ?? null,
      extra.finishedAt ?? null,
      extra.error ? JSON.stringify(extra.error) : null,
      now,
    ],
  });
}

export function __resetDbForTests() {
  cached = null;
}

// Test seam — inject a libSQL client (typically in-memory) so tests can
// exercise the upsert/append helpers without a real Turso connection.
export function __setDbForTests(client) {
  cached = client;
}
