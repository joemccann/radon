#!/usr/bin/env node
// Phase 1 one-shot: copy the existing posts.json + tag_taxonomy.json files
// into the Turso DB so the dashboard's new DB-backed read path serves
// historical content immediately. Idempotent (ON CONFLICT DO UPDATE) —
// safe to run repeatedly, only the first run does real work.

import path from "node:path";
import { fileURLToPath } from "node:url";
import fs from "fs-extra";
import dotenv from "dotenv";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PROJECT_ROOT = path.resolve(__dirname, "..", "..");
dotenv.config({ path: path.join(PROJECT_ROOT, ".env") });
dotenv.config({ path: path.join(PROJECT_ROOT, "web/.env") });

const POSTS_FILE = path.join(PROJECT_ROOT, "web/public/data/posts.json");
const TAXONOMY_FILE = path.join(PROJECT_ROOT, "data/tag_taxonomy.json");

async function main() {
  const writer = await import(path.join(PROJECT_ROOT, "scripts/db/writer.js"));

  const posts = await fs.readJson(POSTS_FILE).catch(() => []);
  const taxonomy = await fs.readJson(TAXONOMY_FILE).catch(() => ({ tags: [] }));

  console.log(`[bootstrap] posts.json: ${posts.length} posts`);
  console.log(`[bootstrap] taxonomy: ${taxonomy.tags?.length ?? 0} tags`);

  const t0 = Date.now();
  await writer.upsertPosts(posts);
  console.log(`[bootstrap] posts upserted in ${Date.now() - t0}ms`);

  const t1 = Date.now();
  const added = await writer.appendTaxonomy(taxonomy.tags ?? []);
  console.log(`[bootstrap] taxonomy +${added} (existing rows preserved) in ${Date.now() - t1}ms`);
}

main().catch((err) => {
  console.error(`[bootstrap] fatal: ${err.message}`);
  process.exit(1);
});
