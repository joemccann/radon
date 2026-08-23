# Radon Newsfeed Scraper — CLAUDE.md

Headless Playwright scraper for themarketear.com → dashboard newsfeed. Loaded when cwd is under `scripts/newsfeed/`.

Module split: `paths`, `browser`, `auth`, `cdp`, `extract`, `media`, `store`, `tagger`, `vision_tagger`, `taxonomy`, `scheduler`, `index`. Output shape locked by `web/components/DashboardNewsFeed.tsx` (`MarketEarPost`).

---

## Key Behaviors

- **Headless Playwright** replaces chrome-cdp. Env: `THEMARKETEAR_EMAIL`, `THEMARKETEAR_PASSWORD`. Session stored at `data/newsfeed-storage.json` (gitignored, ~30d), reuses across runs; full re-auth ~6h. `cdp.js` is a back-compat shim.
- **IPv4 forced** for `themarketear.com` CDN and `api.cerebras.ai` — both AAAA-unreachable from residential IPv6.
- **Cookie-gated images:** `media.js` accepts a `getCookieHeader` callback; Playwright `context.cookies()` follows `/images/<hash>.png` 301 → digitaloceanspaces.
- **Rollover** at 500 KB → archive + keep ⌈N × 0.2⌉. `mergePosts` preserves `tags`.

---

## Tagging Pipeline

Router: vision tagger for posts with images; text tagger otherwise.

- **Vision tagger:** `claude-haiku-4-5`, ~$0.003 / post.
- **Text tagger:** Cerebras `gpt-oss-120b` → fallback `qwen-3-235b-a22b-instruct-2507`. `gpt-oss-120b` needs `max_tokens: 800` (reasoning model — undersized budget truncates the output and produces no tags).
- Exactly **3 tags per post**, free-form.
- **Naming** (`__normaliseTags`): UPPERCASE, multi-word `UPPERCASE-KEBAB-CASE` (`PUT-CALL-RATIO`), allowed `A-Z 0-9 - &`, case-insensitive dedup.
- `hydrateTags` skips posts with `tags.length >= 3` unless `force=true`.
- `data/tag_taxonomy.json` force-tracked. Filter chips on the dashboard auto-derive from it.
- Either `CEREBRAS_API_KEY` or `ANTHROPIC_API_KEY` is sufficient.

---

## Image Attribution Bug (2026-05-22)

themarketear inserts `assets/images/generic.png` as JSON-LD `schema.image` for image-less posts. The scraper used to honour `schema.image` even when the article DOM had no `<img>`, downloading and caching the generic.png bytes (which happened to be the EMB candlestick chart). Every subsequent text-only post inherited that file.

Fixes:
- `scripts/newsfeed/extract.js` no longer reads `schema.image` unconditionally.
- `scripts/newsfeed/media.js:hydrateLocalImages` clears stale images on empty re-scrape.
- One-shot scrub at `scripts/newsfeed/scrub_generic_image_attributions.js`.

Commit b405267.

---

## Backfill + Re-tag

- `scripts/newsfeed/backfill_tags.js`. `--retag` re-tags all posts. Throttles to ~24 req/min.
- `concurrently` env quirk: `index.js` explicitly loads `web/.env` + root `.env` via `dotenv`.

---

## Env Overrides

`RADON_NEWSFEED_DATA_DIR`, `RADON_NEWSFEED_POSTS_FILE`, `RADON_NEWSFEED_ARCHIVE_DIR`, `RADON_NEWSFEED_MEDIA_DIR`, `RADON_NEWSFEED_PUBLIC_ROOT`, `CDP_CLI`.

---

## Filter UI Contract

Per-post chips with AND-semantics when ≥2 are active. Active filters render as a top bar with `×` per chip + "Clear all". Deep-link: `/dashboard?tags=BTC,vol`. URL writes happen in a post-commit `useEffect`.

---

## Tests

64 cases across `newsfeed-{scraper,tagger,taxonomy,time}`, `dashboard-newsfeed-{pagination,tag-filter}`.

---

## Hetzner Resident

Newsfeed runs on the VPS, not laptop. Session at `data/newsfeed-storage.json` (gitignored). On Hetzner `RADON_MEDIA_REMOTE=/var/lib/radon/media/` (compat symlink `~/radon-cloud/media`). Service: `radon-newsfeed.service` (`Restart=on-failure`).

For local one-shot debug: `node scripts/newsfeed/index.js --once`.
