# MarketDesk research ingest

## Article-first publication and deferred PDF archive (2026-10-27)

The newsfeed publishes from the accessible MarketDesk article summary and chart images. Source bullets are input facts, never an appended list in the feed or reader. The model writes connected prose in Joe's voice, preserves source qualifications, and prefixes grounded security tickers with `$` before the final Humanizer review. Source identity, fidelity, novelty and >=90 Humanizer gates remain mandatory; every suitable MarketDesk topic is in scope.

PDF retrieval is not a publication dependency. New provenance uses `evidenceKind: article`, the exact MarketDesk article URL, no PDF page numbers, and a SHA-256 digest of the canonical article inputs. A later archived PDF adds a separate `pdfUrl`/`pdfHash` pair without changing article copy or its identity. Legacy PDF-backed posts remain supported; their raw summary lists no longer render.

Private editorial records live under `$RADON_RESEARCH_DIR/marketdesk/humanizer/<post-id>/<run-id>.json`. Each atomic snapshot retains the exact title/body, findings, deductions, qualitative checks, score and status for every rewrite attempt before further model I/O. Runs survive retries separately; final records bind the normalized `$` copy to its certificate hash. Directories are 0700, files 0600, and storage failures park the article without publishing or spending an attempt. These records never enter public post provenance.

Every discovered article is persisted before slow processing. Pending rows replay fairly from Turso even after leaving the latest-25 provider listing or the discovery lookback. Oversized source bullets fail explicitly instead of silently dropping a final qualification.

PDF archiving uses the authenticated JSON operation `GET /files/<id>/blob` on
https://marketdesk.ai; only the separate archive follows its authorized file redirect.
The independent [PDF archive service](marketdesk-pdf-archive.md) persists its own throttle/backoff and retries provider-authorized PDF access. The provider currently reports previews unavailable and downloads available, but no separate working download route has been verified. Its normal authenticated PDF operation can remain quota-blocked while newsfeed publication continues. No provider allowance is bypassed.


`radon-marketdesk.timer` replaced the shared-folder Dropbox watcher
(`radon-research.service`, retired 2026-10-26) as the research feed's source.
Every 5 minutes it signs in to https://marketdesk.ai, lists the latest
library items, and for each new item reads MarketDesk's bullet summary and chart
images, stores charts as private research assets, and applies source, identity
and duplicate safety. Every suitable report proceeds to copy in Joe's voice
through the shared model ladder and publishes one operator-only research post. Independently archived PDFs are
then copied into the operator's own Dropbox under `/Radon/MarketDesk`, mirroring
the library's folder layout, whether or not the item was published. No browser runs.

Existing research posts, assets and `research_post_sources` rows from the old
watcher stay served. MarketDesk admission is separate from the unchanged
Dropbox intake v2 relevance policy. GROUND, FIGURE MATCH and VERIFY calls are
not run. The copy is a voice rewrite
from MarketDesk's source title, bullet-summary facts and images, guarded by a
numeric firewall (below).

## Admission: all suitable MarketDesk reports

The operator selected "Publish all suitable reports" on 2026-10-27.
`scripts/research/marketdesk_filter.py` admits every MarketDesk report with
usable source facts, known publisher identity and nonduplicate content. FX
and single-name reports outside the portfolio/watchlist are in scope. The
separate Dropbox intake retains its existing triage and selector policy.

1. Identity: `research.identify` on the article title and source summary. The
   institution folder identifies the publisher; no PDF is read by publication.
2. Source facts must be present (`SOURCE_MATERIAL_MISSING` otherwise), and an
   unknown publisher remains held (`PUBLISHER_UNKNOWN`). The normal scraper
   parks not-yet-summarised articles before admission and eventually records
   `no_summary` after the existing wait/attempt limits.
3. Novelty: simhash against published MarketDesk rows and the legacy
   `fingerprints.json` drops `DUPLICATE_OF_PUBLISHED`. The existing
   always-publish desk exception across different publishers is unchanged.
4. Source summary and images go directly to copy generation. There is no
   preliminary relevance SELECT, portfolio lookup or preference/triage veto.
   Existing numerical/ticker/source validation and the final exact-copy
   Humanizer certificate remain required before publication. Source bullet
   lists are input only and are never displayed below the article.

