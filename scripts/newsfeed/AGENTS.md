# Radon Newsfeed Scraper — Codex Instructions

Applies under `scripts/newsfeed/`. Mirrors `scripts/newsfeed/CLAUDE.md`.

## Runtime

- Scraper uses headless Playwright for themarketear.com. `cdp.js` is a compatibility shim.
- Required env: `THEMARKETEAR_EMAIL`, `THEMARKETEAR_PASSWORD`.
- Session storage: `data/newsfeed-storage.json` (gitignored), reused for about 30 days with full re-auth about every 6h.
- Force IPv4 for `themarketear.com` CDN and `api.cerebras.ai`.
- Local one-shot debug: `node scripts/newsfeed/index.js --once`.
- Service is Hetzner-resident as `radon-newsfeed.service`.

## Media / Storage

- Cookie-gated images require Playwright cookies through redirects.
- On Hetzner, `RADON_MEDIA_REMOTE=/var/lib/radon/media/` (compatibility symlink `~/radon-cloud/media`).
- Rollover at 500 KB: archive old posts and keep about 20%.
- `mergePosts` preserves tags.
- Do not honor JSON-LD `schema.image` when article DOM has no `<img>`; it can produce generic placeholder misattribution.

## Tagging

- Posts with images use vision tagger; text-only posts use Cerebras text tagger with fallback.
- Cerebras `gpt-oss-120b` needs `max_tokens: 800`.
- Exactly 3 tags per post.
- Normalize tags to uppercase, multi-word uppercase-kebab-case, allowed chars `A-Z 0-9 - &`, case-insensitive dedupe.
- `data/tag_taxonomy.json` is force-tracked.
- Either `CEREBRAS_API_KEY` or `ANTHROPIC_API_KEY` is sufficient.

## UI Contract

- Output shape is locked by `web/components/DashboardNewsFeed.tsx` (`MarketEarPost`).
- Filter chips use AND semantics when multiple tags are active.
- Deep-link tag filters via `/dashboard?tags=BTC,vol`.
