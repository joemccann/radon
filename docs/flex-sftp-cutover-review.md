# Flex sFTP cutover — adversarial review

Status: OPEN. Date: 2026-08-24.
Target: `docs/flex-sftp-cutover.md` (DRAFT). Does not improve tone. Attacks load-bearing claims until they die or survive with file:line / IBKR URL evidence.

Do not SendRequest. 1025 is live until `2026-08-28T13:58:28Z` (`docs/incident-runbook.md:715-716`).

---

## Findings

### 1. Eligibility: Client Portal Flex sFTP vs reporting-integration sFTP

- **Severity:** P0
- **Claim attacked:** IBKR will grant sFTP for THIS individual (non-advisor) account for Flex Queries; `filedelivery@` + RSA + PGP + IP list is the enablement path (`docs/flex-sftp-cutover.md:49-54, 60-68`).
- **Evidence:**
  - Client Portal Flex delivery (the product we actually use): sFTP is **by request only**; if not requested, **Email is the only method**. Encryption is separately by-request and applies to email and sFTP. Method applies to all reports enabled for delivery. https://www.ibkrguides.com/clientportal/performanceandstatements/deliverysettingsflex.htm (updated 2025-10-03). Same text on statements delivery: contact `filedelivery@interactivebrokers.com`. https://www.ibkrguides.com/clientportal/performanceandstatements/deliver.htm
  - Advisor/Org FTP *settings page* (not Flex delivery dropdown): `https://www.ibkrguides.com/advisorportal/ftp.htm` — User menu → Settings → Account Reporting → FTP. Explicitly asks the requester to say whether sFTP is for **Statements/Flex Queries/PortfolioAnalyst** or **a specific data feed**. Client Portal has no equivalent page (2026-08-17 research: Client Portal FTP URL 404s — `docs/flex-delivery-architecture.md:63-67`).
  - `transmitting-files-3` (the table the plan copies for port 22, RSA, PGP **required**, Mon–Fri ~7–8PM ET, Reporting Holidays: None) lives under **IBKR Reporting Integration**, not Client Portal Flex. Account option on that page: "File is enabled from **master level** to automatically include all **sub accounts**." Support: `filedelivery@` and `salesengineering@` / Acct_Status. https://www.interactivebrokers.com/campus/ibkr-reporting-page/transmitting-files-3/ and https://www.interactivebrokers.com/campus/ibkr-reporting/reporting-integration/ (reportingintegration@, customer-hosted sFTP with IBKR-provided RSA key — **opposite direction** from "VPS is the SSH client").
  - Glossary: "Email delivery is the only method available unless you specifically request FTP delivery." https://www.interactivebrokers.com/campus/glossary-terms/delivery-settings/
  - Usage Note 5 exists on the Web API intro ("same Flex Query reports can also be scheduled for delivery via email or FTP") — https://www.interactivebrokers.com/docs/web-api/flex-web-service/introduction — but it does not say sFTP is available to an individual non-advisor account, and it does not name port 22 / PGP required.
  - No IBKR page states "individual IBKR Pro Client Portal accounts get IB-hosted sFTP for Flex Queries." The 2026-08-17 architecture already called this: **do not design around a capability that may not exist** (`docs/flex-delivery-architecture.md:60-67`). This cutover **supersedes** that conclusion without new evidence.
- **If `filedelivery@` says no:** the plan's fallback (email + `email_r2` fetcher, no inbound port) is the only surviving transport. sFTP code, RSA allowlist, PGP-as-required, 21:15 puller, and "VPS talks to IBKR on :22" all become dead. Plan currently says "Send the request **today**" (`flex-sftp-cutover.md:68`) and then builds sFTP in P2 **in parallel**. That is a sunk-cost trap: P2 must not land in production units until a written IBKR grant names **Flex Queries / Statements**, not a third-party data feed and not reporting-integration master/sub.
- **What the plan must change:**
  - Split the two products in the request email: (A) Client Portal Flex Query delivery over IB-hosted sFTP, pull, queries 1442520 + 1422766 only; (B) we are **not** asking for reporting-integration / EmployeeTrack / acct_status.
  - Hard stop: no `radon-flex-sftp-pull.timer` install, no IP allowlist, no PGP-required assumption, until the reply quotes Flex Queries. A grant for "file delivery" that mentions master/sub or reportingintegration@ is the **wrong product**.
  - Keep email ingest as the default transport in the architecture until the grant arrives. Do not "supersede" `docs/flex-delivery-architecture.md` on a maybe.
  - Add W0 from the 2026-08-17 doc (Portal **Run** on 1442520, not SendRequest). A query that 1001s will deliver nothing, silently (`flex-delivery-architecture.md:67-77, 203-215`). Missing from this cutover.
- **Status:** open

### 2. Delivery method is account-wide — inventory is not a stop

