# MarketDesk PDF archive

Article publication and PDF archiving are separate services. The article
service uses the provider's summary and chart images, writes the news article
in the operator's voice, and publishes without waiting for a PDF. This archive
does not generate or edit article copy.

`radon-marketdesk-pdf.timer` fires at `*:02/5:17`, separate from the article
timer. Its immutable Python container runs as the application UID behind an
isolated bridge network. It receives only Turso, the existing MarketDesk login
seed/fallback and the private research directory. It receives no broker,
secret-store master key, model grants or Dropbox credentials. The normal green
production deploy installs the unit, timer and root-owned runtime drop-in;
there is no separate root installation step.

## Durable queue and limits

Migration `0103_marketdesk_pdf_queue.sql` creates `marketdesk_pdf_queue` and a
singleton `marketdesk_pdf_throttle`. Article discovery calls `enqueue(ref)`.
Every archive cycle also seeds missing PDFs from the entire article ledger,
including older pending, held and dropped documents outside the latest-25
provider listing. Existing stored PDFs are not downloaded again.

One cycle admits at most one download. The default global minimum interval is
300 seconds. `MARKETDESK_PDF_MIN_INTERVAL_SECONDS` may increase that interval;
values below 300 are clamped. Admission is committed in Turso before network
work, so a restart cannot reset the throttle. A 600-second token lease permits
recovery after a killed worker and prevents a stale lease owner from committing
over a replacement attempt.

The archive uses the existing authenticated `/files/<id>/blob` operation,
which the provider routes through its preview file server. Its historical
success does not override the current provider allowance. No working independent
download endpoint has been verified; a `downloads: true` account flag does not
make a blocked preview hop work. The archive never guesses file-server URLs or
bypasses a provider refusal. Provider allowance/session errors defer the queue
for one hour; other download failures defer it for at
least 15 minutes. Both the item retry and the global backoff are persisted.
Queued work is retained rather than retired after a fixed number of failures.
Blocked archive health does not stop news publication.

## Storage, recovery and attachment

PDFs must have a PDF signature, fit the 100 MiB limit, and pass the existing
content-addressed asset store. They remain private and use the authenticated
`/api/newsfeed/research/files/<sha256>.pdf` route. Private per-article receipts
are written atomically and fsynced before the job WAL and Turso acknowledgement.
The long-horizon directory is `$RADON_RESEARCH_DIR/jobs/marketdesk-pdfs/` with
`findings.jsonl`, `state.json`, and `SUMMARY.md`. On recovery a saved receipt is
accepted only after verifying the stored PDF bytes against its digest. Three
SIGKILL tests cover receipt, WAL and database acknowledgement boundaries.

The attachment transaction compares the previously read provenance before
adding `source.pdfUrl` and `source.pdfHash`. A concurrent provenance change is
retried on a later cycle. The archive does not change `contentHash`, `url`,
article identity, title, body or Humanizer certificate. The existing article
ledger receives `pdf_asset` and `pdf_sha256`, preserving its Dropbox outbox:
the article service uploads a ready PDF on its next normal cycle. A PDF saved
before publication is attached after the article exists; held and dropped
notes retain their existing PDF outbox eligibility.

## Health and verification

`marketdesk-pdf` uses a continuous 15-minute freshness window, maps to
`radon-marketdesk-pdf.service`, and reports error during provider backoff.
A normal admission wait with no provider failure is healthy. This row is
separate from `marketdesk-research` and `marketdesk-dropbox`; a healthy article
cycle cannot conceal a blocked archive.

Focused contracts are in `scripts/tests/test_marketdesk_pdf_store.py`,
`scripts/tests/test_marketdesk_pdf_job.py`, and
`cloud/tests/test_marketdesk_pdf_runtime.py`. The parent MarketDesk release owns
the complete test gates and production backlog/browser verification.

Rollback: stop and disable `radon-marketdesk-pdf.timer` through the managed
service controls before reverting to a runtime that does not recognize its
unit. The additive queue migration and private assets can remain; they never
alter published article text or require deletion.
