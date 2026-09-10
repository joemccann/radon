# site/ — radon.run Marketing Site (Codex)

Mirrors `site/CLAUDE.md` — read that for the full detail. Public marketing site at **radon.run** (apex; the app is `web/` = app.radon.run). Next.js 16 + React 19 + Tailwind v4 + TypeScript, App Router, atomic-design.

- **Design**: Clear, selected with app.radon.run (2026-09-05). Paper canvas, evergreen actions, Inter, 12px metadata floor, 6–10px radii. Marketing site, not a logged-out app clone.
- **Product story**: (1) point-of-view construction from scanners, news, and flow; (2) optimal expression via options, stock, or futures. Gates remain as order-path discipline, not the identity.
- **Conventions**: components/{atoms,molecules,organisms,sections}, named exports, `@/` alias, Tailwind v4 `@theme` tokens in `app/globals.css`, Inter + IBM Plex Mono, existing `lib/theme.ts` (default **light**), `"use client"` only where stateful. Preserve SEO infra (`lib/seo.ts` SITE_NAME stays "Radon Terminal"). `value-prop.test.ts` pins the two-system copy.
- **Product plates** (`components/molecules/ProductPlate.tsx`, `public/plates/`): real app screenshots captured via the chrome-cdp skill. Anonymize username to `radon-user`. Capture light+dark pairs. Cookie-gated newsfeed images need an image-load wait. The portfolio plate is a RECREATION. Source copies in `../marketing-mockups/shots/`.
- **⛔ PII**: anonymizing the username does NOT anonymize the data. Swap account-figure plates to demo data before any public deploy.
- **⛔ Deploy**: Vercel (root `site/`); `git push origin main` with a `site/` change AUTO-DEPLOYS radon.run publicly.
