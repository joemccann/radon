# site/ — radon.run Marketing Site (Codex)

Mirrors `site/CLAUDE.md` — read that for the full detail. Public marketing site at **radon.run** (apex; the app is `web/` = app.radon.run). Next.js 16 + React 19 + Tailwind v4 + TypeScript, App Router, atomic-design.

- **Design**: Clear, selected with app.radon.run (2026-09-05). Paper canvas, evergreen actions, Inter, 12px metadata floor, 6–10px radii. Marketing site, not a logged-out app clone.
- **Product story**: (1) point-of-view construction from scanners, news, and flow; (2) optimal expression via options, stock, or futures. Gates remain as order-path discipline, not the identity.
- **Conventions**: components/{atoms,molecules,organisms,sections}, named exports, `@/` alias, Tailwind v4 `@theme` tokens in `app/globals.css`, Inter + IBM Plex Mono, existing `lib/theme.ts` (default **light**), `"use client"` only where stateful. Preserve SEO infra (`lib/seo.ts` SITE_NAME stays "Radon Terminal"). `value-prop.test.ts` pins the two-system copy.
- **Product plates** (`components/molecules/ProductPlate.tsx`, `public/plates/`): real app screenshots captured via the chrome-cdp skill. Anonymize username to `radon-user`. Capture light+dark pairs. Cookie-gated newsfeed images need an image-load wait. The portfolio plate is a RECREATION. Source copies in `../marketing-mockups/shots/`.
- **⛔ PII**: anonymizing the username does NOT anonymize the data. Swap account-figure plates to demo data before any public deploy.
- **⛔ Deploy**: Vercel (root `site/`); `git push origin main` with a `site/` change AUTO-DEPLOYS radon.run publicly.

<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
