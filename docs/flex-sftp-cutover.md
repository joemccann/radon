# Flex sFTP cutover

Status: FINAL after adversarial review 2026-08-24 (`docs/flex-sftp-cutover-review.md`)
Date: 2026-08-24
Does **not** supersede `docs/flex-delivery-architecture.md` until IBKR grants **Client Portal Flex Query** sFTP in writing. Until then email ingest is the default transport; sFTP is the preferred transport **if granted**.

Routine Flex must stop using `SendRequest` regardless of transport. That is the 1025 fix. sFTP is how the VPS talks to IBKR afterward, outbound only.

---

## Key decisions

1. **Do not SendRequest while 1025 is live** (`until 2026-08-28T13:58:28Z`, then `flex_embargo.is_blocked()`). Empty inbox, decrypt fail, host-key change, missing remote file → no Web Service retry.
2. **Do not rotate `IB_FLEX_TOKEN`.**
3. **No inbound port.** VPS is sFTP *client* to IBKR-hosted sFTP, or email pull from R2. Never IBKR upload-to-us.
4. **Eligibility is a hard gate.** `filedelivery@` grant must name Flex Queries / Statements for this account, not reporting-integration / master-sub / EmployeeTrack. Wrong product = refuse, keep email ingest.
5. **Two query ids only:** `1442520` (Activity) and `1422766` (Trade Confirmation). `IB_FLEX_FLOWS_QUERY_ID` unset. Do not tick other Flex queries.
6. **Live blotter = `journal_sync` (Gateway fills).** sFTP `1422766` is daily recon / outage backfill, not same-day SoT. fill_monitor is a synthetic partial-fill mirror.
7. **Pull calendar = Tue..Sat 07:30 ET** (same measured finalize clock as `radon-perf-twr.timer`). 21:15 ET is an experiment that must not write if `period_to` is not the last completed session.
8. **Nightly file Period = Last Business Day** (or Last 1 session), not Last 365 Days. 365-day XML is recovery only.
9. **Classify by section presence, fail closed.** `FlexStatement` vs `FlexQueryResponse` is not a discriminator.
10. **P3 (kill page-driven and weekday SendRequest) ships even if sFTP is refused.**

---

## Why

`SendRequest` is per-token HTTP. 1025 is a failed-attempts lockout; further SendRequests extend it. Live cash-flow last success 2026-08-16, 1025 at 2026-08-21T13:58:28Z.

IBKR Usage Note 5: the same Flex reports can be scheduled via email or FTP. That path does not use the token.

sFTP is preferred **if granted** because missing files are list-dir empty (loud) and the VPS pulls from IBKR over SSH, no mailbox. Email is the documented Client Portal default and the fallback.

---

## Two IBKR products (do not mix)

| | Client Portal Flex delivery | Reporting integration / Acct_Status |
|---|---|---|
| Who | This account's Flex Queries | Advisors / IB / third-party feeds, master/sub |
| Enable | Portal Delivery dropdown; sFTP **by request**; else **Email only** | `filedelivery@` / `salesengineering@` |
| Clock in IBKR tables | Unpublished for Flex (measured Web Service finalize is **next morning**) | ~19:00–20:00 ET Mon–Fri |
| PGP | By request, off by default | Table says required |
| Filenames | Unpublished | AccountID/FileType/AsOfDate |
| Docs | https://www.ibkrguides.com/clientportal/performanceandstatements/deliverysettingsflex.htm | https://www.interactivebrokers.com/campus/ibkr-reporting-page/transmitting-files-3/ |

The request email must quote **(A) Client Portal Flex Query delivery, IB-hosted sFTP, we pull, queries 1442520 and 1422766, XML.** **(B) Not reporting-integration, not EmployeeTrack, not acct_status, not master/sub.**

A reply that mentions master/sub or `reportingintegration@` is the wrong product. Stop.

---

## Scope (every SendRequest site)

Grep allowlist in CI: `gdcdyn`, `FlexStatementService`, `FlexReport(`.

| Site | File | Cutover |
|---|---|---|
| cash_flow_sync | `scripts/cash_flow_sync.py:106` | File ingest at 07:30; weekday SendRequest off. `--from-file` exists. |
| perf_twr_builder | `scripts/perf_twr_builder.py:121` | `--from-file` **does not exist yet** (strict, no disk_cache). Convert `radon-perf-twr.timer`. |
| flex_query | `scripts/trade_blotter/flex_query.py` | `journal_rehydrate --from-file` **does not exist yet**. Next `/api/blotter` GET-only. |
| blotter_service | `scripts/trade_blotter/blotter_service.py` | Live second fetcher. Quarantine with flex_query. FastAPI `POST /blotter` 404. |
| portfolio_performance | `scripts/portfolio_performance.py` | Quarantine. |
| ib_insync FlexReport | `scripts/clients/ib_client.py` `run_flex_query` | Quarantine. |
| FastAPI | `POST /journal/rehydrate`, `POST /blotter`, `POST /performance`, `POST /performance/background` | File-only or 404 Flex path. |
| Next | `web/app/api/blotter/route.ts` POST → rehydrate | GET Turso only. `useBlotter` `hasPost: false`. |

