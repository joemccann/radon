# Flex scheduled delivery: architecture decision

Status: proposed, research complete, no code written.
Date: 2026-08-17.
Scope: replace the IBKR Flex Web Service polling path for `IB_FLEX_NAV_QUERY_ID=1442520`.

---

## UPDATE 2026-08-17, after this research landed — sequencing changed

The research below is sound and its conclusion stands, but a finding it surfaced
turned out to matter more than the recommendation itself, and it changes what to
do FIRST.

**The 10-day outage was largely self-inflicted, not an IBKR rate limit.**
`_FLEX_THROTTLE_CODES` classified 1001, 1018 AND 1019 as throttles, and each
escalated the local 24h/48h/72h/168h ladder. But per IBKR's own table only 1018
is a rate limit; 1019 is the ordinary "still generating, poll again" response and
1001 is a transient generation failure. So a statement seconds from ready bought
a 24-hour backoff, and a transient failure walked a ladder toward a week.

IBKR also publishes **no** daily or multi-day cooldown. The documented limit is
10 requests per MINUTE, per token, and it clears in a minute. The ladder models a
constraint far harsher than the real one.

Fixed in commit 436dcdc1, with the old contract re-pinned to the correct
classification rather than deleted.

**What this means for this document.** The routine path may simply work now that
Radon has stopped embargoing itself, so:

1. Confirm the Web Service works post-fix before building any delivery pipeline.
   The cheap discriminator that costs nothing against the token is pressing
   **Run** on query 1442520 in the Portal — that is a render, not a
   `/SendRequest`. If it generates there but still 1001s via the API, that is an
   IBKR-side issue for a support ticket, not more code.
2. Then adopt the architecture below as the durable path. It is still the better
   design — the argument for it never depended on the throttle being real, only
   on delivery being a mechanism that cannot be rate limited at all.
3. Note the ladder itself is now over-conservative by roughly three orders of
   magnitude and is worth revisiting on its own.

---

## A. The answer

**Yes. Scheduled delivery escapes the Flex Web Service rate limit.** The published limit is
"1 request per second, a maximum of 10 requests per minute", and error 1018 says it verbatim:
"Too many requests have been made from this token." The limit is scoped to the `/SendRequest`
*endpoint* and to the *token*. Scheduled delivery uses neither. IBKR's own Flex Web Service
introduction, Usage Note 5, points at delivery as the alternative: "the same Flex Query reports
can also be scheduled for delivery via email or FTP." The docs index frames them as either/or.
IBKR never publishes an explicit "delivery is not rate limited" sentence, so this is inference
from where the limit is scoped, not a quoted negative. It is a strong inference.

**Recommended architecture: Portal-scheduled email to a machine-owned address, Cloudflare Email
Worker to R2, VPS pulls from R2 and ingests from file.** Zero Flex Web Service requests on the
routine path.

**Email vs FTP: use email. Ask for sFTP anyway, but do not design around it.** sFTP is the
better transport in the abstract (customer-pull, so a missing file fails loudly), but it is
"available by request only", requires an RSA key exchange plus a mandatory PGP key, takes 1 to 2
business days through `filedelivery@interactivebrokers.com`, and **no IBKR page confirms it is
available to a standard individual (non-advisor, non-institutional) account.** The FTP settings
page exists only under the Advisor Portal; the Client Portal equivalent 404s. Do not architect on
a capability that may not exist for this account. The Cloudflare Worker design below recovers the
one property sFTP has and email lacks (pull semantics, loud absence) by making R2 the pull point.

**Also, and this is important: the 1001 you are seeing is probably not the rate limit at all.**
In IBKR's taxonomy 1018 is the throttle code and 1009 is the load code. 1001 is
"Statement could not be generated at this time", a statement *generation* failure for that query
instance. The documented throttle clears within a minute; a 0.85 second 1001 repeating for 10+
days is not that. **Before any of this work starts, open the Portal and press Run on query
1442520 in the UI.** That is a Portal render, not a `/SendRequest` call, so it costs nothing
against the token, and it discriminates "the token is throttled" from "this query cannot
generate." If the query itself is broken, scheduled delivery will silently deliver nothing and
this entire migration will look like it succeeded while producing no data.

---

## B. The recommended architecture

### Who does what

