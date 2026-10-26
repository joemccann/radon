# MarketDesk fixtures

Used by `scripts/tests/test_marketdesk_auth.py` and `scripts/tests/test_marketdesk_scrape.py`.

## Real, unauthenticated captures (2026-10-26 scout, `curl -4`)

| File | What it is |
|---|---|
| `landing.html` | `GET https://marketdesk.ai/` (and the 302 target of `/library/browse/<id>`) while signed out |
| `app-landing.js` | Landing script: FirebaseUI, then `POST /api/user/login {F: idToken}` |
| `API.js` | Client helpers; `[time]` attributes hold unix seconds |
| `Wire.js` | WebSocket client (`page.reload` event) |
| `firebase_config.json` | Scout summary of the public `zerohedge-prod` Firebase web config. Drift guard for `FIREBASE_API_KEY` |

## SYNTHETIC (hand-written, NOT captured)

The library DOM, article DOM, standalone viewer and asset hosts were not reachable without a signed-in
session. These files encode assumptions only. Every selector they exercise lives in
`research.marketdesk_scrape.SELECTORS`.

| File | Assumption it encodes |
|---|---|
| `library.synthetic.html` | Items are `a[href*='/library/browse/<id>']` with a `[time]` unix-seconds element in or next to them |
| `article.synthetic.html` | `h1` title, `.publisher`, `.summary li` bullets, `img` charts, `a.action.preview` to `/standalone/<id>` |
| `standalone_viewer.synthetic.html` | `/standalone/<id>` answers with an HTML viewer embedding the PDF |
| `sample.pdf` | Tiny generated one-page PDF |
| `chart.jpg`, `chart.webp` | Tiny generated rasters for the PNG re-encode path |
| `logo.svg` | Scriptable SVG that must never be stored |

**Before enabling `radon-marketdesk.timer` in production:** run an authed probe, save the real library,
article and standalone responses here, delete the `*.synthetic.html` files, update `SELECTORS` and
`MARKETDESK_ASSET_HOSTS`, and make these tests pass against the captures.