- **Severity:** P0
- **Claim attacked:** P4 "Confirm other currently-emailed statements (account-wide method)" plus failure-mode "inventory first" is enough (`flex-sftp-cutover.md:56, 180-183, 209`).
- **Evidence:**
  - "Select your preferred delivery method from the drop-down. **This will apply to all reports enabled for delivery.**" https://www.ibkrguides.com/clientportal/performanceandstatements/deliverysettingsflex.htm
  - Statements delivery screen configures **Daily and Monthly Activity Statements, Daily Trade Reports, Activity Flex Queries, and Trade Confirmation Flex Queries** in one place. Notification defaults include "email with Attachment" for daily/monthly statements and Daily Trade Reports. https://www.ibkrguides.com/clientportal/performanceandstatements/deliver.htm
  - You cannot keep emailed daily statements and sFTP Flex Queries. One method.
  - Plan P4 action is "Tick 1442520 and 1422766. Method sFTP. … Confirm other currently-emailed statements." "Confirm" is not a stop. There is no inventory checklist, no named current deliveries, no "if any human-consumed statement is Email+attachment, do not flip."
  - The 2026-08-17 doc already warned this and still did not list what is on (`flex-delivery-architecture.md:223-225, 245-246`).
- **What the plan must change:**
  - New **P0.5 blocking ops step**, before the filedelivery email: screenshot Client Portal Statements Delivery + Flex Queries Delivery. Write the inventory into the cutover doc: every report currently ticked, method, encryption, destination address.
  - Stop condition: if any report the operator still wants in a human mailbox is enabled, **do not set method to sFTP**. Use email Flex delivery (the superseded architecture) or leave those reports on Email and **abandon sFTP**.
  - If the inventory is empty (nothing currently delivered), say so in the plan. Silence is not an inventory.
- **Status:** open

### 3. P3 (stop `/api/blotter` POST rehydrate) vs live fills — Gateway is not a Flex substitute when it is down

- **Severity:** P1
- **Claim attacked:** "Live blotter = Gateway; sFTP 1422766 = daily recon." "fill_monitor / ib_execute / journal from Gateway remain source of truth for today" (`flex-sftp-cutover.md:221-223, 244`). Stopping POST `/api/blotter` → `/journal/rehydrate` is safe because same-day rows already land in Turso and GET `/orders` reads them.
- **Evidence — GET `/orders` does read Turso, not Flex:**
  - `web/app/api/blotter/route.ts:75-82` GET builds from `SELECT payload, filled_at FROM journal … LIMIT 5000`.
  - POST (`route.ts:91-118`) is the Flex poke: `radonFetch("/journal/rehydrate")`.
  - `web/lib/useBlotter.ts:14-22` + `web/lib/useSyncHook.ts:12, 18` (`hasPost` default true) → `/orders` `useBlotter(true)` POSTs every 5 min (`WorkspaceSections.tsx:3845`). This is the 1025 burner (`tasks/lessons.md:47`, `docs/incident-runbook.md:704-705`).
- **Evidence — live writers are real, but they are not "fill_monitor":**
  - **journal_sync** is the same-day SoT: `client.get_fills()`, real `ib_exec_id`, 300s, `requires_market_hours = True`, `post_close_grace_minutes = 15` (`scripts/monitor_daemon/handlers/journal_sync.py:1-19, 83-95`). Writes Turso via `upsert_journal_entry`.
  - **fill_monitor** is a partial-fill safety net with synthetic keys `fill-monitor:con-{conId}:order-{permId}:{date}:filled-{N}` (`fill_monitor.py:354-368, 177-181`). `fromJournal.ts` does **not** special-case these rows; they render until journal_sync **deletes** them (`journal_sync.py:48-51, 366-401`).
  - fill_monitor does **not** persist on first sight of an order (`fill_monitor.py:142-144` increments `new_orders` only). Persist runs only on `current_filled > prev_filled` for an already-known order (`147-181`). Completed-order path notifies and drops `known_orders`; it does **not** call `_persist_fill_to_journal` (`223-251`). A one-shot fill that appears and vanishes between 60s polls is journal_sync's job, not fill_monitor's.
  - fill_monitor's own comment: restart between detection and journal_sync loses the fill — "**only Flex rehydrate could recover it**" (`fill_monitor.py:177-180`).
  - Both handlers inherit `requires_market_hours: bool = True` (`base.py:91`). They do not run when the Gateway is 2FA-locked, cascade-stopped, or outside RTH+grace. `radon-monitor.service` is `PartOf=radon-ib-gateway.service` (`cloud/services/radon-monitor.service:4`).
  - `ib_execute.py:334-337` writes a **third** identity (`{date}T{time}#{next_id}`), CLI-only, not the workstation path.
  - FastAPI `POST /journal/rehydrate` still exists (`scripts/api/server.py:2947-2960`) and defaults `--days 365` (`journal_rehydrate.py:29, 651-678, 734`). P3 names Next `/api/blotter` only.