```
  IBKR reporting backend
    query 1442520 "Equity Summary in Base"
    sections: NAV in Base | Cash Transactions | Transfers
    Period: pinned explicit value (see W1)
    Format: XML
    Delivery: Email, enabled per query
         |
         |  ~19:00-20:00 ET weekdays (IBKR's published file-delivery window;
         |  treat the clock as indicative until observed)
         v
  flex@stmt.radon.run                        <- machine-owned, no human mailbox
    Cloudflare Email Routing (MX on stmt.radon.run)
         |
         v
  Cloudflare Email Worker
    1. buffer message.raw ONCE (it is a single-read stream)
    2. postal-mime -> find the XML attachment
    3. sha256(bytes) -> key
    4. R2 PUT  flex/1442520/<yyyy-mm-dd>/<sha256>.xml
    5. explicitly consume the message (a Worker that neither
       forwards, rejects, nor consumes SILENTLY DROPS the mail)
         |
         v
  R2 bucket radon-flex-inbox                 <- content-addressed, durable
         |
         |  HTTPS pull, S3-compatible, outbound only
         v
  Hetzner: radon-flex-ingest.timer  21:15 ET Mon-Fri
    scripts/flex_delivery_ingest.py
      list R2 objects newer than last accepted
      download -> /var/lib/radon/flex-inbox/<sha>.xml  (0700 dir, 0600 file)
      gate: parse / shape / monotonicity  (reject BEFORE any write)
      dedup: flex_deliveries.content_sha256
      then, from that one file, no network:
        perf_twr_builder.build_and_persist(document=FlexDocument(...))
          -> data/performance.json
          -> Turso performance_snapshots, nav_snapshots,
             external_flows, twr_subperiods
        cash_flow_sync --from-file <path>
          -> Turso cash_flows
      heartbeat service_health: flex-delivery + performance
      delete the file; keep at most 3 newest for replay
```

### Flex Web Service requests per day

| Path | Requests/day |
|---|---|
| Routine NAV + flows + cash ingest | **0** |
| Weekly reconciliation (section C) | 0 on six days, 1 on one day |
| **Total routine** | **0** |

Today, for comparison: `radon-perf-twr.timer` fires Mon to Fri at 20:45 ET and makes at least one
unguarded `SendRequest` per run, and the monitor daemon's cash-flow handler makes another at
17:00 ET. Two per weekday minimum, more with retries, and each one made during an embargo pushes
the reset further out.

### Why R2 and not IMAP on Gmail

IMAP polling a Gmail mailbox is simpler and needs no Worker. It is rejected as the primary because:

1. **The operator's Gmail is MCP-connected.** A daily plaintext statement in that mailbox is a
   permanent, searchable archive of account number, full NAV curve, and every cash movement,
   reachable by anything holding OAuth to that account. This is the single worst security outcome
   available in this design space.
2. Google app passwords are a deprecating, rotating credential with no scoping.
3. A mailbox has no content-addressing, so dedup and replay have to be reinvented.

R2 gives content-addressed storage for free (the key *is* the dedup identity), keeps the statement
out of any human mailbox, and is pulled outbound over HTTPS with the same S3-compatible client
pattern the portfolio cold-archive already uses against Backblaze.

**Escape hatch, if the operator will not own a Worker:** deliver to a dedicated Gmail address that
is *not* the primary and *not* MCP-connected, poll it with `imaplib` from the same ingest job
behind the same fetcher interface, and delete the message on successful ingest. Everything
downstream of the fetcher is identical. Build the fetcher as one interface with two
implementations so this is a config flip, not a rewrite.

---

## C. Keep the Web Service, but only as an *exercised* path

**Decision: keep it, demoted to a weekly reconciliation that actually runs. Do not keep it as a
dormant fallback.**

The argument against keeping it is real and this repo has been bitten by exactly it: a code path
with no caller rots and nothing notices (`feedback_scan_endpoint_without_a_caller` was precisely a
scan endpoint with a mirror and a service_health row and no timer, stale forever, paging nobody).
A `--fallback` flag nobody exercises for four months is not a fallback; it is untested code that
will fail on the night you need it.

The argument against deleting it is also real: scheduled delivery is a *push*. When a push does
not arrive there is nothing to retry against, and the recovery lane would be "the operator exports
by hand from the Portal", which is where we are today.

