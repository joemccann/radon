# MarketDesk research ingest

`radon-marketdesk.timer` replaced the shared-folder Dropbox watcher
(`radon-research.service`, retired 2026-10-26) as the research feed's source.
Every 5 minutes it reads the marketdesk.ai library, opens each new article,
downloads the source PDF behind the article's `a.action.preview` link
(`/standalone/<id>`), stores the PDF and every article image as private research
assets, writes the post copy in Joe's voice through the shared model ladder, and
publishes one operator-only research post per article. The PDF is then copied
into the operator's own Dropbox under `/Radon/MarketDesk`.

Existing research posts, assets and `research_post_sources` rows from the old
watcher stay served. The v2 intake grounding and VERIFY pipeline is bypassed on
purpose: the copy is a voice rewrite of MarketDesk's own bullet summary and the
article text, guarded by a numeric firewall (below).

## Components

| Module | Role |
|---|---|
| `scripts/research/marketdesk_job.py` | One cycle: held-expiry sweep, scrape and publish, Dropbox outbox, health. Timer entry point, run by path. |
| `scripts/research/marketdesk_auth.py` | Firebase refresh-token exchange, password fallback, `POST /api/user/login`, Playwright storage-state reuse. |
| `scripts/research/marketdesk_bootstrap.py` | Laptop-only, headed. The operator signs in with Google once; the script captures the Firebase refresh token. Never run in a unit. |
| `scripts/research/marketdesk_scrape.py` | Chromium launch, library listing, article parse, PDF and image download, PNG re-encode. Every selector lives in one `SELECTORS` constant. |
| `scripts/research/marketdesk_copy.py` | System prompt (voice doc plus humanize rules), em-dash scrub, `copy_problems` validator, model ladder call. |
| `scripts/research/marketdesk_store.py` | Turso ledger `marketdesk_articles` (claim, status, upload outbox). |
| `scripts/research/dropbox_writer.py` | Write-scoped Dropbox client: identity check, idempotent upload, chronological path. |
| `scripts/research/publish.py` | `kind: "marketdesk"` provenance branch beside the unchanged `dropbox` branch. |
| `scripts/db/migrations/0102_marketdesk_articles.sql` | Ledger table; deletes the retired `dropbox-research` health row. |

## One cycle

1. `publish.expire_stale_held()` runs best effort, so held research outcomes keep
   expiring after the watcher that used to call it was retired.
2. Open the marketdesk.ai session (warm storage state, else refresh-token
   exchange, else the THEMARKETEAR password fallback). An auth failure fails the
   cycle (`marketdesk-research` error).
3. Drain the Dropbox upload outbox (up to 10 published rows whose upload is
   missing, skipped or failed). It runs before the scrape so a slow or killed
   scrape can never starve uploads.
4. List the library newest first, scrolling until the rendered list stops
   growing (bounded). Keep articles inside `MARKETDESK_LOOKBACK_HOURS`
   (default 48) that the ledger has not published or failed out, capped at
   `MARKETDESK_MAX_PER_CYCLE` (default 5). Anchors with no parseable `[time]`
   fail the cycle (`library_unparsed`). If the oldest listed item is newer than
   the newest article the ledger knows, the cycle still publishes and then
   reports `listing_gap`: items between them may have been missed.
5. Per article, inside its own try: claim and lease the ledger row, heal a crash
   between publish and ledger write (`research_post_sources` already has the
   post id), fetch the article, require a non-empty summary (`no_summary`),
   download the PDF, store up to 40 images (12 shown, each attached to the copy
   request), write the copy, publish, record. One bad article never fails the
   cycle. No new article starts after 150 seconds of the cycle; the rest wait
   for the next one.
6. Drain the outbox again when something was just published, persist the
   browser storage state and write the `marketdesk-dropbox` row (the worse of
   the two outbox runs).

Publication comes before the Dropbox upload: freshness first. The Dropbox status
lives in the ledger, not in post provenance, because the upload can complete in a
later cycle.

## Idempotency (Turso-first)

- The post id is a pure function of the article id:
  `stable_post_id("marketdesk:" + article_id, "article")`.
- The post `timestamp` is the article's own publication time, so even an
  accidental re-publish never bumps the feed.
- `marketdesk_articles` rows are `pending`, `published` or `failed`. A
  `pending` row is retried until 5 attempts, then flips to `failed` and is
  never retried.