- **Verdict:** P3 is the correct 1025 kill for `/orders` **and** GET will still show same-day fills **iff** journal_sync ran. sFTP daily 1422766 makes the blotter **worse** on every Gateway-down session (2FA lock is a live class here): today a 5 min Flex rehydrate can recover; after cutover the next file is 21:15 ET or next morning. The plan accepts daily Flex blotter (`flex-sftp-cutover.md:221`) but does not name that regression or a Portal-XML `--from-file` recovery during IB outage.
- **What the plan must change:**
  - Name **journal_sync** as live SoT, fill_monitor as synthetic mirror, Flex as outage backfill. Stop saying fill_monitor covers today.
  - P3 must also delete or 404 FastAPI `POST /journal/rehydrate` and `POST /blotter` (`server.py:4062-4068`, `run_module("trade_blotter.flex_query")`).
  - Add an explicit Gateway-down recovery: operator Portal export → inbox → `--from-file`. Not SendRequest.
  - E2E: fill while `/orders` is open, with POST rehydrate disabled, assert journal_sync row appears in GET `/api/blotter` within one 300s cycle. Second case: Gateway down, assert no silent empty blotter and no SendRequest.
- **Status:** open

### 4. Two queries, one sFTP dir — "classify by FlexStatement vs FlexQueryResponse" is wrong and will mis-route

- **Severity:** P0
- **Claim attacked:** "classify by parsed XML (query sections / FlexStatement vs FlexQueryResponse), never by filename" (`flex-sftp-cutover.md:101, 207`). Filename convention unpublished, so XML classification is sufficient.
- **Evidence:**
  - **Both** production shapes are `FlexQueryResponse` wrapping `FlexStatement`. Activity 1442520 fixture: `scripts/tests/fixtures/cash_transactions_flex_ytd_detail_sample.xml:23-25` (`queryName="Equity Summary in Base" type="AF"`). Trade Confirmation parse path: `.//Trade` under the same wrappers (`scripts/tests/test_journal_rehydrate.py:868-873`, `scripts/trade_blotter/flex_query.py:186-194`). `FlexStatement vs FlexQueryResponse` distinguishes **nothing**.
  - IBKR publishes AccountID/FileType/AsOfDate for **reporting-integration**, not Flex delivery (`flex-delivery-architecture.md:257-259`). If `filedelivery@` provisions the reporting-integration product, filenames exist and the XML may not be a FlexQueryResponse at all.
  - `cash_flow_sync.parse_cash_transactions` walks `.//CashTransaction` only (`scripts/cash_flow_sync.py:442-443`). A Trade Confirmation file yields `[]`, then `upsert_cash_flow_rows` is a no-op, CLI still `EXIT_OK` (`cash_flow_sync.py:829-868`). Silent success.
  - `journal_rehydrate` / `FlexQueryFetcher._parse_xml` walks `.//Trade` only (`flex_query.py:186-194`). Activity 1442520 (NAV + Cash + Transfers, no Trades) yields zero executions. Silent skip.
  - Activity Flex **can** include a Trades section if that checkbox is ticked (`https://www.ibkrguides.com/clientportal/performanceandstatements/activityflex.htm` §5 Sections). Then one document matches **both** classifiers.
  - `queryName` is operator-editable. Do not key on it. `type="AF"` is on the Activity fixture; Trade Confirmation type code is unpublished in this repo.
- **Mis-route of 1422766 into cash_flow_sync:** zero cash rows, possible ok heartbeat, cash-flows lozenge looks "synced" only if some other path wrote `synced_at`. Worse: if ingest still calls `upsert_cash_flow_rows` on a 365-day Activity file **after** a Trade file no-op, see finding 9.
- **What the plan must change:**
  - Classifier is a **closed** match on **section presence**, fail-closed on ambiguity:
    - 1442520 iff `EquitySummaryByReportDateInBase` AND `CashTransaction` AND `Transfers`, AND zero `Trade` (or Trade ignored and never sent to journal_rehydrate).
    - 1422766 iff `.//Trade` AND no `EquitySummaryByReportDateInBase`.
    - Anything else: reject, keep `.gpg`, heartbeat error. Never "best effort."
  - Do not mention FlexStatement vs FlexQueryResponse as a discriminator.
  - Prefer IBKR putting queries in **separate remote directories** (ask in the filedelivery email). If they will not, SHA + section gate is the only safety.
  - Unit tests: swap the two fixtures; assert each is rejected by the other writer with zero Turso writes.
- **Status:** open

### 5. PGP required + RSA + IP allowlist vs Hetzner IPv6 blackhole and OpenSSH