The resolution is to make the rare path a *scheduled* path. One run per week, Sunday, breaker-gated
through the existing `_throttle_backoff` ladder:

- It is exercised every week, so it cannot silently rot.
- It reconciles: fetch the statement over the Web Service, compare the derived NAV series and flow
  set against what the delivered file produced. A divergence is a real signal that the delivery
  template drifted.
- One request per week will never re-enter the embargo. The ladder walks back down on
  `record_success`, and the token stays proven warm, which also keeps `flex-token-check` honest.
- When a delivery is missed, the same code path can be invoked on demand with a known-good breaker
  state instead of being a cold-start gamble.

Non-negotiable condition on keeping it: **every** `SendRequest` call site must go through one
breaker-gated helper first (see W10). There are currently six independent `SendRequest`
implementations in this repo and only the cash-flow handler consults the breaker.

---

## D. The work

Ordered. Do not start W2 before W0 answers.

### W0. Diagnose the 1001 in the Portal. NEEDS-DECISION (blocking)

**No code.** Client Portal, Performance & Reports, Flex Queries, open the Activity Flex Query
1442520, press **Run**. Verify in the Portal: exact menu labels vary by Portal version.

- If it renders: the query is fine, the failure is token-side, proceed with the full design.
- If it fails to generate: fix the query first (most likely suspects are the Period field and the
  Transfers section). Scheduled delivery of a query that cannot generate delivers nothing, and it
  fails **silently**, which is strictly worse than today.

**Test that proves it:** a rendered statement in the browser, and its row/section counts compared
against the hand export the operator already produced.
**Risk:** skipping this ships a migration that appears to succeed and produces no data.

### W1. Configure delivery in the Portal. NEEDS-DECISION

Clickpath (**verify in the Portal**; IBKR relabels these pages and the current docs and the live
UI disagree in places):

1. Client Portal, Performance & Reports, Statements, gear/Delivery Settings.
2. Set the delivery **method** to Email. Note: the method is **user/account-wide**, it applies to
   every report enabled for delivery, so this also changes any IB-defined statements already being
   delivered.
3. Performance & Reports, Flex Queries, Delivery Configuration panel for **Activity Flex Query**
   (Trade Confirmation Flex is a separate panel). Tick query 1442520.
4. Set the delivery address to the machine-owned address from W2, not a human mailbox.
5. Open query 1442520 and **pin the Period to an explicit value** (Last Business Day, or Last 365
   Calendar Days if the full curve is wanted every night). Delivery has **no** `fd`/`td`/`p`
   override; the saved Period governs entirely, and IBKR's Usage Note 6 warns that variable "Last
   N Days" durations behave unpredictably.
6. Confirm Format is XML.

**Frequency: there is no documented frequency selector.** Current IBKR docs for Client Portal,
Advisor Portal, Org Portal and Student Lab all show only method plus per-query checkbox. The
"Daily / Weekly / Monthly" dropdown that appears in search summaries traces to a dead legacy
Account Management mirror. **Verify in the Portal.** If no selector exists, cadence follows the
query Period and IBKR's delivery run, published as "Daily, Monday to Friday (weekend delivery
available upon request)" at "~7PM to 8PM ET".

**Test that proves it:** the first delivered email arrives, and `describe_statement_shape()` on its
attachment returns `flex_statement_count == 1`, the expected account id, and
`has_transfers_section == True`.
**Risk:** the account-wide method setting has side effects on other delivered reports. Note what
delivery is already configured before changing it.

### W2. Receiving endpoint: Cloudflare Email Routing + Worker + R2. UNAMBIGUOUS

- New subdomain `stmt.radon.run`, MX records per Cloudflare Email Routing, one routing rule
  `flex@stmt.radon.run` to the Worker.
- Worker: buffer `message.raw` exactly once, `postal-mime` to extract the XML attachment,
  `sha256` the bytes, R2 `PUT flex/<queryId>/<yyyy-mm-dd>/<sha256>.xml`, then **explicitly consume
  the message**. A Worker that does not forward, reject, or consume drops the mail with no error
  anywhere.
- Reject any message whose envelope sender is not IBKR's, before parsing.
- Do **not** key anything on the attachment filename. No IBKR source documents the filename
  convention for Flex email attachments (the AccountID/FileType/AsOfDate convention that IBKR
  publishes belongs to the reporting-integration feed program, not to Flex delivery). Parse
  defensively.
