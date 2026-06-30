#!/usr/bin/env node
/**
 * Mirror recent public newsfeed `posts` from the PROD Turso into the DEMO Turso,
 * so demo.radon.run's "Live Market Analysis" panel shows real headlines.
 *
 * Isolation: this is a PROD-SIDE job — the only place that holds BOTH the prod
 * and demo Turso credentials. The demo deployment never holds prod creds; it
 * only reads its own `posts` table (getDb -> demo DB). Post images are public on
 * media.radon.run and are already absolute URLs in prod rows, so rows copy
 * across verbatim.
 *
 * Source: TURSO_DB_URL + TURSO_AUTH_TOKEN (prod).
 * Dest:   TURSO_DEMO_DB_URL + TURSO_DEMO_AUTH_TOKEN (demo).
 * Optional: MIRROR_POST_LIMIT (default 400).
 */
import { createClient } from "@libsql/client";

const PROD_MARKER = "radon-joemccann";
const DEMO_MARKER = "radon-demo";

// libsql:// / wss:// -> https:// so the client uses the stateless HTTP transport
// (no wedging WebSocket singleton). Mirrors web/lib/db.ts:toHttpTransportUrl.
function toHttp(url) {
  if (!url) return url;
  if (url.startsWith("libsql://")) return `https://${url.slice("libsql://".length)}`;
  if (url.startsWith("wss://")) return `https://${url.slice("wss://".length)}`;
  if (url.startsWith("ws://")) return `http://${url.slice("ws://".length)}`;
  return url;
}

const UPSERT_SQL = `INSERT INTO posts (id, title, content, timestamp, images, raw_images, tags, tags_text, tags_vision, created_at, updated_at)
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
    updated_at  = excluded.updated_at`;

async function main() {
  const srcUrl = process.env.TURSO_DB_URL;
  const srcToken = process.env.TURSO_AUTH_TOKEN;
  const dstUrl = process.env.TURSO_DEMO_DB_URL;
  const dstToken = process.env.TURSO_DEMO_AUTH_TOKEN;
  const limit = Number(process.env.MIRROR_POST_LIMIT || 400);

  if (!srcUrl || !srcToken) throw new Error("TURSO_DB_URL + TURSO_AUTH_TOKEN (prod source) required");
  if (!dstUrl || !dstToken) throw new Error("TURSO_DEMO_DB_URL + TURSO_DEMO_AUTH_TOKEN (demo dest) required");

  // Isolation guards: dest must be the demo DB, never prod, never the same DB.
  if (!dstUrl.includes(DEMO_MARKER)) {
    throw new Error(`dest ${dstUrl} is not the demo DB (missing '${DEMO_MARKER}') — refusing`);
  }
  if (dstUrl.replace(DEMO_MARKER, "").includes(PROD_MARKER) || dstUrl === srcUrl) {
    throw new Error("dest looks like prod / equals source — refusing to mirror");
  }

  const src = createClient({ url: toHttp(srcUrl), authToken: srcToken });
  const dst = createClient({ url: toHttp(dstUrl), authToken: dstToken });

  const { rows } = await src.execute({
    sql: `SELECT id, title, content, timestamp, images, raw_images, tags, tags_text, tags_vision, created_at, updated_at
          FROM posts ORDER BY timestamp DESC LIMIT ?`,
    args: [limit],
  });
  console.log(`[mirror] read ${rows.length} posts from prod`);

  const batch = rows.map((r) => ({
    sql: UPSERT_SQL,
    args: [
      r.id, r.title, r.content ?? null, r.timestamp,
      r.images ?? "[]", r.raw_images ?? "[]",
      r.tags ?? "[]", r.tags_text ?? "[]", r.tags_vision ?? "[]",
      r.created_at, r.updated_at,
    ],
  }));
  if (batch.length) await dst.batch(batch, "write");

  const { rows: after } = await dst.execute("SELECT count(*) AS n, max(timestamp) AS newest FROM posts");
  console.log(`[mirror] demo posts now: ${after[0].n} (newest ${after[0].newest})`);
}

main().catch((err) => {
  console.error("[mirror] FAILED:", err.message);
  process.exit(1);
});