Each cycle applies operator feedback first, preserving an actionable vote's
note before automatic replay can move that item to pending. Then
`marketdesk_store.requeue_suitable(limit=5)` atomically reopens
at most five held/dropped ledger rows carrying only retired relevance codes:
`DOC_TYPE_FX_PAIR_NOTE`, `SINGLE_STOCK_NOT_IN_BOOK`, `SERIES_DENYLIST`,
`RULE_DOC_TYPE`, `RULE_PUBLISHER`, `NO_CANDIDATES`, `INVALID_CANDIDATE`.
The last code referred to an invalid preliminary SELECT candidate, not a
failed copy certificate. Source/identity/duplicate decisions and all failed,
published or already-pending rows are excluded. Each reopened row leaves the
eligible set, so retries never reset a live lease or its attempt budget.
Metadata, stored assets, Dropbox outbox state and operator notes survive.
The durable pending queue processes these references even outside the latest
listing/lookback, applying all current source and editorial gates again.
No global status reset or manual replay command is needed after deployment.

Outcomes remain mirrored to `research_outcomes` for Held-tab/history support.
Feedback sync still records preferences for the separate intake, and an
explicit "should have published" vote can reopen held/dropped MarketDesk work.
Its note is retained in the private review, but cannot override source,
identity or duplicate safety. A "want more" vote on a published MarketDesk
post remains handled without revising the existing article.

## marketdesk.ai API (captured signed in, 2026-10-26)

The web app is a thin client over a cookie-authenticated JSON API. Real
responses are the test fixtures in `scripts/tests/fixtures/marketdesk/api_*.json`.

| Call | Returns |
|---|---|
| `POST /api/user/login {F: <Firebase ID token>}` | `{success: true}` and the httpOnly session cookie `A` (about 60 days). |
| `POST /api/mobile/latest/latest {institutions: []}` | The 25 newest item ids, newest first. |
| `POST /api/mobile/items/provision {pathIds: [...]}` | Per item: `name`, `t` (unix seconds), `type`, `institution`. |
| `GET /api/mobile/items/<id>/extra` | `summary` (markdown bullets `- **Head**: text`) and `images` (`iid`, `page`). Empty, or a `**Document Missing**` placeholder, until MarketDesk has processed the item. |
| `GET /api/mobile/items/<id>/path` | Folder chain: `Current / 2026 / October / Oct 26 / <bank> / <desk>...`. |
| `GET /files/<id>/blob` | The PDF bytes. `/standalone/<id>` is a pdf.js viewer over this URL. |

Signed out, an API call answers HTTP 200 `{"success": false}` and `/files/*`
answers 302; the session re-logs in once and retries. Chart images are public
objects at `https://box1.nyc3.cdn.digitaloceanspaces.com/charts/<id>/<iid>`,
fetched with a cookie-less opener that never follows redirects.

## Components

| Module | Role |
|---|---|
| `scripts/research/marketdesk_job.py` | One cycle: held-expiry sweep, operator feedback, policy replay, scrape, admission, publish, Dropbox outbox, health. Timer entry point, run by path. |
| `scripts/research/marketdesk_filter.py` | MarketDesk-only source/identity/novelty admission; all suitable reports go to grounded copy. |
| `scripts/research/marketdesk_auth.py` | Firebase refresh-token exchange, password fallback, `POST /api/user/login`, cookie session with one re-login on a signed-out answer. |
| `scripts/research/marketdesk_bootstrap.py` | Laptop only. `--from-cdp` reads the Firebase refresh token from a signed-in marketdesk.ai tab in Chrome; `--check` exchanges it, logs in and lists. Never run in a unit. |
| `scripts/research/marketdesk_scrape.py` | JSON API client: latest listing, summary, chart images, folder chain, PDF blob, PNG re-encode. |
| `scripts/research/marketdesk_copy.py` | System prompt (voice doc plus humanize rules), em-dash scrub, `copy_problems` validator, model ladder call. |
| `scripts/research/marketdesk_store.py` | Turso ledger `marketdesk_articles` (claim, status, admission decision, bounded policy replay, re-queue, upload outbox) and `marketdesk_feedback_handled`. |
| `scripts/research/dropbox_writer.py` | Write-scoped Dropbox client: identity check, idempotent upload, mirrored library path. |
| `scripts/research/publish.py` | `kind: "marketdesk"` provenance branch beside the unchanged `dropbox` branch. |
| `scripts/db/migrations/0102_marketdesk_articles.sql` | Ledger table; deletes the retired `dropbox-research` health row. |

## One cycle

1. `publish.expire_stale_held()` runs best effort, so held research outcomes keep
   expiring after the watcher that used to call it was retired.
2. Drain the Dropbox upload outbox (up to 10 published, held or dropped rows whose upload is
   missing, skipped or failed). It runs before the scrape so a slow or killed
   scrape can never starve uploads.