- This Worker is a **second deploy target outside this repo's CI gate**. Keep it in
  `cloud/workers/flex-inbox/` with its own `wrangler.jsonc` so it is at least version-controlled
  at the same SHA, and document that deploying it is a manual `wrangler deploy`.

**Test that proves it:** a unit test over the Worker with a saved raw MIME fixture built from a
real delivery, asserting the R2 key equals `sha256` of the attachment bytes; plus one live
end-to-end send.
**Risk:** attachment size limits are unpublished. A 365-day three-section Activity XML is the case
to watch. If it exceeds the limit, this becomes the argument that finally forces sFTP.

### W3. `flex_deliveries` table. UNAMBIGUOUS

New migration. **Check Turso `MAX(version)` and every in-flight worktree before picking a number**
(highest on disk here is `0049_vixcor.sql`; parallel worktrees have collided on this before).

```sql
CREATE TABLE IF NOT EXISTS flex_deliveries (
  content_sha256 TEXT PRIMARY KEY,
  query_id       TEXT NOT NULL,
  account_id     TEXT NOT NULL,
  period_from    TEXT NOT NULL,
  period_to      TEXT NOT NULL,
  when_generated TEXT NOT NULL,
  parsed_rows    INTEGER NOT NULL,
  received_at    TEXT NOT NULL,
  channel        TEXT NOT NULL,
  ingested_at    TEXT,
  status         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_flex_deliveries_period_to ON flex_deliveries(period_to);
```

Identity is `sha256(raw bytes)` plus the explicit named tuple
`(account_id, period_from, period_to, when_generated)`, all of which
`cash_flow_sync.describe_statement_shape()` already returns. **Hash the named tuple, never "every
field except X"** (`feedback_identity_hash_over_dict_shape`: a later-added field faked corruption
and wedged orders-sync).

**Test that proves it:** ingest the same fixture twice, assert one row, assert the second run exits
0 with status `duplicate` and writes nothing to `performance_snapshots`.
**Risk:** migration number collision, which presents as "nothing to apply" on a new migration.

### W4. `perf_twr_builder --from-file`. UNAMBIGUOUS

Two I/O chokepoints: `_fetch_nav_document()` (`scripts/perf_twr_builder.py:442`) and
`resolve_flows(document)` (`:519`). `resolve_flows` **already** reuses a passed document when
`document.query_id == _flows_query_id()`, and `IB_FLEX_FLOWS_QUERY_ID` is deliberately unset so
`_flows_query_id()` falls back to the NAV id. Threading an optional
`FlexDocument(query_id=<nav id>, xml=<file text>)` through `get_nav_snapshots()` and
`build_and_persist()` therefore resolves NAV **and** flows from one file with zero network. About
15 lines.

**The critical detail:** `get_nav_snapshots()` falls through `flex_live -> disk_cache -> turso`. A
file-driven run must **fail loudly**, not fall back. Without a strict mode, a bad file republishes
a stale curve tagged `nav_source="disk_cache"` and the page looks fine.

Add `--from-file PATH` to `main()` (`:1439`), setting strict mode implicitly.

**Test that proves it:** (a) golden test, run `--from-file` against the operator's real 329-row
export fixture, assert the payload matches the known-good live payload field for field; (b)
red test, `--from-file` a truncated statement, assert non-zero exit and assert
`data/performance.json` is unchanged.
**Risk:** silent fallback to `disk_cache`. Test (b) is the one that matters.

### W5. `cash_flow_sync` file path. UNAMBIGUOUS (mostly done)

`--from-file` already exists (`scripts/cash_flow_sync.py:595`), with `--dry-run`, `--since`,
`--json`, `--no-file`. `parse_cash_transactions()` (`:330`) is pure and parity-proven. The work is
only to have the ingest job call it in-process rather than shelling out, and to have it share the
single validation gate from W6 instead of re-deriving one.

Note the two parsers disagree in direction about the Transfers section, and both are correct:
`perf_twr._transfers_section_warnings` **errors** when Transfers is absent (an ACATS would read as
investment gain), while `cash_flow_sync.statement_shape_warnings` **warns** that it does not ingest
them. Section present plus the cash-flow warning is the correct steady state. Do not "fix" either.

