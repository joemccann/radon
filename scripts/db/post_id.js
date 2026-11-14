// Namespace guard for `posts.id`.
//
// Scraped newsfeed posts take their id from the upstream page markup, while
// scripts/research/publish.py fills the same table under the reserved
// `research-` namespace. upsertPosts writes ON CONFLICT(id) DO UPDATE, so a
// scraped id is only allowed to address a row the scraper itself owns.

export const RESERVED_POST_ID_PREFIXES = Object.freeze(["research-"]);

export function isReservedPostId(id) {
  if (typeof id !== "string") return false;
  const lower = id.toLowerCase();
  return RESERVED_POST_ID_PREFIXES.some((prefix) => lower.startsWith(prefix));
}

// A post the scraper may write: a non-empty string id outside every reserved
// namespace.
export function isWritablePostId(id) {
  return typeof id === "string" && id.length > 0 && !isReservedPostId(id);
}