- Only content verdicts spend an attempt (`no_pdf`, `not_pdf`, `no_summary`,
  `no_article_body`, every ladder rung rejecting the copy, HTTP 4xx). Outages
  are parked with no attempt spent: a ladder outage (HTTP, timeout, quota, no
  keyed rung), HTTP 5xx or 429, a transport error, a page whose title and
  preview never rendered, and an expired session (which also stops the cycle).
  Parked articles retry every cycle until they publish or leave the lookback.
- `claim` writes a lease before the slow work. A run killed mid-article
  (`TimeoutStartSec`, SIGTERM) leaves it, and the next claim counts that
  attempt, so an article that always overruns retires after 5 kills.
- Host disk holds only the session (`$RADON_RESEARCH_DIR/marketdesk/storage.json`
  and `firebase.json`, mode 0600) and a per-cycle temp workdir that is deleted
  at cycle end. Nothing durable is host-local.

## Copy rules

The system prompt is Joe's voice (`docs/joe-mccann-social-voice.md`) with the
humanize rules ported in. Em dashes are scrubbed in code before validation and
never burn a ladder rung. `copy_problems` rejects a draft (the ladder moves to the
next rung) for: a number that does not appear verbatim in the scraped source
text, any Market Ear or ZeroHedge mention, banned AI phrases, markdown, emoji,
caption keys that do not match the attached images, structural humanize tells
(`underscores`, a sentence-opening `Additionally,`, `serves as` / `stands as`,
`not just` / `not only ... but`, a trailing `, highlighting` style clause), or
length limits. The figures are attached to the request
(`complete_multimodal_json`), so captions are read off the images. Ladder
exhaustion leaves the article unpublished (see the attempt rules above). The
ladder is `scripts/clients/model_ladder.py`; no provider is hardcoded.

Summary bullets are shown verbatim, so each one is scrubbed (em dashes, curly
quotes) and dropped when it names Market Ear or ZeroHedge, carries a URL or
email, or uses a banned phrase. Article text excludes nav, header, footer, aside
and account UI, and an article page with no article container is refused rather
than read whole. The `MARKETDESK` tag is always first.

## Dropbox layout

Uploads go to the operator's own Dropbox, mirroring the shared folder's
chronological layout with the America/New_York date:

```text
/Radon/MarketDesk/<YYYY>/<Month>/<Mon> <DD>/<slug>-<article_id>.pdf
/Radon/MarketDesk/2026/October/Oct 02/fed-cuts-and-curve-4AJs5Adl2Yp.pdf
```

The day is zero-padded and the short month is the first three letters (`Sep`,
never `Sept`). Uploads use `mode=add`, `autorename=false`; a 409 conflict whose
content hash matches is success. The existing `DROPBOX_*` reader credentials are
read-only by contract and are never used for writes.

## Credentials and environment

All optional; each is read with a default. Documented in `cloud/.env.example`,
none in `cloud/config/required-env.txt`.

| Name | Effect when unset |
|---|---|
| `MARKETDESK_FIREBASE_REFRESH_TOKEN` | No session seed. The rotated token in `firebase.json` wins once present; otherwise the password fallback runs. |
| `THEMARKETEAR_EMAIL` / `THEMARKETEAR_PASSWORD` | Already required for the newsfeed. Fallback only; the account has no password sign-in method today. |
| `MARKETDESK_MAX_PER_CYCLE` / `MARKETDESK_LOOKBACK_HOURS` | 5 / 48. |
| `MARKETDESK_LIBRARY_URL` | `https://marketdesk.ai/`. |
| `MARKETDESK_ASSET_HOSTS` | Only `marketdesk.ai` serves PDFs and images. |
| `PLAYWRIGHT_CHROMIUM_EXECUTABLE` | The image-pinned Playwright headless shell. |
| `DROPBOX_WRITE_APP_KEY` / `DROPBOX_WRITE_REFRESH_TOKEN` / `DROPBOX_WRITE_ACCOUNT_ID` | Uploads skipped; posts still publish; `marketdesk-dropbox` reads `paused`. |
| `DROPBOX_WRITE_ROOT_NAMESPACE_ID` | Namespace check skipped. |
| `DROPBOX_WRITE_ROOT` | `/Radon/MarketDesk`. |

## Runtime

