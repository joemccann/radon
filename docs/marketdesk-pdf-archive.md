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

Migration `0103_marketdesk_pdf_queue.sql` creates the queue and account clock;
`0104_marketdesk_pdf_account_backoff.sql` adds durable quota escalation and
PDF-only selection decisions. Discovery can only enqueue published reports.
Each archive cycle seeds missing published reports from the full ledger,
including articles outside the latest-25 listing. The claim rechecks publication
and matching MarketDesk provenance. Pending, held, dropped, failed, deleted or
mismatched sources cannot authorize a new download. Existing queue rows and
saved assets remain intact.

The operator selected **stricter portfolio/watchlist and relevance filters for
PDFs** on 2026-10-27. This does not narrow newsfeed publication. A deterministic
PDF-only policy reads the current cloud watchlist, latest IB portfolio snapshot
and approved research triage rules. Missing/invalid book context fails closed;
local stale JSON and model guesses cannot admit downloads. It excludes FX pair
notes, routine calendars, denied publishers/series/types, off-book security
reports and unmeasured commentary. Book/watchlist names, established preferred
desks, and measured market research on flows, positioning, options, volatility,
liquidity, breadth, aggregate earnings, rates, credit and chip/AI investment remain in
scope. Ambiguous relevance is excluded with an explicit reason rather than
spending account allowance. No PDF or extra model call is needed for selection.

At most 100 stale/new decisions are refreshed per timer cycle, using published
source titles and feed text. The context hash changes with book/rules/policy;
post edits also invalidate selection. Claims require a current eligible
selection, so old decisions cannot leak through during a refresh. Rejected
rows do not block later batches and can become eligible after a book change.
The queue, selection table and account clock expose filtered versus deferred
backlog separately.

One cycle admits at most one PDF. The global minimum interval is 1,800 seconds
(30 minutes). `MARKETDESK_PDF_MIN_INTERVAL_SECONDS` can increase it; existing
values below 1,800 are clamped in both the worker and the store. Admission is
committed before provider access, so restart/deployment cannot cause a catch-up
burst. A 600-second token lease recovers a killed worker and protects against
stale acknowledgements. The successful lease token is recorded separately so
a stale owner cannot claim another worker's acknowledgement. This is conservative local pacing, not a documented
MarketDesk rate guarantee; the same account's interactive use also consumes
its provider allowance.

The archive uses the existing authenticated `/files/<id>/blob` operation,
which the provider routes through its preview file server. Its historical
success does not override the current provider allowance. No working independent
download endpoint has been verified; a `downloads: true` account flag does not
make a blocked preview hop work. The archive never guesses file-server URLs or
bypasses a provider refusal. Before requesting bytes, the worker checks the
provider's account allowance. A spent allowance causes no PDF request.
Allowance errors and HTTP 429 defer the entire account for 6 hours, then 12,
then 24 hours, capped at 24 across all subsequent refusals. The streak is
shared across reports and survives restarts. Only a verified fresh provider
success resets it; recovering a saved receipt does not. Authentication failures
back off one hour, other failures at least 15 minutes, with the 30-minute
admission floor still enforced. Item/account retry clocks commit atomically.
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
before this policy is still reconciled locally; later held or dropped notes
retain already-stored PDF outbox assets. They do not authorize new downloads.

## Health and verification

`marketdesk-pdf` uses a continuous 15-minute freshness window, maps to
`radon-marketdesk-pdf.service`, and reports error during provider backoff.
A normal admission wait with no provider failure is healthy. This row is
separate from `marketdesk-research` and `marketdesk-dropbox`; a healthy article
cycle cannot conceal a blocked archive.

Focused contracts are in `scripts/tests/test_marketdesk_pdf_store.py`,
`scripts/tests/test_marketdesk_pdf_job.py`, and
`cloud/tests/test_marketdesk_pdf_runtime.py`. The parent MarketDesk release owns
the complete test gates and production backlog verification. Selection contracts
are in `scripts/tests/test_marketdesk_pdf_policy.py`.

Rollback: stop and disable `radon-marketdesk-pdf.timer` through the managed
service controls before reverting to a runtime that does not recognize its
unit. The additive queue migration and private assets can remain; they never
alter published article text or require deletion.