**Test that proves it:** existing `scripts/tests/test_cash_flow_sync_cli.py` stays green, plus one
new test asserting the ingest job produces byte-identical row output to the CLI on the same file.
**Risk:** low.

### W6. Ingest job, unit, timer. UNAMBIGUOUS

`scripts/flex_delivery_ingest.py`, plus `cloud/services/radon-flex-ingest.{service,timer}`.

Model the unit on `radon-perf-twr.service`: `Type=oneshot`, `User=radon`,
`WorkingDirectory=/home/radon/radon`, `EnvironmentFile=/home/radon/radon-cloud/.env`,
`Environment=RADON_DB_NO_REPLICA=1`, `StartLimitIntervalSec=300`, `StartLimitBurst=5`. Add the
hardening in section E, which no existing unit uses and which this job needs more than any other.

Timer: `OnCalendar=Mon..Fri *-*-* 21:15:00 America/New_York`, `Persistent=true`,
`RandomizedDelaySec=300`. 21:15 sits after IBKR's ~19:00 to 20:00 ET delivery window and after the
20:45 the existing perf timer already encodes as the settle point.

Validation gate, reject **before any write**, using only functions that already exist:

1. `ET.fromstring` parses.
2. `flex_statement_count == 1` and `account_ids == [expected]`.
3. `has_transfers_section` is True.
4. `parse_nav_entries` yields at least 2 observations.
5. Monotonicity floor: `period_to >= ` last accepted `period_to`, and `parsed_rows >= ` last
   accepted minus a small tolerance. A statement that halves its cash rows is a query-config change,
   not a real day.

Fail closed: heartbeat `error`, leave the file in the inbox, exit non-zero.

**Per `cloud/CLAUDE.md`, a new unit owes a root `install -m 0644` AND a hash bump in
`cloud/config/installed-units.sha256` in the same commit**, or `tests/test_unit_install_acknowledgment.py`
fails CI. New env keys go in `cloud/config/required-env.txt` only if the deploy preflight should
hard-fail without them.

**Test that proves it:** table-driven test over five bad fixtures (unparseable, two statements,
wrong account, no Transfers, one NAV point) asserting each one exits non-zero and writes nothing;
plus `pytest cloud/tests` and `systemd-analyze verify` on the unit.
**Risk:** the timer fires whether or not a file arrived. That is deliberate. It is what turns
absence into an event.

### W7. service_health. UNAMBIGUOUS, and it is broken today

Three separate defects, all verified in this repo:

1. **`perf_twr_builder` never writes a `service_health` row.** `persist_payload` (`:1315`) calls
   `upsert_performance_snapshot` **directly**, bypassing `db.scan_mirror.mirror_scan_snapshot()`,
   which is the function that owns both the snapshot upsert and the `record_service_health`
   heartbeat. `scan_mirror.SNAPSHOT_UPSERTS:45` maps `"performance"` to
   `upsert_performance_snapshot`, and nothing calls it. **Fix: route `persist_payload` through
   `mirror_scan_snapshot("performance", payload)`.** One line, and it is the chokepoint every other
   scan already uses.
2. **The declared `performance` window is wrong for a scheduled build.**
   `web/lib/serviceHealthWindows.ts:373` declares
   `{ open: 30 * MIN, extended: 30 * MIN, closed: 3 * DAY, category: "on-demand" }`, with a comment
   describing it as a user-triggered scan endpoint. It is not; it is a nightly timer. Turning on the
   heartbeat from (1) without fixing this pages every 30 minutes. **Change to
   `{ open: 26 * HOUR, extended: 26 * HOUR, closed: 4 * DAY, category: "scheduled", requires_ib: false }`**,
   matching `cash-flow-sync` and `execution-sweep`, both weekday-only and both widened to 4 days
   precisely because 25h tripped every Saturday.
3. **`"performance"` is in no watchdog bucket** and not in
   `scripts/watchdog/services.py:SCHEDULED_SERVICES`.

Then add a **separate** `flex-delivery` key heartbeated by the ingest job on every run, because
"the mailbox and R2 are reachable" and "a fresh statement exists" are different failures with
different fixes. Register **both** keys in `scripts/watchdog/services.py` **and**
`web/lib/serviceHealthWindows.ts` (contract tests pin them against each other; editing one alone
fails CI), and add both to the watchdog **daily** bucket alongside `cash-flow-sync`,
`execution-sweep` and `flex-token-check`.