- **Severity:** P1
- **Claim attacked:** PGP is required; send RSA pubkey + all Hetzner egress IPv4 **and** IPv6; puller is IPv4-first; host-key pin + `StrictHostKeyChecking=yes` (`flex-sftp-cutover.md:49-52, 76-77, 97, 128, 207, 231`).
- **Evidence:**
  - **PGP required** is the reporting-integration table (`transmitting-files-3`). Client Portal Flex delivery: encryption is **by request only**, off by default (`deliverysettingsflex.htm`, glossary). Request PGP anyway (correct), but do not treat "required" as proof we are on the Flex product.
  - IPv6 blackhole is real and recent: `scripts/utils/ipv4_first.py:1-12` (Hetzner advertises global AAAA; Yahoo/GitHub SYN blackhole; 2026-08-23 radon-divyield 60s/request). **That helper patches `socket.getaddrinfo` for Python urllib.** OpenSSH `sftp -b` and a stock paramiko client **do not call it**. Plan P2 says "paramiko or OpenSSH" + "IPv4-first" without `AddressFamily inet` / `HostName <ipv4-literal>`.
  - Allowlisting "all Hetzner egress IPv4 and IPv6" (`flex-sftp-cutover.md:76`): if IBKR allowlists the AAAA and the puller Happy-Eyeballs to v6, every connect eats the full TCP timeout, then maybe IPv4. If they allowlist only the v6 they saw in the email and we force IPv4, **auth fails**. Deploy runners (`~/.radon-deploy-runners/`, `docs/monorepo-cloud-migration.md`) change **inbound** GitHub→VPS SSH, not VPS egress — but a Hetzner NIC/IPv6 rebuild or floating-IP move **will** miss the allowlist with no page in this plan except "IPv6 blackhole → IPv4-first."
  - `ssh-keygen … -N ''` (`flex-sftp-cutover.md:154`): empty passphrase next to `TWS_PASSWORD` (finding 10).
  - `StrictHostKeyChecking=yes` with a dedicated `known_hosts` is correct. `accept-new` is not in the plan (good). Do not weaken this.
- **What the plan must change:**
  - Send IBKR **IPv4 only** until a v6 path to their sFTP host is proven (`curl -4/-6` / `nc` from the VPS, not from the laptop). Document the measured addresses in the host-only env, not git.
  - OpenSSH config: `AddressFamily inet`, `IdentitiesOnly yes`, `UserKnownHostsFile` pin, `StrictHostKeyChecking yes`. If paramiko: wrap with `prefer_ipv4()` **and** still pin the IPv4.
  - After any host network change: treat allowlist miss as fail-closed (heartbeat error), not a SendRequest retry.
  - PGP: request it; decrypt in memory as specified. Do not copy reporting-integration "PGP required" into Flex-delivery certainty.
- **Status:** open

### 6. Timer 21:15 ET vs measured Flex finalize ~07:30 next morning; holidays; 1025 still live

- **Severity:** P0
- **Claim attacked:** "Pull at 21:15 ET is after IBKR's ~19:00–20:00 window and after `radon-perf-twr`'s **old** 20:45 theory" (`flex-sftp-cutover.md:137-141, 95`). Missing file → error heartbeat, no SendRequest, one 22:30 retry. US holidays: puller runs, ingest no-ops.
- **Evidence:**
  - Live timer, not theory: `cloud/services/radon-perf-twr.timer:5-16` — moved to **Tue..Sat 07:30 ET** because "a live Flex pull at **20:57 ET Monday still returned NAV through Friday 08-14**" and "an evening run can only ever republish the previous session."
  - Same finding in the cash-flow handler: every 17:00 ET SendRequest 2026-08-10..20 was 1001; same query succeeds ~07:34 ET; "an evening pull never captured same-day data" (`scripts/monitor_daemon/handlers/cash_flow_sync.py:72-79`, `FIRE_HOUR_ET = 8`).
  - `transmitting-files-3` ~7–8PM ET is the **reporting-integration** clock, possibly the wrong product (finding 1). Even if Flex email/sFTP emits then, Web Service at that hour served **last finalized** NAV. Delivery of a query is not a new generation calendar.
  - 21:15 + 22:30 same evening therefore ingests **yesterday** (or Friday on Monday). Monotonic `period_to >= last` (`flex-delivery-architecture.md:360`) **accepts** a stale same-`period_to` republish. TWR page does not advance. This is the bug `radon-perf-twr.timer` already fixed.
  - US holidays: cash-flow handler skips via `utils.market_calendar`. Puller "runs Mon–Fri including market holidays" (`flex-sftp-cutover.md:143`). IBKR "Reporting Holidays: None" is again reporting-integration. A holiday file with `period_to` = previous session must not stamp `synced_at` on all cash_flows (finding 9).
  - 1025 live until **2026-08-28T13:58:28Z**, not "2026-08-28" (`incident-runbook.md:715-716`, `flex_embargo.py:26-27, 180-198`). A 22:30 "retry" that falls through to SendRequest, or P5 "after 2026-08-28" at 00:00Z, re-locks. Plan says no SendRequest on empty dir (`flex-sftp-cutover.md:141, 201`) — **that line stands** and must be a grep-enforced test, not a comment.
- **What the plan must change:**
  - Default pull calendar: **Tue..Sat 07:30 ET** (same as `radon-perf-twr.timer`), not 21:15. Treat 21:15 as an experiment: if `period_to` is not the last completed session, heartbeat error and **do not write**.
  - 22:30 retry is list-dir only. Structural test: `flex_sftp_pull.py` / ingest job must not import `gdcdyn` / `SendRequest`.
  - P5 / any recon: `raise_if_blocked()` and refuse until `active_until()` is None. Date-only "after 2026-08-28" is forbidden; use `2026-08-28T13:58:28Z`.
  - Re-add W0 (Portal Run) before trusting that a delivered file will exist at all.
- **Status:** open

### 7. "VPS talks to IBKR" vs outbound 22, egress firewall, known_hosts

