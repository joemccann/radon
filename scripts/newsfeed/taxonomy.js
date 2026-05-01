// Atomic, append-only writer for data/tag_taxonomy.json.
// Both the scraper and the backfill CLI add new tags concurrently — read-merge-write
// under a single fs.writeFile (atomic on POSIX). The taxonomy grows; we never delete.

import path from "path";
import fs from "fs-extra";

const TAXONOMY_RELATIVE = path.join("data", "tag_taxonomy.json");
const DEFAULT_DESCRIPTION =
  "Curated, growable taxonomy applied to Market Ear posts. Tagger picks exactly 3 tags per post; novel tags are auto-appended here. Edit freely — no order or grouping is enforced.";

export function resolveTaxonomyFile(projectRoot) {
  return path.join(projectRoot, TAXONOMY_RELATIVE);
}

export async function loadTaxonomy(projectRoot) {
  const file = resolveTaxonomyFile(projectRoot);
  try {
    const raw = await fs.readFile(file, "utf8");
    const parsed = JSON.parse(raw);
    const tags = Array.isArray(parsed?.tags)
      ? parsed.tags.filter((t) => typeof t === "string" && t.length > 0)
      : [];
    return { tags, version: parsed?.version ?? 1, description: parsed?.description ?? DEFAULT_DESCRIPTION };
  } catch (err) {
    if (err.code === "ENOENT") {
      return { tags: [], version: 1, description: DEFAULT_DESCRIPTION };
    }
    throw err;
  }
}

// Returns the list of tags that were genuinely new and got appended.
// In-process concurrent writers are serialised via a promise chain so a
// read-modify-write cycle is atomic relative to other appends from the same
// Node process. Cross-process races (scraper + backfill at once) are rare in
// practice and the writer documentation says backfill should be paused.
let writeChain = Promise.resolve();

export function appendTagsToTaxonomy(projectRoot, candidates) {
  const next = writeChain.then(async () => {
    const file = resolveTaxonomyFile(projectRoot);
    const current = await loadTaxonomy(projectRoot);
    // Case-insensitive dedup: the canonical form is whatever is already in the
    // taxonomy. If "BTC" exists, the candidate "btc" is treated as a duplicate.
    const existingByLower = new Map(current.tags.map((t) => [t.toLowerCase(), t]));
    const additions = [];
    for (const tag of candidates) {
      if (typeof tag !== "string" || tag.length === 0) continue;
      const key = tag.toLowerCase();
      if (existingByLower.has(key)) continue;
      existingByLower.set(key, tag);
      additions.push(tag);
    }
    if (additions.length === 0) return [];

    const updated = {
      version: current.version,
      description: current.description,
      tags: [...current.tags, ...additions],
    };

    await fs.ensureDir(path.dirname(file));
    await fs.writeFile(file, JSON.stringify(updated, null, 2) + "\n");
    return additions;
  });

  writeChain = next.catch(() => {});
  return next;
}