Canonical ids: `IB_FLEX_NAV_QUERY_ID=1442520`, `IB_FLEX_QUERY_ID=1422766`. **1497709 is stale** (`docs/operations.md`, `docs/performance-audit-findings.md`). Do not deliver it. `flex_query.py` docstring wrongly says 1442520 for journal; it is 1422766.

---

## Transport (best practice)

**If Flex sFTP is granted:** VPS SSH client → IBKR sFTP `:22`. Outbound only. IPv4-only until a v6 path is proven from the VPS (`AddressFamily inet`). IBKR allowlist gets **IPv4 only**.

**If refused:** email to `flex@stmt.radon.run` (not MCP mailbox) → Worker → R2 → same ingest. Still no inbound port. Still no SendRequest on miss.

Rejected: VPS as sFTP server; plain FTP/21; new Flex token; headless Portal; TWS as CashTransaction source.

---

## Blocking ops before any method change

### W0 — Portal Run (not SendRequest)

Run 1442520 and 1422766 in Client Portal. Screenshot section counts. If Run fails, do not request delivery for that query (delivery of a 1001 query is silent empty).

### W0.5 — delivery inventory (stop condition)

Screenshot Statements Delivery and Flex Queries Delivery. List every ticked report, method, encryption, destination.

**Stop:** if any report the operator still wants in a human mailbox is enabled, **do not set method to sFTP.** Either leave method Email (email ingest) or disable those reports first. You cannot keep emailed daily statements and sFTP Flex. One method.

### W0.6 — keys (only after W0 + inventory, still before grant)

On the VPS, not the laptop:

- SSH identity + PGP private under `/var/lib/radon/flex-secrets/` (0700), **not** `~/.ssh` / `~/.gnupg` next to TWS.
- Encrypted off-box backup of the PGP private key (laptop, not git, not MCP).
- Unit does **not** load full `/etc/radon/env` (no `TWS_PASSWORD`). Turso + sFTP host vars only.

Then email `filedelivery@interactivebrokers.com`. Do not SendRequest.

---

## Architecture (after grant, or email fallback)

```
IBKR reporting
  1442520  Activity XML  Period=Last Business Day  Format=XML
  1422766  Trade Confirmation XML  same Period
  Delivery: sFTP+PGP  OR  email to flex@stmt.radon.run
        |
        |  IBKR-hosted sFTP :22  (if granted)
        |  else R2 object from Email Worker
        v
Hetzner  radon-flex-pull.timer   Tue..Sat 07:30 ET
  scripts/flex_sftp_pull.py   (or email_r2 fetcher)
    OpenSSH: IdentitiesOnly yes, AddressFamily inet,
             UserKnownHostsFile pin, StrictHostKeyChecking yes
    download *.gpg -> /var/lib/radon/flex-inbox/  (0700/0600)
    decrypt in memory
    classify FAIL CLOSED:
      1442520 iff EquitySummaryByReportDateInBase AND CashTransaction
                 AND Transfers AND no Trade
                 (if Trade present: ignore Trade, never send to journal_rehydrate)
      1422766 iff .//Trade AND no EquitySummaryByReportDateInBase
      else: reject, keep .gpg, error heartbeat
        |
        v
  scripts/flex_delivery_ingest.py
    1442520 -> cash_flow_sync file path + perf_twr_builder --from-file (strict)
               insert-only / do not bump synced_at on unchanged (id, amount, type, date)
    1422766 -> journal_rehydrate --from-file
    Turso flex_deliveries (content_sha256)
    last_synced_at for /cash-flows := flex_deliveries.ingested_at / period_to
      NOT max(cash_flows.synced_at)
    heartbeat; retain <=3 newest .gpg; no plaintext
```

Ask IBKR for **separate remote directories** per query id. If they will not, section gate is the only safety.

---

## Cadence

| Job | Calendar | Source |
|---|---|---|
| flex-pull | Tue..Sat 07:30 ET | Measured: evening Flex still serves last finalized session (`radon-perf-twr.timer` comments; cash_flow 17:00 ET 1001s). |
| Optional 21:15 probe | experiment only | Write **only** if `period_to` == last completed session. Else error, no writer. |
| Empty dir | error heartbeat | One list-dir retry 08:30 ET. **No SendRequest.** Structural test: puller must not import `gdcdyn`. |
| Holidays | timer fires | No-op if `period_to` not new. Do not stamp `synced_at` on old ids. |
| Live fills | RTH journal_sync 300s | Gateway. Independent of Flex. |
| Gateway down | no Flex backfill until next 07:30 or operator `--from-file` | Named regression. Portal XML into inbox. Never SendRequest. |

---

## Cash-flow PK (A8)