- **Severity:** P1
- **Claim attacked:** Outbound TCP/22 to IBKR only; no inbound; host key pinned (`flex-sftp-cutover.md:38-42, 94-97, 122-124`).
- **Evidence:**
  - `cloud/scripts/setup-vps.sh:646-661` opens **inbound** 80/443 only. ufw default allow **outgoing** is not changed in-repo. Outbound 22 already works (git@github.com clone, `setup-vps.sh:22`). Opening outbound 22 is not a new hole. Inbound 22 from IBKR is correctly rejected.
  - "VPS talks to IBKR" is satisfied by **HTTPS to gdcdyn today**. sFTP is not the only way to talk to IBKR. The slogan does not justify picking a transport that may be refused (finding 1).
  - No `iptables` egress allowlist to the IBKR sFTP host exists in the plan beyond a bullet (`flex-sftp-cutover.md:128`). Without it, the unit can SSH anywhere the `radon` user can resolve, using a dedicated key **if** `IdentitiesOnly=yes`; without that, ssh-agent / default keys may leak.
  - `StrictHostKeyChecking=yes` + dedicated `known_hosts` stands. MITM via `accept-new` is not in the plan. Keep it that way.
- **What the plan must change:**
  - Pin OpenSSH `UserKnownHostsFile`, `IdentitiesOnly yes`, `AddressFamily inet`. Add a systemd `RestrictAddressFamilies=` / documented ufw outbound allow to the IBKR host **after** they give the hostname, not before.
  - Drop "VPS talks to IBKR" as a reason to prefer sFTP over email. The real reasons are observable absence and no mailbox. Those survive a refusal via R2 pull.
- **Status:** open

### 8. Dead SendRequest copies — plan under-counts call sites

- **Severity:** P0
- **Claim attacked:** "Every production Flex Web Service consumer. Two query ids." `portfolio_performance.py` still has dead `gdcdyn` URLs; cutover deletes or quarantines them. P3 stops `/api/blotter` and `/performance/background` (`flex-sftp-cutover.md:23-32, 113-116, 174-178`). 2026-08-17 W10 listed six SendRequest implementations (`flex-delivery-architecture.md:451-456`).
- **Evidence — live `gdcdyn` SendRequest in Python (grep `scripts/`):**
  | Site | File | Still live? |
  |---|---|---|
  | cash_flow_sync | `scripts/cash_flow_sync.py:106` | YES. Daemon 08:00 ET (`handlers/cash_flow_sync.py:66-80`). Not in P3. |
  | perf_twr_builder | `scripts/perf_twr_builder.py:121` | YES. `radon-perf-twr.timer` Tue..Sat 07:30 ET. Not in P3. FastAPI `_do_performance_rebuild` (`server.py:4113-4119`) shells it. |
  | flex_query | `scripts/trade_blotter/flex_query.py:72, 91` | YES. `journal_rehydrate` default fetcher (`journal_rehydrate.py:673-678`). Next POST `/api/blotter`. |
  | blotter_service | `scripts/trade_blotter/blotter_service.py:188, 212` | YES. Second `FlexQueryFetcher`. FastAPI `POST /blotter` (`server.py:4062-4068`) runs `trade_blotter.flex_query`. Plan names this file only as "dead" adjacent to portfolio_performance. It is not dead. Coverage: `scripts/tests/test_flex_caller_coverage.py:91-105`. |
  | portfolio_performance | `scripts/portfolio_performance.py:380, 502` | CLI + `ib_client.run_flex_query` (`ib_client.py:1146-1164` → ib_insync `FlexReport`, another SendRequest). Plan calls these dead; they still execute if invoked. |
- **Not in the plan's P3/P6 list at all:**
  - `POST /performance` (`server.py:4129-4148`) — R-101: GET `/api/performance` cold-start used to storm this; still a SendRequest behind cooldown.
  - `POST /performance/background` (`server.py:4226-4247`) — plan mentions it.
  - `POST /journal/rehydrate` (`server.py:2947-2960`).
  - `POST /blotter` FastAPI (`server.py:4062-4068`).
  - `radon-perf-twr.timer` + `radon-perf-twr.service` (`TimeoutStartSec=600`, Flex poll).
  - monitor `cash_flow_sync` 08:00 ET.
  - `ib_client.FlexReport` (`scripts/clients/ib_client.py:1160`).
- **P3 as written leaves weekday SendRequest at 2+ per day** (08:00 cash-flow + 07:30 TWR) **plus** any open `/orders` tab (5 min 1422766) until the Next route changes. That is not "routine Flex comes from sFTP."
- **What the plan must change:**
  - Full call-site table in the cutover, owned by a grep test (`gdcdyn` / `FlexStatementService` / `FlexReport(` allowlist). W10's "six implementations" is already stale (ib_insync is a seventh).
  - P3 expands to: Next `/api/blotter` GET-only (`hasPost: false`); 404/remove FastAPI `/journal/rehydrate` and `/blotter`; `/performance` and `/performance/background` file-only or 404 the Flex path; **stop or convert** `radon-perf-twr.timer` and cash-flow handler **before** celebrating sFTP. Sunday recon is the only SendRequest, behind `raise_if_blocked`.
  - Quarantine `portfolio_performance.py` **and** `blotter_service.py` **and** `ib_client.run_flex_query` in the same PR, or the "dead URL" claim is false.
