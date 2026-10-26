# MarketDesk fixtures

Used by `scripts/tests/test_marketdesk_auth.py` and `scripts/tests/test_marketdesk_scrape.py`.

## Real captures of the signed-in JSON API (2026-10-26)

Response bodies only: no cookies, tokens, request headers or account details. Trimmed where noted.

| File | Call |
|---|---|
| `api_latest.json` | `POST /api/mobile/latest/latest {"institutions": []}`: the 25 newest item ids |
| `api_provision.json` | `POST /api/mobile/items/provision`: four of those 25 items (CBOE, Goldman Sachs desk, an unprocessed UBS item, a Market Ear item) |
| `api_extra_4AJs5Adl2Yp.json` | `GET /api/mobile/items/4AJs5Adl2Yp/extra`: processed item, markdown bullet summary and 9 chart images |
| `api_extra_Pd4eYWnz5FH.json` | Same call for an item MarketDesk has not processed: the `**Document Missing**` placeholder, no images |
| `api_path_4AJs5Adl2Yp.json` | `GET /api/mobile/items/4AJs5Adl2Yp/path`: `Current / 2026 / October / Oct 26 / CBOE` |
| `api_path_eQJttFDd58v.json` | Same call for a Goldman Sachs desk item: `... / Goldman Sachs / S&T` |

## Unauthenticated captures (2026-10-26)

| File | What it is |
|---|---|
| `app-landing.js` | Landing script: FirebaseUI, then `POST /api/user/login {F: idToken}` |
| `firebase_config.json` | Public `zerohedge-prod` Firebase web config. Drift guard for `FIREBASE_API_KEY` |

## Generated

| File | Use |
|---|---|
| `sample.pdf` | Tiny one-page PDF (the real PDFs are third-party research and are not committed) |
| `chart.jpg`, `chart.webp` | Tiny rasters for the PNG re-encode path |
| `logo.svg` | Scriptable SVG that must never be stored |