Nightly 365-day upsert last-write-wins on `transactionID` and rewrites `synced_at` on every row (`scripts/db/writer.py`). That is why a full pull then a week of 1025 showed **Synced 7d ago**.

Nightly sFTP must be **Last Business Day** (new ids only) **or** upsert must not update `synced_at` when `(id, amount, type, date)` is unchanged. C12 (composite PK) stays a separate decision. Do not replay the $38.18 last-write-wins bug every night.

---

## Cutover phases

**P0 ops (this week, no SendRequest)**
W0 Portal Run both queries. W0.5 inventory. W0.6 keys. filedelivery email. 1025 stays armed.

**P1 ingest floor (code, parallel, no IBKR)**
`flex_deliveries` migration. `perf_twr_builder --from-file` strict. `journal_rehydrate --from-file`. Local-directory fetcher. Unit+timer **disabled** until grant + calendar. Tests: swap Activity vs Trade fixtures; each writer rejects the other with zero Turso writes.

**P2 kill SendRequest on the request path (code, does not wait on IBKR)**
- Next `/api/blotter` GET-only (`hasPost: false`).
- 404 FastAPI `/journal/rehydrate` and `/blotter`.
- `/performance` and `/performance/background` file-only.
- cash_flow handler and `radon-perf-twr.timer` skip SendRequest while `flex_embargo.is_blocked()`; after embargo they wait for P4 file ingest, not a weekday probe.
- Quarantine `portfolio_performance.py`, `blotter_service.py`, `ib_client.run_flex_query`.
- Grep test for `gdcdyn` / `FlexReport(` allowlist.
- E2E: fill with POST rehydrate disabled, journal_sync row on GET `/api/blotter` within 300s. Gateway down: no empty-blotter lie, no SendRequest.

**P3 sFTP puller (code, behind fetcher; do not install the timer until grant)**
OpenSSH as specified. IPv4. Keys under `flex-secrets`. Tests against a stub SSH, not live IBKR.

**P4 enable delivery (ops, after written Flex grant + empty-or-acceptable inventory)**
Tick **only** 1442520 and 1422766. Untick every other Flex query. Method sFTP **only if** W0.5 stop did not fire. Period Last Business Day. XML. PGP if they enabled it.

**P5 dual-run data (no SendRequest until embargo lapses)**
Compare sFTP/inbox vs Portal **Run** `--from-file`. First allowed SendRequest recon: **2026-08-30** Sunday, one query, `raise_if_blocked()` first, and only if `now >= 2026-08-28T13:58:28Z` **and** `is_blocked() is False`. Encode in code.

**P6 demote**
Weekday SendRequest = 0. Token remains for the Sunday job only.

Rollback: local-directory fetcher + Portal XML. Never "just SendRequest" during embargo.

---

## Failure modes

| Failure | Action |
|---|---|
| filedelivery grant is reporting-integration | Refuse. Email ingest. No sFTP timer. |
| Human-consumed statement still Email+attachment | Do not flip method to sFTP. |
| Empty remote dir / R2 miss | error; 08:30 list-dir retry; no SendRequest |
| Host key changed | fail closed, page |
| PGP decrypt fail | keep .gpg, fail closed |
| Ambiguous XML (both Trade and NAV) | reject |
| IPv6 blackhole / allowlist miss | fail closed; IPv4 only |
| 365-day file on nightly path | reject (Period gate) |
| `/orders` still POSTs rehydrate | P2 not done; do not celebrate sFTP |
| Sunday recon before 13:58Z 28th | forbidden in code |

---

## What this does not fix

- T+1 cash publication
- Intraday NAV
- A query that fails Portal Run
- Same-day blotter when Gateway/2FA is down (named regression; Portal `--from-file` is the recovery)
- A8 composite PK (separate)

---

## PR plan

1. **flex-inbox ingest** — migration, TWR + journal_rehydrate `--from-file` strict, local fetcher, section-classifier tests, `synced_at` insert-only, `last_synced_at` from `flex_deliveries`. No IBKR.
2. **kill weekday SendRequest** — blotter GET-only; 404 rehydrate/blotter POSTs; performance file-only; grep allowlist; e2e journal_sync.
3. **sFTP puller** — OpenSSH IPv4, pinned known_hosts, PGP in-memory, stub server tests. Timer **not** enabled.
4. **docs** — STALE 1497709; fix `flex_query.py` comment; runbook; this file.
5. **systemd** — unit + `auto-sync-units.txt` + `installed-units.sha256`. Enable timer only after Flex grant.

Ops (not a PR): W0, inventory, keys, filedelivery email, Portal ticks.

---

## Open questions (operator)

1. W0.5 inventory: any IB statement you still want emailed to a human inbox? If yes, sFTP method is off the table and we use email Flex ingest.
2. Accept daily Flex blotter recon, with same-day fills only from Gateway (`journal_sync`)? (Required for killing `/orders` POST.)
3. Off-box backup location for the PGP private key (not git, not MCP mail).