Heartbeat rules, all previously learned the hard way:
- Heartbeat on **every** cycle including no-ops, or the error row latches
  (`feedback_service_health_heartbeat`).
- Heartbeat on the **failure** path too, or a wedged poller looks identical to a quiet one
  (`feedback_polling_marker_must_match_failure`).
- Do **not** latch `last_run` on a soft failure (`feedback_dont_latch_last_run_on_soft_failure`).
  An unreachable R2 must stay due.

**Test that proves it:** `scripts/tests/test_service_registration_completeness.py` and the
TS/Python window-parity test both green with the two new keys; plus a test asserting
`build_and_persist()` writes a `service_health` row with `status="ok"`.
**Risk:** enabling the heartbeat before fixing the window turns on a 30-minute pager.

### W8. Missing-delivery alarm. UNAMBIGUOUS

A push has no natural "it is late" event, so the alarm must be time-driven. The W6 timer fires
whether or not a file arrived. On each firing:

- No object in R2 newer than the last accepted `period_to`, **and** `sessions_behind() > 1`
  (reuse `perf_twr_builder.sessions_behind()` `:211` and `last_completed_session()` `:196`, do not
  write a second staleness rule): heartbeat `flex-delivery` **error**, do not latch `last_run`.
- Nothing new but `sessions_behind() <= 1`: heartbeat **ok**. Weekends and holidays are legitimately
  quiet, which is what the 4-day closed window encodes.

**Test that proves it:** window-relative fixtures (today minus N, never hardcoded dates,
`feedback_window_relative_test_dates`), asserting error on a 3-session gap and ok on a Saturday.
**Risk:** this is the single highest-value item. Switching from pull to push **removes the only
failure signal that exists today**, which is a unit that exits non-zero. Do not ship W1 to W6 to
production without W7 and W8.

### W9. Filesystem and repo hygiene. UNAMBIGUOUS

- **`.gitignore` has no `*.xml` rule anywhere.** The repo is PUBLIC. A statement that lands under
  `data/` is one `git add` from a permanent leak. Add explicit ignores, and more importantly never
  write a statement inside the checkout at all.
- Inbox at `/var/lib/radon/flex-inbox/`, directory `0700 radon:radon`, files `0600`, written
  atomically `tmp` then `rename` (the pattern `persist_payload` already uses).

**Test that proves it:** a test asserting the ingest job's write path resolves outside the repo
root, and `gitleaks detect --source . --config cloud/.gitleaks.toml` green.
**Risk:** an `*.xml` allowlist entry in gitleaks would be SHA-keyed and breaks on rebase
(`feedback_gitleaks_allowlist_sha_keyed_breaks_on_rebase`). Do not create a case that needs one.

### W10. Demote and chokepoint the Web Service. NEEDS-DECISION

There are currently **six independent `SendRequest` implementations**: `scripts/cash_flow_sync.py:104`,
`scripts/perf_twr_builder.py:114`, `scripts/portfolio_performance.py:379` and `:495`,
`scripts/trade_blotter/flex_query.py:83`, `scripts/trade_blotter/blotter_service.py:184`. Only the
cash-flow handler consults `scripts/monitor_daemon/handlers/_throttle_backoff.py`. **`perf_twr_builder`
fires Mon to Fri at 20:45 ET and makes an entirely unguarded request every night**, embargo or not.
That is very likely a material contributor to the current state.

Work: one breaker-gated `flex_request()` helper that every call site routes through, then
`radon-perf-twr.timer` changed from nightly to Sunday-only reconciliation, then the Web Service
call removed from the ingest path entirely.

**Needs a decision** because collapsing six call sites is larger than the rest of this plan
combined, and the blotter and `portfolio_performance` paths have their own callers and cadences
that were not audited here.

**Test that proves it:** a test asserting every module-level Flex URL constant is unreachable
except through the gated helper (grep-style structural test), plus a test asserting the helper
refuses when `is_blocked()`.
**Risk:** leaving any ungated call site means the breaker is decorative.

---

## E. Security requirements

These are requirements, not suggestions. The artifact is the account number, the full NAV history,
every cash movement, and positions. It is the most sensitive thing Radon touches, in a public repo.

1. **The statement MUST NOT be delivered to a human mailbox, and MUST NOT be delivered to any
   MCP-connected mailbox.** Delivery address is machine-owned (`flex@stmt.radon.run`).
