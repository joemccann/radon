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
source titles and feed text. Exhausted rows, including `aged_out`, are not in
that refresh. The context hash changes with book/rules/policy;
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
the cohort, `PDF_PREFERRED_DESK` and `PDF_BOOK_OR_WATCHLIST` outrank
`PDF_MEASURED_MARKET_RESEARCH`, then the newest `published_at`, then article id.
Finished or currently excluded/withdrawn priority rows do not block other permitted work;
selection refresh processes stale priority rows first. Preference persists across
restarts; completed membership is inert and needs no operator cleanup.

A report that fails on its own (any non-quota code, such as a provider
`http_500` for one file) doubles its item retry delay per attempt, capped at one
day and never earlier than the account clock. After `PRIORITY_MAX_FAILURES` (3)
attempts ending in such a failure, a priority row stops reserving precedence, so
one bad file cannot starve the backlog. It keeps its place when due.

One provider 500 is terminal instead. When `/files/<id>/blob` answers 500 with
the JSON message `Cannot read properties of null (reading 'path')`, marketdesk.ai
has no stored bytes for that file. It repeats on every retry (`IQSa8ONnKqF`
failed 101 times from 2026-10-27 to 2026-11-14, and each daily retry put the
service row in error). The session maps that exact body to `file_missing`; the
store marks the row `disposition=exhausted`, `disposition_reason=file_missing`
at once, without touching the account clock, soft streak or `last_error`; the
job logs `<id> retired: file_missing` and the cycle stays ok. Any other 500
body is still the transient `http_500` above.

One timer fire admits one batch, then waits out the persisted account interval.
Migration 0113 is the safety reset: the live row starts at 900 seconds and batch
1, then ramps. It does not open at 300 seconds and batch 6. A clean cycle
subtracts 30 seconds, down to 300 seconds or to `MARKETDESK_PDF_MIN_INTERVAL_SECONDS`
when that is higher. The batch grows by one only after 8 clean cycles, up to 6.
`MARKETDESK_PDF_DAILY_CAP` is a rolling 24 hour admission cap (unset means 400,
0 turns it off). `MARKETDESK_PDF_HOURLY_CAP` is a rolling 1 hour admission cap
(unset means 12, 0 turns it off). `MARKETDESK_PDF_MAX_AGE_DAYS` is how long a
passing report stays downloadable (unset means 7, 0 turns it off). Age is
`published_at`, the report date copied from the article ledger. `first_seen_at`
is when the queue noticed the row and lags on backfill, so it is not the age.
A passing pending row older than the window becomes `disposition=exhausted`
with `disposition_reason=aged_out`. The 0112 check list cannot grow in an
additive migration, so this reuses `exhausted` and is distinct by reason.
Filtered rows, downloads, in-flight and expired leases, and other exhausted
reasons (`file_missing`, `attempts_exhausted`) are not rewritten. Seed does
not insert a second row, and a book change revives only `filtered` rows.
Within a priority tier, pending rows are admitted newest `published_at` first,
so a same-day report takes the cap ahead of a 6-day-old one. The lease SQL
enforces both caps. The hourly
default is a hypothesis from the 2026-11-15 pace that drew `preview_quota`
near 18 admissions in an hour, not a published provider allowance. A missed
fire does not stack into a catch-up burst.

A provider quota signal (`preview_quota`, `download_quota`, `http_429`,
`http_401`, `http_402`, `http_403`, or an HTTP-date `Retry-After`) doubles
the persisted interval, stops the rest of the batch, and resets the batch to 1.
It refunds the attempt and sets that row's next attempt to the account
deadline plus one interval (at least 300 seconds), so the file that drew the
quota is not the next admission. A delta-seconds `Retry-After` lengthens that
one wait and does not become the steady pace. The ceiling is 24 hours, the
old embargo cap. After the signal, clean cycles still subtract 30 seconds,
and every 4 clean cycles the interval also halves toward the floor. A 2100
second clock is back at the floor in under 6 hours. The 900 second ramp stays
additive until a quota signal, or an interval already above 900 seconds, arms this recovery. A later success that clears `last_error` does not cancel it. A single HTTP 500 spends
the current window and backs that file off on its own clock.
Three transport or 5xx errors in a row multiply the account interval by 1.5.
A clean cycle subtracts 30 seconds, never below the floor. When that leaves
the interval shorter than the wait the admit trigger just parked, the next
admission moves forward to the shorter interval. The daily and hourly caps
still bound the burst. ea66c3842 metered PDF bytes on the signed-in account
and picked a fixed 1,800 second slot after `preview_quota`. It does not publish
a numeric allowance. The hourly cap is a separate configurable hypothesis.
The controller does not return to that fixed 1,800 second slot.
`preview_quota` is the signal that slows it.

