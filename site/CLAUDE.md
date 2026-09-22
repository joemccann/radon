# site/ — radon.run Marketing Site

The public marketing site at **radon.run** (apex). Separate from the app (`web/` = app.radon.run). Next.js 16 + React 19 + Tailwind v4 + TypeScript, App Router, atomic-design components. Operator-only to *deploy* (see below), but the source lives here in the radon repo.

## Design: Clear (selected with app.radon.run)

The site uses the same **Clear** system as the workstation (selected 2026-09-05). Paper canvas, evergreen actions, Inter hierarchy, tabular amounts, 12px metadata floor, 6–10px radii. It is a marketing site, not a logged-out clone of the app chrome.

**Product story (two systems):**
1. **Point of view construction** — scanners, news, dark-pool / OTC flow, CRI / GEX / VCG-R / GRG.
2. **Expression** — the cheapest convex way to own that view (options, stock, futures).

Four sequential gates still sit on the order path. They are the discipline, not the identity. Do not sell Radon as "gates only."

## Conventions (match these)

- **Atomic design**: `components/{atoms,molecules,organisms,sections}`. Named exports matching filenames; `interface Props` at top; `@/` import alias; Tailwind utility classes driven by tokens (`bg-canvas`, `text-primary`, `border-grid`, `text-signal-deep`, `bg-figure-bg`); no CSS modules. `"use client"` only where stateful.
- **Tokens**: in `app/globals.css` via Tailwind v4 `@theme`. Clear light is the default; `html[data-theme="dark"]` is the quiet dark counterpart. No parallel theme store.
- **Fonts**: `next/font/google` in `app/layout.tsx` (Inter + IBM Plex Mono). Inter for UI and display. IBM Plex Mono only for secondary numeric detail. No Newsreader. No new display face.
- **Theme**: `lib/theme.ts` + `ThemeToggle` (`data-theme` on `<html>`, localStorage key `theme`, pre-hydration script in `layout.tsx`). Default is **light** (`DEFAULT_SITE_THEME`). Don't add a second toggle.
- **SEO/infra**: preserve `app/{layout,robots,sitemap,manifest,global-error}.tsx` + `lib/seo.ts` (`SITE_NAME` stays "Radon Terminal"). Update `SITE_DESCRIPTION` when the two-system story changes; `value-prop.test.ts` pins it.
- **Copy**: no em dashes. Name view construction and expression on every public surface that describes the product.

## ⛔ Product Plates — how we show the product (READ before touching plates)

The site shows the real product as framed plates (`components/molecules/ProductPlate.tsx` + `PlateFrame.tsx`), in `public/plates/`. They are REAL screenshots of the authenticated app, captured + curated like this:

- **Capture**: via the `chrome-cdp` skill against the debug Chrome on `:9222` (authenticated to app.radon.run). Open a NEW tab, work in it, CLOSE it after — never hijack the user's tabs. Set theme via `localStorage['radon-theme'|'theme']` + `data-theme` on `<html>` then nav (reload applies pre-hydration). Capture **light + dark pairs** at ~1512×862 (high-DPR). `ProductPlate` swaps light/dark via `[data-theme]`.
- **Anonymize the identity**: swap the sidebar username to **`radon-user`** (+ `RA` avatar) via a DOM **re-applier** (`setInterval(fn, 200)`) so it survives React re-renders, then capture. Verify `/mccann/i` is absent before shooting.
- **Cookie-gated newsfeed images**: the dashboard LIVE MARKET FEED images load via `_next/image` proxying `media.radon.run` and take ~9-12s. They capture EMPTY if you shoot early. WAIT: poll `img.complete && img.naturalWidth>0`, and reset the feed rail's `scrollTop` (it is an independently-scrolling container so the first article image must be in-viewport). Verify the image rendered by reading the PNG back.
- **Portfolio plate is a RECREATION, not a capture**: `../marketing-mockups/portfolio-recreation.html` renders fabricated-but-internally-consistent positive P&L (MV = Last×contracts×100, Initial = Entry×contracts×100, P&L = MV−Initial, P&L% = P&L/Initial) with no real positions. Use this pattern when a plate must show specific (e.g. positive) numbers without exposing the real account.
- **Source copies** live in `../marketing-mockups/shots/`; the live assets are `public/plates/`. Update BOTH on a re-capture.

**⛔ PII RULE — figures vs identity.** Anonymizing the username does NOT anonymize the data. The **dashboard plate still shows REAL account FIGURES** (net liq, day P&L, open risk, cash, real working/filled orders). GEX/VCG plates are market-wide data (not account PII); the portfolio plate is already demo (recreation). Before ANY public deploy, swap the dashboard (and any account-figure plate) to demo/synthetic data — recreate it like the portfolio. Publishing real account financials is irreversible.

## Deploy — pushing publishes radon.run

**Vercel**, root directory `site/`. `vercel.json` has an `ignoreCommand` (`scripts/vercel-ignore-build.mjs`) that builds ONLY when files under `site/` changed (pushes touching only `web/`/`scripts/`/`data/` skip the site build) and defaults to building if the diff is undeterminable. So **`git push origin main` with any `site/` change auto-deploys radon.run publicly.** radon.run is operator-only (see `feedback_no_radon_run_prod_deploy`): do not push site changes that should not go live, and clear the PII rule above first. `NEXT_PUBLIC_SITE_URL=https://radon.run` in prod drives canonical/OG/sitemap URLs. The app CI (`.github/workflows/ci.yml`) runs the repo-root Vitest which INCLUDES `site/lib` tests — keep them green.