2. **The statement MUST NOT be written anywhere inside the git checkout.** Inbox is
   `/var/lib/radon/flex-inbox/`, directory `0700`, files `0600`, atomic `tmp` then `rename`. Never
   into `data/`, never into `logs/`.
3. **Request PGP encryption from IBKR** (`filedelivery@interactivebrokers.com`, same email as the
   sFTP request). Encryption is by-request-only and applies to both email and sFTP. It is off by
   default, so plaintext XML transits SMTP unless requested. This is the single highest-leverage
   mitigation and it makes email and sFTP converge in risk. Preferred handling: fetch `.xml.gpg`,
   decrypt in memory, parse, persist only the derived rows. The retained replay copy stays `.gpg`.
4. **Delete on successful ingest.** Retain at most the 3 newest for replay, pruned by mtime
   **inside the same job**. A separate prune timer can go stale.
5. **Never log the body, and redact `account_ids` before logging the shape dict.**
   `describe_statement_shape()` returns `account_ids`; printing it puts the account number in
   journald, and `data/journal_archive/` rsyncs journald snapshots to the laptop.
6. **The unit MUST carry systemd hardening:** `StateDirectory=radon/flex-inbox`,
   `StateDirectoryMode=0700`, `UMask=0077`, `PrivateTmp=true`, `ProtectHome=read-only`,
   `NoNewPrivileges=true`, `ProtectSystem=strict`. No existing unit uses these. This one should be
   the first.
7. **All credentials in `/home/radon/radon-cloud/.env`, mode `0600`, via `EnvironmentFile=`.**
   Never `~/.zshrc`, never inside the checkout, never `web/.env`. **Single-quote any value
   containing `$`** or systemd and bash sourcing expand it and the unit fails silently under
   `set -u` (`feedback_env_file_shell_expansion`). Run new keys through `cloud/scripts/check-env.py`.
8. **Radon MUST NOT open an inbound port for this.** Hosting an inbound SFTP or an inbound webhook
   puts new authentication surface on the host that holds `TWS_USERID`, `TWS_PASSWORD` and the Turso
   token in one file. `cloud/scripts/setup-vps.sh` opens only 80 and 443 today. That asymmetry is
   why the two inbound designs are rejected outright, not weighed.
9. **If sFTP is granted: pin IBKR's host key** in a dedicated `known_hosts` with
   `StrictHostKeyChecking=yes`, key at `/home/radon/.ssh/ibkr_sftp` mode `0600`. A host-key change
   must fail loudly rather than download.
10. **Only the PGP public key may ever be committed.** The private key lives on the host at `0600`.

Attacker payoff if this leaks: account number, NAV curve, every deposit and withdrawal. That is a
complete financial profile and a credible spear-phishing and SIM-swap package. It is not directly a
trading capability, which is exactly why item 8 matters most.

---

## F. What this does not fix

- **It does not make the data fresher than IBKR generates it.** Delivery runs in the same
  ~19:00 to 20:00 ET window and reflects the same T+1 settlement. The known ~1 day cash-transaction
  settlement lag stays (`feedback_flex_cash_transaction_lag`).
- **It does nothing intraday.** Flex is an end-of-day reporting product in every delivery mode. A
  live NAV during the session still requires the TWS API or the Client Portal Gateway, and neither
  gives a historical NAV series with classified external flows.
- **It does not retroactively clear the current embargo.** The local ladder is Radon policy; IBKR
  publishes no cooldown duration at all, only "try again shortly". The only thing that shortens the
  current state is not making more requests.
- **It does not fix the query if the query is broken.** See W0. If 1001 is a generation failure,
  delivery delivers nothing, silently.
- **It does not remove the manual Portal export as the true last resort.** It makes it rarer.
- **It does not cover trades.** `IB_FLEX_QUERY_ID=1422766` is a separate blotter query with separate
  call sites, untouched by this design. If it is also being polled, it is also spending the same
  token.
- **It does not make the Gmail MCP an automation path.** MCP tools are bound to an interactive
  Claude session; there is no route from a `radon-*.timer` to `mcp__claude_ai_Gmail__*`. The MCP is
  useful as the *operator recovery lane* (find the attachment, run `--from-file`) and nothing more.