A 600-second token lease recovers a killed worker. The admit trigger moves the
account clock only when a lease changes hands. A lease that matches no row
leaves the clock alone. Policy rejects are `disposition=filtered` and can
return if the book changes. Eight non-quota failures after `attempts_baseline` become
`disposition=exhausted` and drop out of the backlog. Migration 0113 sets that
baseline to the attempt count already on the row and restores any row already
marked exhausted, so a pre-existing attempts=100 row is not dead on arrival.
Quota errors refund the attempt. Claim order is an expired download lease,
then operator priority, then preferred-desk and book-or-watchlist ahead of
ordinary measured research, then newest `published_at`. A download whose lease
has already expired is still reclaimed on the next claim. Ordinary measured
research older than the window is retired. A preferred-desk or book row older
than the window stays while any ordinary passing row inside the window is
still pending, and it is claimed ahead of that row. When a clean cycle
shortens the interval, the next admission moves
forward to that shorter interval; the hourly cap still bounds the burst.

The archive uses the existing authenticated `/files/<id>/blob` operation,
which the provider routes through its preview file server. Its historical
success does not override the current provider allowance. No working independent
download endpoint has been verified; a `downloads: true` account flag does not
make a blocked preview hop work. The archive never guesses file-server URLs or
bypasses a provider refusal. Before requesting bytes, the worker checks the
provider's account allowance. A spent allowance causes no PDF request.
Allowance errors and HTTP 429 double the shared interval, up to 24 hours, and
honor `Retry-After` as the minimum for that one wait. The streak survives
restarts. A verified provider success steps the interval down by 30 seconds,
and by the post-quota halving when recovery is armed. A saved receipt does not.
Authentication failures wait at least one hour.
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
It flags the eligible active backlog when its oldest `published_at` is older
than 24 hours or its depth is growing past 48 rows. `oldest_h` is that
publication age, not `first_seen_at` (a backfill can first see an old report
within the hour). The message is
`PDF archive backlog: pending=N oldest_h=H growing=0|1 stalled=0|1`.
A backlog that is still being admitted reports `warn`, which never pages
(2026-11-17: 588 rows admitted oldest-first every ~48 min held the row red).
It reports `error` and pages when the depth is growing, no row was admitted in
the last 6 hours (`stalled=1`), or the oldest row is older than 14 days.
Only `marketdesk-pdf` reports this breach. `marketdesk-dropbox` reflects the Dropbox
outbox alone (2026-11-16): one condition on two rows read as a Dropbox fault, and the
second caller overwrote `observed_pending` minutes apart, so `growing` compared two
writers' samples. A stalled archive timer still surfaces as a stale `marketdesk-pdf` row.

Measured allowance (2026-11-16): both `preview_quota` trips on 2026-11-15
(12:12 and 20:52 UTC) came after 73 and 74 downloads in the preceding 24 hours,
including an 18-download hour that did not trip. Treat the provider limit as
about 74 previews per rolling 24 hours. Eligible weekday intake is 70 to 100.
`MARKETDESK_PDF_DAILY_CAP=70` keeps admissions under the allowance, so a quota
trip no longer doubles the interval. Passing rows older than
`MARKETDESK_PDF_MAX_AGE_DAYS` (default 7) leave the active backlog when they
are ordinary measured research. Preferred-desk and book rows are admitted
first and are not aged out while an ordinary row inside the window is still
pending. With arrivals spread through the session, the hourly cap of 12 and
the rolling 70 spend slots as rows arrive. In the interleaved sim that is 7
protected rows and 63 of 78 ordinary rows on a steady day. The ordinary rows
that do not fit age out. They do not block the next protected report.
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
