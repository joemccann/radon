# Radon Site

Marketing site for Radon, built as a standalone Next.js app in `site/`.

## What Lives Here

- A standalone, crawlable Next.js App Router marketing site for the Radon brand and product narrative
- The institutional-terminal landing page under `app/`
- Reusable section and content primitives under `components/` and `lib/`
- Site-only deployment and verification helpers under `scripts/`

## Local Development

```bash
cd site
npm install
npm run dev
```

The site runs on `http://localhost:3333`.

For canonical URLs and share metadata, set:

```bash
NEXT_PUBLIC_SITE_URL=https://radon.run
```

If the deployed hostname changes, update that value in Vercel so `canonical`, `robots`, `sitemap`, JSON-LD, and social cards all emit the correct absolute URL.

## Verification

```bash
cd site
npm run lint
NEXT_DIST_DIR=.next-build npm run build
python3 scripts/seo_audit_report.py
```

`NEXT_DIST_DIR` is supported so local verification can build without colliding with another live Next.js process using the default `.next/` directory.

## SEO Audit Workflow

The site ships an audit script that verifies the rendered landing page, `robots.txt`, `sitemap.xml`, `manifest.webmanifest`, and the social-image metadata routes against a local production server.

```bash
cd site
python3 scripts/seo_audit_report.py
```

Use `NEXT_PUBLIC_SITE_URL=https://radon.run` in production so canonical URLs, sitemap entries, and Open Graph/Twitter URLs resolve to the public hostname. The script writes an HTML report to `reports/` and opens it unless `--no-open` is passed.

## SEO Surface

The site publishes its crawl/share primitives through App Router metadata routes and a shared SEO helper:

- `site/lib/seo.ts` defines canonical metadata, viewport, Open Graph, Twitter cards, and JSON-LD.
- `site/app/robots.ts` emits `robots.txt`.
- `site/app/sitemap.ts` emits `sitemap.xml`.
- `site/app/manifest.ts` emits `manifest.webmanifest`.
- `site/app/opengraph-image.tsx` and `site/app/twitter-image.tsx` generate branded share cards.

## SEO Audit Report

Generate a live HTML audit report against a running site instance:

```bash
python3 scripts/site_seo_audit.py --url http://127.0.0.1:3333 --open
```

If local ports are unavailable, audit the verified static build artifacts instead:

```bash
cd site
NEXT_DIST_DIR=.next-build npx next build --webpack
cd ..
python3 scripts/site_seo_audit.py --build-dir site/.next-build/server/app --open
```

The script writes `reports/site-seo-audit-YYYY-MM-DD.html`, checks the homepage plus `robots.txt`, `sitemap.xml`, `manifest.webmanifest`, and the social image endpoints, then opens the report locally when `--open` is passed.

## Vercel Deployment

The Vercel project for the site should use `site/` as its **Root Directory**.

This app includes [vercel.json](/Users/joemccann/dev/apps/finance/radon/site/vercel.json) with an `ignoreCommand` that only allows a deploy to continue when files under `site/` changed. Pushes that only touch `web/`, `scripts/`, `data/`, or other repo paths will skip the site build.

The ignore step is implemented by [vercel-ignore-build.mjs](/Users/joemccann/dev/apps/finance/radon/site/scripts/vercel-ignore-build.mjs). It compares the current commit against the previous deployed commit and defaults to **continuing the build** if Vercel cannot determine the diff.