3. Sync feedback and re-queue actionable votes, retaining their notes first.
   Then reopen up to five retired relevance decisions (see Admission).
   A feedback fetch failure changes no vote state; automatic replay still runs.
4. Sign in: refresh-token exchange (else the THEMARKETEAR password fallback),
   then `POST /api/user/login`. An auth failure fails the cycle
   (`marketdesk-research` error).
5. List the 25 newest items. Items whose institution is The Market Ear or
   ZeroHedge are excluded (radon-newsfeed already carries that feed). Keep items
   inside `MARKETDESK_LOOKBACK_HOURS` (default 48) that the ledger has not
   published, held, dropped or failed out. Persist every new reference before
   processing; replay all durable pending rows with operator re-queues first
   and least-recently-attempted rows next, even outside that listing/lookback. If the oldest listed item is newer than the newest
   article the ledger knows, the cycle still publishes and then reports
   `listing_gap`: items between them may have been missed.
6. Per item, inside its own try: claim and lease the ledger row, heal a crash
   between publish and ledger write (`research_post_sources` already has the
   post id), read the summary and folder chain, run source/identity/novelty admission (held or dropped items stop here,
   counted against `MARKETDESK_MAX_PER_CYCLE`), store up to 40 images (12 shown,
   each attached to the copy request), write grounded prose from the original
   source facts, certify the exact final copy, publish, record. An item MarketDesk has not summarised yet is parked as
   `summary_pending` (no attempt spent, not a failure, not counted against
   `MARKETDESK_MAX_PER_CYCLE`, default 5) and fails as `no_summary` once older
   than `MARKETDESK_SUMMARY_WAIT_HOURS` (default 6). One bad item never fails
   the cycle. No new item starts after 150 seconds of the cycle; the rest wait
   for the next one.
7. Drain the outbox again when something was just published, held or dropped and write the
   `marketdesk-dropbox` row (the worse of the two outbox runs).

Publication comes before the Dropbox upload: freshness first. The Dropbox status
lives in the ledger, not in post provenance, because the upload can complete in a
later cycle.

## Idempotency (Turso-first)

- The post id is a pure function of the article id:
  `stable_post_id("marketdesk:" + article_id, "article")`.
- The post `timestamp` is the article's own publication time, so even an
  accidental re-publish never bumps the feed.
- `marketdesk_articles` rows are `pending`, `published`, `held`, `dropped` or
  `failed`; held and dropped come back only through an operator re-queue. A
  `pending` row is retried until 5 attempts, then flips to `failed` and is
  never retried.
- Only article content verdicts spend an attempt (`no_summary`, `summary_too_large`,
  `bad_response`, every ladder rung rejecting the copy, HTTP 4xx). Outages are
  parked with no attempt spent: a ladder outage (HTTP, timeout, quota, no keyed
  rung), HTTP 5xx or 429, a transport error, and a session that stays signed
  out after one re-login (which also stops the cycle).
  Parked articles remain durably replayable after they leave the discovery lookback.
- PDF provider denials belong exclusively to the separate archive service. A
  `preview_quota` response persists an independent retry/backoff and an error
  health row; it does not park or stop article publication. No reset time is
  assumed. Existing pending article rows from the former PDF-first path replay
  automatically under the article-only flow.
- `claim` writes a lease before the slow work. A run killed mid-article
  (`TimeoutStartSec`, SIGTERM) leaves it, and the next claim counts that
  attempt, so an article that always overruns retires after 5 kills.
- A SIGTERM exits 143 and the unit maps it with `SuccessExitStatus=143`: a
  deploy's `stop-clean` lands on this 5-minute oneshot often and must not leave
  it `failed`. A hung cycle still fails as `Result=timeout`, and the
  `marketdesk-research` health row still records the SIGTERM as `error`. Not
  75: `radon-app-runtime run` exits 75 for a surviving orphan container.
  Managed cancellation bypasses provider exception handlers, so interrupted
  requests do not advance the model ladder; cleanup and the error heartbeat
  still run before exit 143.
- `TimeoutStopSec=40` (this unit and `radon-marketdesk-pdf.service`): a cycle
  that does not exit on SIGTERM is SIGKILLed before the deploy helper's 60s
  stop wait ends. The 90s default outlived that wait on 2026-10-27 and the
  deploy exited 71 with every radon service stopped. Pinned by
  `cloud/tests/test_systemd_services.py::TestMarketdeskStopFitsTheDeployWait`.