- **Status:** open

### 9. `flex_deliveries` vs `cash_flows` PK (A8) — 365-day ingest rewrites `synced_at`

- **Severity:** P1
- **Claim attacked:** `flex_deliveries` `content_sha256` PK plus existing `cash_flow_sync --from-file` is a safe ingest (`flex-sftp-cutover.md:104-108, 164-167`). Implied idempotency.
- **Evidence:**
  - `cash_flows.id TEXT PRIMARY KEY` is IB `transactionID` (`scripts/db/migrations/0002_cash_flows.sql:13-21`).
  - `upsert_cash_flow_rows` last-write-wins on `id`, **always sets `synced_at = _now_iso()`** on every conflict (`scripts/db/writer.py:573-635`). Returns count of in-batch duplicates dropped (`584-590`).
  - A8: IBKR reuses one `transactionID` per posting batch; three 2026-07-06 rows net +220.21 collapse to +182.03; **$38.18 destroyed**; parser yields 264, Turso 262 (`docs/cash-flow-sync-overhaul.md:121-127`; warn path `cash_flow_sync.py:843-851`). C12 still NEEDS-DECISION (`cash-flow-sync-overhaul.md:518-532`). Nightly sFTP of Last 365 Days **replays the destruction every night**.
  - `/cash-flows` `last_synced_at = max(row.synced_at)` after date/type filters (`scripts/api/server.py:5114-5123`). A full 365-day upsert stamps **every** surviving id with now. That is why a full pull then a week of 1025 showed **7d** on the lozenge: the clock is "last successful upsert of the whole table," not "last new cash event." A nightly 365-day file makes the lozenge always "just now" even when `period_to` did not advance, unless ingest skips the writer.
  - `flex_deliveries.content_sha256` dedups **byte-identical** files only (`flex-delivery-architecture.md:276-300`). A new daily statement with one extra day is a new SHA → full upsert of overlapping history.
  - `--from-file` exists on cash_flow_sync (`cash_flow_sync.py:14-17, 723`). **`perf_twr_builder --from-file` does not exist** (grep). **`journal_rehydrate --from-file` does not exist** (grep; only `--days`). P1 of the plan is still unbuilt, and TWR file mode must be **strict** (no `disk_cache` fallback) (`flex-delivery-architecture.md:313-323`).
- **What the plan must change:**
  - Do not ship nightly 365-day cash upsert until C12 is decided **or** ingest inserts only ids not already present **and** does not update `synced_at` on unchanged `(id, amount, type, date)`.
  - Pin Period to **Last Business Day** (or Last N = 1) for delivery; 365-day is a Portal **Run** / recovery artifact, not the nightly file. Usage Note 6 already warned variable "Last N Days" (`flex-delivery-architecture.md:229-232`).
  - `last_synced_at` must follow `flex_deliveries.ingested_at` / `period_to`, not `max(cash_flows.synced_at)`.
  - P1 is blocked on actually implementing `--from-file` for TWR and journal_rehydrate. Do not pretend they exist.
- **Status:** open

### 10. Security: Flex PGP + TWS_PASSWORD as the same unix user; systemd vs `~/.ssh`

- **Severity:** P1
- **Claim attacked:** systemd hardening (`StateDirectory`, `UMask=0077`, `PrivateTmp`, `ProtectSystem=strict`, `NoNewPrivileges`) plus 0600 keys is enough (`flex-sftp-cutover.md:122-133`). Architecture had `ProtectHome=read-only` (`flex-delivery-architecture.md:493-495`); cutover **dropped** it.
- **Evidence:**
  - `radon-monitor.service` / `radon-perf-twr.service`: `User=radon`, `EnvironmentFile=/etc/radon/env` (contains `TWS_USERID`, `TWS_PASSWORD`, `IB_FLEX_TOKEN`). Proposed puller is the same user, identity `/home/radon/.ssh/ibkr_sftp`, PGP `/home/radon/.gnupg/` (`flex-sftp-cutover.md:98, 129`). Compromising `radon` (Next, API, monitor, this job) yields TWS login **and** Flex PGP private key **and** sFTP key (empty passphrase, finding 5).
  - `ProtectSystem=strict` without `ReadWritePaths`/`StateDirectory` would block the inbox; `StateDirectory=radon/flex-inbox` is the right write path. `ProtectHome=read-only` still allows reading `~/.ssh` and `~/.gnupg`. `ProtectHome=tmpfs` would hide the keys and break OpenSSH unless keys move under `StateDirectory` or `CredentialFiles`.
  - No backup of the PGP private key is specified. VPS rebuild → cannot decrypt retained `.gpg`. IBKR must then accept a new pubkey; files already delivered are lost.
  - Architecture item 8 still stands: do not open inbound sFTP on this host (`flex-delivery-architecture.md:501-504`). Cutover agrees. That part lives.
