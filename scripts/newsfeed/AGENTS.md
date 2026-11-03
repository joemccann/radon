# Radon Newsfeed Scraper — Codex Instructions

Applies under `scripts/newsfeed/`. Mirrors `scripts/newsfeed/CLAUDE.md`.

## Runtime

- Scraper uses headless Playwright for themarketear.com. `cdp.js` is a compatibility shim.
- Renewal env: `THEMARKETEAR_EMAIL`, `MARKETDESK_FIREBASE_REFRESH_TOKEN` (existing same-account Firebase seed). `THEMARKETEAR_PASSWORD` is an optional legacy fallback.
- Session storage: `data/newsfeed-storage.json` (0600, cookies and localStorage); own rotated grant `data/newsfeed-firebase.json` (0600). Proactively renew the product cookie every 6h, require the configured identity and premium content.
- Force IPv4 for `themarketear.com` CDN; model networking belongs to the standalone gateway.
- Local one-shot debug: `node scripts/newsfeed/index.js --once`.
- Service is Hetzner-resident as `radon-newsfeed.service`.

## Media / Storage

- Cookie-gated images require Playwright cookies through redirects.
- On Hetzner, `RADON_MEDIA_REMOTE=/var/lib/radon/media/` (compatibility symlink `~/radon-cloud/media`).
- Rollover at 500 KB: archive old posts and keep about 20%.
- `mergePosts` preserves tags.
- Do not honor JSON-LD `schema.image` when article DOM has no `<img>`; it can produce generic placeholder misattribution.

## Tagging

- Text and image tagging use `scripts/model_gateway/client.js` over the private Unix socket. Neither consumer resolves model credentials, spawns inference processes or contacts providers.
- Ladder order: antigravity -> anthropic -> grok -> cursor -> codex -> nvidia -> cerebras (last). Soft-fail when none work.
- Optional local rung `slm-tagger` behind `RADON_SLM_TAGGER_MODE` (default `off`). Closed vocabulary: 3 uppercase kebab tags, taxonomy-validated, `abstain:*` fall-through. Distill/reviewer never see it.
- Exactly 3 tags per post.
- Normalize tags to uppercase, multi-word uppercase-kebab-case, allowed chars `A-Z 0-9 - &`, case-insensitive dedupe.
- `data/tag_taxonomy.json` is untracked and runtime-owned (Turso `tag_taxonomy` is canonical).
- Any keyed ladder provider is sufficient for text tagging.

## UI Contract

- Output shape is locked by `web/components/DashboardNewsFeed.tsx` (`MarketEarPost`).
- Filter chips use AND semantics when multiple tags are active.
- Deep-link tag filters via `/dashboard?tags=BTC,vol`.

Both taggers retain exactly three normalized tags. Rejected normalized results continue only with a service-issued traversal token; caller code never iterates providers or resets the service budget. The shared Node client bounds request/response bytes and total time and forwards cancellation.