`cloud/services/radon-marketdesk.service` is a `Type=oneshot`, `User=radon`,
`UMask=0077` unit, `TimeoutStartSec=270` (under the 300s cadence),
`StartLimitIntervalSec=1800` with `StartLimitBurst=10` (6 healthy starts per
window). `radon-marketdesk.timer` fires `*:04/5:41`, `AccuracySec=1s`,
`Persistent=false`.

Its `runtime-container.conf` drop-in (control plane) runs the job in the Python
app image through `radon-app-runtime`:

- the private research bind `/var/lib/radon-private/research` read-write at
  `/var/lib/radon/research`, and no data, media or 2FA lease binds;
- an isolated bridge network, the vendored Chromium seccomp profile
  (`/etc/radon/seccomp/chromium.json`, refused if missing or untrusted) and a
  512m `/dev/shm`, the same isolation as the newsfeed;
- an env allowlist: Turso, the model ladder keys, `THEMARKETEAR_*`,
  `MARKETDESK_*`, `DROPBOX_WRITE_*` and the Playwright knobs. The read-only
  `DROPBOX_*` reader, B2, IB and Clerk secrets never enter the container;
- read-only `.grok`, `.codex` and `.claude` ladder grants, and no writable
  Antigravity grant (third-party Chromium content), so its ladder falls past
  antigravity.

The radon-api container keeps its read-only research bind, so existing and new
research assets are served by the same authenticated route.

## Health

| Row | Writer | Window | States |
|---|---|---|---|
| `marketdesk-research` | `service_cycle`, continuous bucket | 15m | `ok` every completed cycle (including one with no new article); `error` on auth, listing or Turso failure, a SIGTERM, a `listing_gap`, a cycle where articles failed or parked and none published, or any article retired to `failed`. Error text is the first line only (Playwright call logs carry cookies). |
| `marketdesk-dropbox` | `record_service_health`, continuous bucket | 15m | `ok` after a cycle with write credentials and no upload failure; `paused` when all three write credentials are absent (a warning, never the error bucket); `error` when an upload failed (retried next cycle) or when only some write credentials are set. |

Both rows are registered in `scripts/watchdog/services.py` and
`web/lib/serviceHealthWindows.ts`, and both map to `radon-marketdesk.service` in
`scripts/api/service_log_units.json` for the admin log view.

## Operator steps before enabling in production

1. Run `python scripts/research/marketdesk_bootstrap.py` on the laptop, sign in
   with Google, and copy the token from the 0600 file it writes (it prints no
   secret) into `/etc/radon/env` as
   `MARKETDESK_FIREBASE_REFRESH_TOKEN`. `--check` exchanges it once and reports
   `ok` or `failed`.
2. Capture real library, article and standalone HTML with that session, replace
   the synthetic parser fixtures and `SELECTORS`, and set
   `MARKETDESK_ASSET_HOSTS`. Every selector is unverified until then.
3. Create a full Dropbox (not App Folder) app on the operator account with
   `account_info.read`, `files.metadata.read` and `files.content.write`; mint an
   offline PKCE refresh token and set `DROPBOX_WRITE_*`.
4. After a green deploy, confirm the retired watcher is off and the timer is
   live, then that the first ledger row and post landed in Turso:

```bash
systemctl is-enabled radon-research.service      # disabled
systemctl list-timers radon-marketdesk.timer
journalctl -u radon-marketdesk.service -n 50
```

## Runbook

- **Symptom:** `marketdesk-research` is `error` or stale.
  - Read `journalctl -u radon-marketdesk.service -n 100`. `MarketDeskAuthError`
    means the session and the refresh token both failed: rerun the laptop
    bootstrap and replace `MARKETDESK_FIREBASE_REFRESH_TOKEN`.
  - A unit in `start-limit-hit` is parked by the crash-loop brake:
    `systemctl reset-failed radon-marketdesk.service` after fixing the cause.
  - A listing failure after a marketdesk.ai redesign is a selector change:
    update `SELECTORS` and the fixtures.
- **Symptom:** `marketdesk-dropbox` is `paused`. Write credentials are absent.
  Posts are still publishing; set `DROPBOX_WRITE_*` and the outbox uploads the
  backlog on the next cycles.
- **Symptom:** `marketdesk-dropbox` is `error`. An upload failed (conflict with
  different content, token, account or namespace mismatch). The row is retried
  every cycle; the ledger's `last_error` names the article.
- **Rollback:** `systemctl disable --now radon-marketdesk.timer` stops new posts.
  Published rows stay. The retired watcher is not a rollback target: deploys
  disable it on every `restart-managed` and `recover`.