- The private research store holds the rotated refresh token
  (`$RADON_RESEARCH_DIR/marketdesk/firebase.json`, mode 0600), image/PDF assets
  and editorial audits. The per-cycle temp workdir is deleted at cycle end;
  the session cookie lives in memory for one cycle. Article status, archive
  admission and published provenance remain canonical in Turso.

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
ladder is `scripts/clients/model_ladder.py`. The image copy call skips grok
(`IMAGE_PROVIDERS`): grok-4.6 took 153s or hit the 120s read timeout on every
image call (2026-10-27), most of the 270s cycle budget. Captions obey the same
numeric rule as the content (system rule 26), since chart values are not in
the source text.

`research.publish` refuses any research post without a valid Humanizer
certificate (`scripts/research/humanizer.py`), so `marketdesk_copy.certify` runs
the shared Humanizer review over the accepted title and content through
`complete_text_json`. A rewrite it requests is accepted only when it still passes
`copy_problems` against the same source text and needs no em-dash or quote scrub.
Each review and rewrite reply goes through `humanizer.reply_ok` as the ladder's
accept hook, so a rung that misquotes the copy falls to the next rung instead of
failing the article (`malformed_review`). A review the ladder cannot reach parks
the article; a failed review spends an attempt. The certificate is stripped from `research_post_sources` on publish.

Source bullets are bounded input only: up to 20 items, at most 3000 characters
per item. Oversized input is rejected explicitly rather than silently losing
a trailing qualification. Markdown bold markers are stripped during parsing.
Source bullets are never appended to the article or reader. Actual security
ticker symbols receive `$` before the final Humanizer score and certificate;
an overlapping symbol needs an explicit source security marker and stock
context at that occurrence. Ordinary AI spending, country/currency labels
and compound index labels stay plain. Unclear bare security mentions fail
validation and must be clarified before certification. The publisher is the bank folder of the library chain (`Goldman Sachs`,
not the `S&T` desk label), else the item's institution. The `MARKETDESK` tag is
always first.

## Dropbox layout

Uploads go to the operator's own Dropbox (personal account, home namespace),
mirroring the marketdesk.ai library chain, which is the shared folder's layout,
and keeping the library item name:

```text
/Radon/MarketDesk/<YYYY>/<Month>/<Mon> <D>/<bank>[/<desk>...]/<item name>.pdf
/Radon/MarketDesk/2026/October/Oct 26/Goldman Sachs/S&T/GS Molavi Ruminations Macro, Micro, Markets 26 Oct 2026.pdf
```

The chain is stored in the post provenance (`folders`, `fileName`) and in the
ledger (`folders_json`, `file_name`, for held and dropped items) so the outbox
can upload in a later cycle. Separators, colons and control characters in a name
become spaces; leading and trailing dots and spaces are trimmed. A post without a
dated chain falls back to `/<YYYY>/<Month>/<Mon> <DD>/<slug>-<article_id>.pdf` on
the America/New_York date. Uploads use `mode=add`, `autorename=false`; a 409
conflict whose content hash matches is success, and a same-name conflict with
different bytes retries once as `<item name> (<article_id>).pdf`. The existing
`DROPBOX_*` reader credentials are read-only by contract and are never used for
writes.

## Credentials and environment

All optional; each is read with a default. Documented in `cloud/.env.example`,
none in `cloud/config/required-env.txt`.

| Name | Effect when unset |
|---|---|
| `MARKETDESK_FIREBASE_REFRESH_TOKEN` | No session seed. The rotated token in `firebase.json` wins once present; otherwise the password fallback runs. |
| `THEMARKETEAR_EMAIL` / `THEMARKETEAR_PASSWORD` | Already required for the newsfeed. Fallback only; the account has no password sign-in method today. |
| `MARKETDESK_MAX_PER_CYCLE` / `MARKETDESK_LOOKBACK_HOURS` | 5 / 48. |
| `MARKETDESK_SUMMARY_WAIT_HOURS` | 6: an unsummarised item fails as `no_summary` after 6 hours. |
| `MARKETDESK_ASSET_HOSTS` | Only the MarketDesk chart CDN serves images. |
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
- an isolated bridge network, the vendored seccomp profile
  (`/etc/radon/seccomp/chromium.json`, refused if missing or untrusted) and a
  512m `/dev/shm`, the same isolation as the newsfeed. The job no longer runs a
  browser; the isolation stays because it still parses third-party content;
