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

### Requested backlog priority

Migration `0105_marketdesk_pdf_priority.sql` adds a durable binary queue priority.
`marketdesk_pdf_store.prioritize(article_ids)` atomically replaces the requested
cohort (at most 200 existing IDs; duplicates are ignored). Invoke this through a
managed application container as the application UID, with the exact frozen IDs.
An empty list clears the preference. The change never rewrites item retry times,
account clocks, attempts, leases, source identity or published copy.

While any priority row is unfinished, currently published with matching source,
and selected by current PDF policy, ordinary rows cannot claim an account slot.
A future retry or active lease reserves precedence without bypassing its deadline.
Claim-time SQL rechecks priority, including a concurrent operator update. Within
the cohort, the existing retry/publication/ID ordering remains. Finished or
currently excluded/withdrawn priority rows do not block other permitted work;
selection refresh processes stale priority rows first. Preference persists across
restarts; completed membership is inert and needs no operator cleanup.

A report that fails on its own (any non-quota code, such as a provider
`http_500` for one file) doubles its item retry delay per attempt, capped at one
day and never earlier than the account clock. After `PRIORITY_MAX_FAILURES` (3)
attempts ending in such a failure, a priority row stops reserving precedence, so
one bad file cannot starve the backlog. It keeps its place when due.

One timer fire admits one batch, then waits out the persisted account interval.
Migration 0112 stores that interval and the batch size on `marketdesk_pdf_throttle`,
so a restart and a second worker share one rate. The floor is 300 seconds and
the batch is 6: 288 fires/day times 6 is 1,728 PDFs/day. Clearing 1,300 backlog
rows while 200/day arrive is 1,700 PDFs, about 20 hours at that pace. A missed
fire does not stack into a catch-up burst. `MARKETDESK_PDF_MIN_INTERVAL_SECONDS`
is ignored; the row is the rate.

A provider quota signal (`preview_quota`, `download_quota`, `http_429`) doubles
the persisted interval. `Retry-After` lengthens that one wait and does not
become the steady pace. The ceiling is 24 hours, the old embargo cap. A single
HTTP 500 spends the current window and backs that file off on its own clock.
Three transport or 5xx errors in a row multiply the account interval by 1.5.
A clean cycle subtracts 30 seconds, never below the floor, and never moves the
next admission earlier. ea66c3842 metered PDF bytes on the signed-in account
and picked a fixed 1,800 second slot after `preview_quota`. It does not publish
a numeric allowance, so the controller is not capped under an invented quota.
`preview_quota` is the signal that slows it.

A 600-second token lease recovers a killed worker. The admit trigger moves the
account clock only when a lease changes hands. A lease that matches no row
leaves the clock alone. Policy rejects are `disposition=filtered` and can
return if the book changes. Eight non-quota failures, including rows already
at 100 attempts, become `disposition=exhausted` and drop out of the backlog.
Quota errors refund the attempt. Fresh reports (published within 36 hours)
and the oldest backlog alternate windows, so today's file is in the next
fresh window while the backlog still drains.

The archive uses the existing authenticated `/files/<id>/blob` operation,
which the provider routes through its preview file server. Its historical
success does not override the current provider allowance. No working independent
download endpoint has been verified; a `downloads: true` account flag does not
make a blocked preview hop work. The archive never guesses file-server URLs or
bypasses a provider refusal. Before requesting bytes, the worker checks the
provider's account allowance. A spent allowance causes no PDF request.
Allowance errors and HTTP 429 double the shared interval, up to 24 hours, and
honor `Retry-After` as the minimum for that one wait. The streak survives
restarts. A verified provider success steps the interval down by 30 seconds;
a saved receipt does not. Authentication failures wait at least one hour.
Other item failures use that file's own exponential clock, capped at one day,
and do not move the account interval unless they are the third transport or
5xx error in a row. Item and account updates commit atomically.
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
It also reports error when the eligible active backlog is older than 24 hours
or its depth is growing past 48 rows. The message is
`PDF archive backlog: pending=N oldest_h=H growing=0|1`.
`marketdesk-dropbox` reports the same breach on its own health row.
A normal admission wait with a young, stable queue is healthy. A persisted
`preview_quota` or `download_quota` deferral keeps that error row, prints the
deferral, and exits 0 so the 5-minute timer does not page P1 on every fire.
A persisted provider `http_5xx` deferral does the same: the error row and the
900-second retry stay, and the oneshot exits 0. A Turso wire failure records
that error row and exits 0 for the same reason.
The next timer retries the queue. Statement errors, `http_404`, and other
non-5xx download failures still exit 1. This row is separate from
`marketdesk-research` and `marketdesk-dropbox`; a healthy article cycle cannot
conceal a blocked archive.

Focused contracts are in `scripts/tests/test_marketdesk_pdf_store.py`,
`scripts/tests/test_marketdesk_pdf_job.py`, and
`cloud/tests/test_marketdesk_pdf_runtime.py`. The parent MarketDesk release owns
the complete test gates and production backlog verification. Selection contracts
are in `scripts/tests/test_marketdesk_pdf_policy.py`.

Rollback: stop and disable `radon-marketdesk-pdf.timer` through the managed
service controls before reverting to a runtime that does not recognize its
unit. The additive queue migration and private assets can remain; they never
alter published article text or require deletion.