- **A human forwarding the email is not automation.** It inherits today's silent-failure mode and
  adds a person to it. The only version of this that automates is IBKR delivering directly to a
  machine-owned destination.

---

## G. Migration path

**Phase 0, today, no code.**
1. Run W0. Press Run on query 1442520 in the Portal. Everything else waits on the answer.
2. **Stop spending requests.** `radon-perf-twr.timer` is the unguarded nightly consumer; stop and
   disable it (`radon unit stop radon-perf-twr.timer`, not a piecemeal `systemctl`, and never
   `radon restart`, which cycles the Gateway and triggers 2FA). The monitor daemon's cash-flow
   handler is already breaker-gated. Confirm with `journalctl` that nothing else fired a
   `SendRequest` in the last 24h.
3. Send one email to `filedelivery@interactivebrokers.com` with the account ID requesting **both**
   sFTP delivery **and** PGP encryption for Statements/Flex Queries, and asking explicitly whether
   sFTP is available on this account type. Both are by-request-only, both change the design, and a
   rep responds in 1 to 2 business days. Sending it now means the answer arrives while W2 to W9 are
   being built.
4. Keep serving `/performance` from the hand-built payload. It is correct.

**Phase 1, the throttled token.** Do not probe it. Every request while embargoed extends it, and
IBKR publishes no duration, so there is no schedule to wait out, only a policy to stop violating.
Let the local ladder expire on its own. `flex-token-check` is expiry telemetry from an env value
and makes no network call, so it is safe to leave running. The token itself is fine; only the
request rate is in question. Do not rotate it, and do not create a second token to route around the
limit, which would be scoped per token and would read as evasion.

**Phase 2, build the ingest first, channel second.** W3, W4, W5, W6, W7, W8, W9 are all testable
against the operator's existing 329-row export with zero network and zero delivery configured. Ship
them behind the fetcher interface with a local-directory fetcher. At the end of Phase 2 the operator
can drop a hand-exported file into `/var/lib/radon/flex-inbox/` and get a fully automated,
deduped, validated, heartbeated ingest. **That alone removes the manual pipeline** and is worth
shipping on its own even if delivery never gets configured.

**Phase 3, turn on delivery.** W2 then W1, in that order: the Worker and R2 must exist before mail
is pointed at them, or the first deliveries are dropped with no record. Run both channels in
parallel for one week, delivery-fed ingest plus the existing hand export, and diff the payloads
daily.

**Phase 4, demote.** Once a week of deliveries has landed clean, do W10: chokepoint the six
`SendRequest` sites, flip `radon-perf-twr.timer` to Sunday-only reconciliation, re-enable it. Steady
state is zero Flex Web Service requests per weekday and one per week.

**Rollback at any phase:** the fetcher interface has a local-directory implementation, and
`--from-file` works on a hand export from the Portal. That is the floor, and it is strictly better
than today's floor because the ingest, validation, dedup and alarm all still run.

---

## Sources

Flex Web Service pacing: https://ibkrcampus.com/docs/web-api/flex-web-service/using-flex-web-service/make-request-to-send-request.md ·
Error codes (1001 / 1009 / 1018 / 1019): https://ibkrcampus.com/docs/web-api/flex-web-service/error-codes.md ·
Usage Note 5, delivery as the alternative: https://ibkrcampus.com/docs/web-api/flex-web-service/introduction.md ·
Flex delivery settings (method is account-wide, sFTP and encryption by request): https://www.ibkrguides.com/clientportal/performanceandstatements/deliverysettingsflex.htm ·
Statements delivery: https://www.ibkrguides.com/clientportal/performanceandstatements/deliver.htm ·
Activity Flex Query Period field: https://www.ibkrguides.com/clientportal/performanceandstatements/activityflex.htm ·
File delivery, FTP/sFTP/PGP, daily Mon-Fri ~7-8PM ET: https://www.interactivebrokers.com/campus/ibkr-reporting-page/transmitting-files-3/ ·
Advisor Portal FTP: https://www.ibkrguides.com/advisorportal/ftp.htm ·
Account Management API is advisor/IB-only, PDF-only: https://ibkrcampus.com/docs/web-api/account-management/reporting/activity-statements.md ·
Access token lifetime and IP restriction: https://ibkrcampus.com/docs/web-api/flex-web-service/client-portal-configuration/enable-and-create-access-token.md