- an env allowlist: Turso, the model ladder keys, `THEMARKETEAR_*`,
  `MARKETDESK_*` and `DROPBOX_WRITE_*`. The read-only `DROPBOX_*` reader, B2, IB
  and Clerk secrets never enter the container;
- read-only `.grok`, `.codex` and `.claude` ladder grants, and no writable
  Antigravity grant (third-party content), so its ladder falls past
  antigravity.

The radon-api container keeps its read-only research bind, so existing and new
research assets are served by the same authenticated route.

## Health

| Row | Writer | Window | States |
|---|---|---|---|
| `marketdesk-research` | `service_cycle`, continuous bucket | 15m | `ok` every completed cycle (including one with no new article); `error` on auth, listing or Turso failure, a SIGTERM, a `listing_gap`, a cycle where articles failed or parked and none published, or any article retired to `failed`. Error text is the first line only, bounded. An item waiting for its summary is not an error. |
| `marketdesk-dropbox` | `record_service_health`, continuous bucket | 15m | `ok` after a cycle with write credentials and no upload failure; `paused` when all three write credentials are absent (a warning, never the error bucket); `error` when an upload failed (retried next cycle) or when only some write credentials are set. |

Both rows are registered in `scripts/watchdog/services.py` and
`web/lib/serviceHealthWindows.ts`, and both map to `radon-marketdesk.service` in
`scripts/api/service_log_units.json` for the admin log view.

## Operator steps before enabling in production

1. Sign in to marketdesk.ai with Google in a Chrome started with
   `--remote-debugging-port=9222`, then run
   `python scripts/research/marketdesk_bootstrap.py --from-cdp http://127.0.0.1:9222`.
   Copy the token from the 0600 file it writes (it prints no secret) into
   `/etc/radon/env` as `MARKETDESK_FIREBASE_REFRESH_TOKEN`. `--check` exchanges
   it, logs in and lists the latest items, then reports `ok` or `failed`.
2. Create a full Dropbox (not App Folder) scoped app on the operator's personal
   account with `account_info.read`, `files.metadata.read` and
   `files.content.write`; mint an offline PKCE refresh token and set
   `DROPBOX_WRITE_*`. Done 2026-10-26: app `radon-marketdesk-writer`, personal
   account; the values are in the laptop `.env`.
3. After a green deploy, confirm the retired watcher is off and the timer is
   live, then that the first ledger row and post landed in Turso:

```bash
systemctl is-enabled radon-research.service      # disabled
systemctl list-timers radon-marketdesk.timer
journalctl -u radon-marketdesk.service -n 50
```

## Runbook

- **Symptom:** `marketdesk-research` is `error` or stale.
  - Read `journalctl -u radon-marketdesk.service -n 100`. `MarketDeskAuthError`
    means the refresh token and the password fallback both failed: rerun the
    laptop bootstrap and replace `MARKETDESK_FIREBASE_REFRESH_TOKEN`.
  - A unit in `start-limit-hit` is parked by the crash-loop brake:
    `systemctl reset-failed radon-marketdesk.service` after fixing the cause.
  - `bad_response` or HTTP 4xx on every item after a marketdesk.ai change is an
    API change: recapture the `api_*.json` fixtures signed in and update
    `marketdesk_scrape.py`.
- **Symptom:** `marketdesk-dropbox` is `paused`. Write credentials are absent.
  Posts are still publishing; set `DROPBOX_WRITE_*` and the outbox uploads the
  backlog on the next cycles.
- **Symptom:** `marketdesk-dropbox` is `error`. An upload failed (conflict with
  different content, token, account or namespace mismatch). The row is retried
  every cycle; the ledger's `last_error` names the article.
- **Rollback:** `systemctl disable --now radon-marketdesk.timer` stops new posts.
  Published rows stay. The retired watcher is not a rollback target: deploys
  disable it on every `restart-managed` and `recover`.

## Generated-copy editorial admission

Every generated title, body and figure caption passes the shared Humanizer
review before publication. The exact-copy certificate includes captions and the
current rubric version. Deterministic structural checks reject negative
parallelisms, including comma and sentence-split variants, even when a model
reports no findings. A score of at least 90 and no qualitative findings are
required. Rewrites retain source numbers, attribution, direction and conditions;
a separate semantic check precedes the last editorial review. Failed captions
hold the article for regeneration. Stale outbox certificates require re-review.

The cross-service contract and share/cache behavior are documented in
[Generated-copy Humanizer gate](generated-copy-humanizer.md). Existing published
rows require separate reviewed corrections; deploying this gate does not rewrite
article history.