- **What the plan must change:**
  - Put SSH identity + PGP private material under `/var/lib/radon/flex-secrets/` (0700), not `~/.ssh` / `~/.gnupg`. systemd `BindReadOnlyPaths` + `ProtectHome=tmpfs` (or `read-only`) so the unit cannot read `/etc/radon/env` **if** the job does not need TWS. This job needs Turso + IBKR sFTP + PGP, **not** TWS_PASSWORD — drop `EnvironmentFile=` of the full env; pass only Turso + sFTP host vars.
  - Backup: encrypted copy of the PGP private key off-box (operator laptop, not git, not MCP mailbox). Test restore once before cutover.
  - Keep no-inbound, in-memory decrypt, no XML in git, no `account_ids` in logs.
- **Status:** open

### 11. Dual-run Sunday SendRequest while 1025 embargo exists

- **Severity:** P0
- **Claim attacked:** P5 "sFTP ingest + (after 2026-08-28) one Sunday SendRequest recon" (`flex-sftp-cutover.md:184-186, 190`).
- **Evidence:**
  - Every further SendRequest **extends** 1025 (`flex_embargo.py:1-7, 180-198`; `tasks/lessons.md:3-16, 42-48`; `incident-runbook.md:690-720`).
  - Deadline is `last_attempt_finished_at + 7d` = **2026-08-28T13:58:28Z**, not Monday 08:00, not date `2026-08-28` (`incident-runbook.md:715-716`). `record_lockout` is EXTEND-ONLY only for a **live** deadline; a new IBKR 1025 after lapse arms a **new** 7d (`flex_embargo.py:180-198`).
  - Sunday 2026-08-23 already passed. Next Sunday is **2026-08-30** (after 13:58Z on the 28th) — legal **if** nothing SendRequests Fri–Sat. A "week of dual-run" that starts when sFTP files first arrive (Wed/Thu if they reply in 1–2 days) **cannot** include a SendRequest before 13:58Z on the 28th.
  - P3 as written does not stop 08:00 cash-flow or 07:30 TWR (finding 8). Those timers **are** the dual-run, and they will fire Monday 08-24 through Friday 08-28 into the lockout unless `raise_if_blocked` holds. Embargo reconstruction is supposed to stop them (`tasks/lessons.md:8-13`); if the sidecar is missing and Turso reconstruct fails, they SendRequest. Plan must not add a Sunday job on top of that until P3 is complete.
- **What the plan must change:**
  - No SendRequest in P5 until `flex_embargo.is_blocked() is False` **and** `datetime.now(timezone.utc) >= 2026-08-28T13:58:28Z`. Encode in code, not in a date string in a markdown table.
  - First Sunday recon: 2026-08-30, one query id, one SendRequest, `raise_if_blocked` first. Not "one week of dual-run SendRequest."
  - Dual-run **data** comparison is sFTP/inbox vs Portal **Run** export (`--from-file`), not vs SendRequest, until the embargo lapses.
- **Status:** open

### 12. Scope creep: "all flex queries" vs 1442520+1422766; 1497709 is stale

- **Severity:** P1
- **Claim attacked:** Two query ids only; `IB_FLEX_FLOWS_QUERY_ID` stays unset; no new ids (`flex-sftp-cutover.md:23-32, 243`).
- **Evidence:**
  - Canonical ids: `.env.example:73,81` `IB_FLEX_QUERY_ID=1422766`, `IB_FLEX_NAV_QUERY_ID=1442520`. `IB_FLEX_FLOWS_QUERY_ID` commented unset (`83-87`). `CLAUDE.md` same. `cloud/config/required-env.txt` lists the two.
  - **Stale third id 1497709 still in tree:**
    - `docs/operations.md:51, 203` — `IB_FLEX_NAV_QUERY_ID=1497709`
    - `docs/performance-audit-findings.md:187` — claims production is 1497709
    - `docs/ib-flex-transfers-runbook.md:12-13` — admits 1497709 was copied from a stale CLAUDE.md line; production is 1442520
    - Tests using 1497709 as a **fixture string** (ok): `tests/test_perf_twr_ingest.py:77,303`, `scripts/tests/test_monitor_daemon/test_handler_heartbeat.py:242,266`
  - Ticking "all Flex queries" in Portal Delivery (`deliverysettingsflex.htm` "checkbox next to each Flex Query you want delivered") would enable every saved query on the account, including orphans. Plan P4 says tick 1442520 and 1422766 only — good — but the account-wide **method** still flips everything **enabled** (finding 2).
  - `flex_query.py:11` docstring wrongly says journal_rehydrate uses Flex Query **1442520**. It uses `IB_FLEX_QUERY_ID` (**1422766**) (`journal_rehydrate.py:665`). That lie will cause a "fix" that points blotter at the Activity query.
- **What the plan must change:**
  - P4: tick **only** 1442520 and 1422766. Untick every other Flex query before changing method.
  - Delete or stamp STALE on `docs/operations.md` 1497709 (and performance-audit-findings) in the same docs PR. Do not add 1497709 to sFTP.
  - Fix `flex_query.py` module comment. Do not deliver 1442520 as the trade query.
  - Portal: do not create `IB_FLEX_FLOWS_QUERY_ID`. A third remote file was how this token earned 1025 (`flex-sftp-cutover.md:30`, `CLAUDE.md` Flex section).
- **Status:** open

### Extra (not in the 12, still blocking)

#### E1. W0 is missing — scheduled delivery of a broken query is silent

- **Severity:** P0
- **Claim attacked:** Cutover can proceed in parallel with ingest against local XML (`flex-sftp-cutover.md:68, 160-168`) without Portal Run.
- **Evidence:** `flex-delivery-architecture.md:67-77, 203-215`. 1001 is generation failure, not 1018. Delivery of a query that cannot generate delivers nothing. 21:15 empty-dir alarm then looks like "sFTP late" forever.
- **What the plan must change:** P0 includes Portal **Run** (not SendRequest) of 1442520 and 1422766. Screenshot section counts. If Run fails, stop the filedelivery request for that query.
- **Status:** open

#### E2. systemd unit install contract

- **Severity:** P2
- **Claim attacked:** PR plan item 5 (`flex-sftp-cutover.md:233`).
- **Evidence:** `cloud/CLAUDE.md` / `flex-delivery-architecture.md:366-368`: new unit needs `install -m 0644`, `cloud/config/auto-sync-units.txt`, hash bump `cloud/config/installed-units.sha256`, or `tests/test_unit_install_acknowledgment.py` fails CI. Plan mentions this. Keep it. Also: do not enable the timer until finding 1 grant + finding 6 calendar are decided.
- **Status:** open

---

## What should STAND

These claims survived. Do not weaken them.

1. **Do not SendRequest during 1025.** Embargo through `2026-08-28T13:58:28Z`. Empty sFTP dir, decrypt fail, host-key change, missing file at pull time → **no** Web Service retry. `flex_embargo.raise_if_blocked` stays on every leftover caller (`flex_embargo.py:174-177`; runbook `flex-1025-lockout`).
2. **Do not rotate `IB_FLEX_TOKEN` to dodge 1025.** (`flex-sftp-cutover.md:46`; `CLAUDE.md`; `flex-delivery-architecture.md:561-563`).
3. **Do not open an inbound sFTP/webhook on the VPS.** `setup-vps.sh` 80/443 only. Fallback is email to a machine-owned address, not a listener (`flex-sftp-cutover.md:40-42, 66`).
4. **Do not deliver statements to an MCP-connected human mailbox.** (`flex-delivery-architecture.md:146-150, 478-479`).
5. **Inbox outside the git checkout.** `/var/lib/radon/flex-inbox/`, 0700/0600, no `data/*.xml` in a public repo (`flex-delivery-architecture.md:437-442`).
6. **Decrypt in memory; retain `.gpg` only; never log XML body or `account_ids`.**
7. **`IB_FLEX_FLOWS_QUERY_ID` stays unset.** One Activity document, two writers. Third query id doubles files and was a 1025 cause.
8. **P3 direction is right even if sFTP is refused:** `/orders` must stop POSTing rehydrate. GET Turso-only. That is the 1025 mitigation that does not wait on IBKR.
9. **`--from-file` / local-directory fetcher as the floor.** cash_flow_sync already has it (`cash_flow_sync.py:14-17`). TWR + journal_rehydrate must gain a **strict** file mode. Portal XML drop must run the pipeline with zero network.
10. **Host-key pin with `StrictHostKeyChecking=yes`** (never `accept-new`). Fail closed on key change.
11. **Eligibility is a hard gate.** Code can be built against fixtures. Production cutover waits on a written IBKR grant **for Flex Queries on this account**, or falls back to email ingest.

---

## Required plan edits ( condensed )

| ID | Change |
|---|---|
| P0-elig | Do not supersede email until Flex-Query sFTP is granted in writing. Wrong-product grant = refuse. |
| P0-inv | Inventory current Portal deliveries **before** method change; stop if any human email report is enabled. |
| P0-clock | Pull Tue..Sat 07:30 ET unless 21:15 files prove `period_to` is the last session. |
| P0-sites | Expand P3 to every `gdcdyn` / `FlexReport` caller, including 08:00 cash-flow and 07:30 TWR. |
| P0-class | Classify by section presence; FlexStatement vs FlexQueryResponse is not a discriminator. |
| P0-1025 | No SendRequest before `2026-08-28T13:58:28Z`. P5 recon is `--from-file` until then. |
| P0-W0 | Portal Run 1442520 and 1422766 before requesting delivery. |
| P1-blotter | journal_sync is live SoT; name the Gateway-down regression. |
| P1-pk | Do not nightly-upsert 365-day cash_flows; fix `synced_at` / A8 or pin Period=Last Business Day. |
| P1-ssh | IPv4-only allowlist; OpenSSH `AddressFamily inet`; keys not in `~/.ssh` next to TWS env. |
| P1-ids | Kill 1497709 in `docs/operations.md`; do not tick extra Flex queries. |
