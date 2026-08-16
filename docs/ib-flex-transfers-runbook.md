# IB Flex Transfers — closing the last TWR data gap

## Why this matters

The /performance page reported +951.28% TWR because deposits were chained as investment return. That is fixed: flows now reach the TWR builder and an unexplained session is quarantined instead of published. One gap remains — Flex query `1497709` is a **CashTransactions** query, which structurally cannot carry a `<Transfer>` element, so the 2026-02-06 ACATS (NAV `246,713.50 -> 972,215.53`, `+725,502.03`) is invisible. The builder correctly refuses to publish a TWR and shows a degraded page. Skip this runbook and /performance stays degraded forever: the residual is real and no code change can invent the missing amount.

---

## 1. DECISION: one query, not two

**Recommendation: amend the existing Activity Flex Query `1497709` to add the Transfers section. Do not create a second query, and do not introduce `IB_FLEX_FLOWS_QUERY_ID`.**

Why one query is provably sufficient:

- **An Activity Flex Query already carries multiple sections, and `1497709` already proves it.** Two independent consumers read two different sections out of the *same* document from that one query id: `scripts/perf_twr_builder.py:282` reads `EquitySummaryByReportDateInBase` (the "NAV in Base" section) and `scripts/cash_flow_sync.py:254` reads `CashTransaction` (the "Cash Transactions" section). Sections in an Activity Flex Query are independent checkboxes; Transfers is another one of them. The idea that Flex queries are "typed" one-section-per-query is contradicted by the production query in front of us.
- **The builder is already written for the single-document case.** `_flows_query_id()` (`scripts/perf_twr_builder.py:454-455`) falls back to `IB_FLEX_NAV_QUERY_ID` when `IB_FLEX_FLOWS_QUERY_ID` is unset, and `resolve_flows()` (`:500-504`) reuses the already-fetched XML when the two ids match, so one build makes exactly one Flex request. This is pinned by `tests/test_perf_twr_ingest.py:283` (`test_flows_d7_one_flex_fetch_per_distinct_query_id`).
- **A second query id doubles the Flex request rate per build.** The Flex Web Service rate limit is a sliding window and this repo has already taken a 24h→168h embargo from over-requesting (`project_cash_flow_sync_incident_2026_08_04`, codes 1001/1018/1019). Adding a second id would put `radon-perf-twr` at two SendRequests every weekday evening for zero benefit.
- **`flex_flows.parse_flows` reads both sections out of one string** (`scripts/lib/flex_flows.py:146` and `:161`) and nets them per date, which is only correct if they come from one statement — which is what one query gives you.

Consequence: **no new environment variable, no code change, no deploy.** This is a Portal-only change (see §4 and §5).

> Distinct Flex query types do exist (Activity vs. Trade Confirmation). `1497709` is an Activity Flex Query — that is the type that owns both Cash Transactions and Transfers. If the Portal shows `1497709` under "Trade Confirmation Flex Queries" rather than "Activity Flex Queries", stop and re-plan; that would be new information.

---

## 2. Exact IB Portal clickpath

Menu wording drifts between IBKR Portal releases. The path below is the current one to the best of my knowledge; **anything marked "verify in the Portal" must be read off the screen, not assumed.** A wrong guess here costs a throttle cycle.

1. Log in to IBKR **Client Portal** (Account Management) as the account owner.
2. Top-right user menu → **Performance & Reports** → **Flex Queries**.
   *(Older wording: Reports → Flex Queries. Verify in the Portal.)*
3. Under **Activity Flex Query**, find the query with id **1497709** and click the **edit** (pencil/gear) icon. Confirm the id on screen before editing — do not edit `1422766` (the blotter/trades query) or `1442520` (journal rehydrate).
4. **Do not change** the existing enabled sections. `EquitySummaryInBase` ("Net Asset Value (NAV) in Base") feeds the NAV series and `Cash Transactions` feeds both `cash_flow_sync.py` and half the flow map. Removing either breaks production.
5. In the **Sections** list, tick **Transfers**. Click into it to open its field picker.
6. Tick these fields. The first six are **required by the parser**; the rest are audit-only but cheap:

   | Field | Required? | Read by |
   |---|---|---|
   | `reportDate` | **required** | `scripts/lib/flex_flows.py:163` (falls back to `dateTime`, then `date`) |
   | `dateTime` | recommended | fallback date source, same line |
   | `type` | **required in practice** | `:109`, `:117`, `:167` — appears in every `UnknownFlowType` message; without it the errors are unreadable |
   | `direction` | **required** | `:107-109`. Missing `direction` raises `UnknownFlowType(...:no_direction)` and fails the whole build. |
   | `assetCategory` | **required** | `:111-115` — selects the cash branch vs. the securities branch |
   | `positionAmountInBase` | **required** | `:115` — the amount the builder uses for a securities transfer |
   | `positionAmount` | **required** | `:115` — fallback when `positionAmountInBase` is blank |
   | `cashTransfer` | **required** | `:113` — the amount used when `assetCategory == "CASH"` |
   | `symbol` | audit only | not read by any parser |
   | `quantity` | audit only | not read; `quantity * transferPrice` is explicitly **not** sanctioned (`docs/performance-refactor-spec.md` §B.2) |
   | `transferPrice` | audit only | **never read as an amount** — `scripts/lib/flex_flows.py:105-106` says so in a comment; it is a per-share price and reading it is the original defect |
   | `accountId` | audit only | account scoping comes off `<FlexStatement accountId=...>` (`scripts/perf_twr_builder.py:269`), not off the `<Transfer>` row. Tick it anyway so a consolidated statement is human-auditable. |

   **Field-name uncertainty to verify in the Portal:** IBKR's Transfers picker labels fields in prose ("Position Amount in Base", "Cash Transfer", "Direction", "Asset Category", "Report Date", "Transfer Price"), not by XML attribute name. I am confident `direction`, `assetCategory`, `reportDate`, `cashTransfer`, `transferPrice`, `quantity`, `symbol` and `type` exist in the Transfers section. I am **least certain** that both `positionAmount` **and** `positionAmountInBase` are offered as separate checkboxes — some IBKR section pickers expose only the base-currency variant. If only one is available, tick it; the parser tries `positionAmountInBase` first and falls back to `positionAmount`, so either one alone works for a USD base-currency account. If **neither** appears, stop and report back — that invalidates the plan in §3 and needs a decision, not a workaround.
   If the picker offers a "Select All" for the Transfers section, use it. There is no parsing cost to extra attributes: `parse_flows` reads attributes by name and ignores everything else.

7. **Date / Period:** leave the query's existing period setting **unchanged**. The same document supplies the NAV series, so shrinking the window silently shortens `period_start` on the /performance page. Read the current setting and confirm the window still covers 2026-02-06 (verify in the Portal — likely "Custom Date Range" or "Year to Date"; whatever it is, `2026-01-13` deposits are already coming through, so it reaches at least that far back).
8. **Format: XML.** Not CSV. `fetch_flex_xml` sends `v=3` and calls `ET.fromstring` on the response (`scripts/perf_twr_builder.py:244-247`), and `cash_flow_sync.py:239-247` does the same. A CSV switch breaks both consumers instantly.
9. **Models / accounts:** leave the account selection unchanged. If it is a consolidated (multi-account) query, keep it consolidated — `_account_scopes` handles one `<FlexStatement>` per account.
10. Save. Note the id is still `1497709` — amending a query does not change its id. If the Portal creates a new query instead of editing, **do not proceed**; a new id would require the §5 env work you are trying to avoid.

---

## 3. ⚠ THE VALUATION SUBTLETY — read this before §6

**A securities transfer has no single unambiguous cash amount.** For an in-kind ACATS, `cashTransfer` is `0` — no cash moved. The economically correct external-flow amount `C` for TWR is the **market value of the transferred securities on the transfer date**, which IBKR reports as `positionAmount` / `positionAmountInBase`.

The rules, already implemented in `_transfer_amount` (`scripts/lib/flex_flows.py:102-120`):

- **Which field is `C`:**
  - `assetCategory == "CASH"` → `C = cashTransfer`.
  - anything else (`STK`, `OPT`, `FUT`, …) → `C = positionAmountInBase`, falling back to `positionAmount` only when the base-currency field is blank.
- **When several are present:** `positionAmountInBase` wins over `positionAmount` unconditionally (first-readable wins, `:89-99` + `:115`). `cashTransfer` is never consulted on a non-CASH row and `positionAmount*` is never consulted on a CASH row, so a partial-ACATS row that carries both a non-zero `cashTransfer` and a non-zero `positionAmountInBase` contributes **only** the branch its `assetCategory` selects. If you see such a row in §6b, flag it — it is the one shape the current code could under-count.
- **When all are zero or absent:** absent → `UnknownFlowType(transfer:<type>:no_amount)` at `:117`, which fails the build loudly and correctly (`resolve_flows` turns it into `FlowSet.failed`, status `degraded`). **Zero is different from absent**: `_amount` treats the literal string `"0"` as readable and returns `0.0`, so a securities transfer that IBKR reports with `positionAmountInBase="0"` produces a silent zero flow, not an error. Check for this explicitly in §6b.
- **Sign convention:** `direction="IN"` → **positive** flow; `direction="OUT"` → **negative**. The magnitude is `abs()`-ed first, so IBKR's own sign on the field is ignored and cannot double-negate (`:119-120`).
- **`transferPrice` is never an amount.** It is a per-share price. Reading it was defect #3 in `docs/performance-refactor-spec.md` §0, and `tests/test_perf_twr_flows.py:218` is the anti-pin.
- **Avoiding double counting — the partial-ACATS case.** Every `<Transfer>` row is external by definition, and `parse_flows` sums CashTransaction rows and Transfer rows into the **same** `by_date` bucket (`:158` and `:168`). If IBKR emits a `<Transfer assetCategory="CASH">` row for a wire that *also* has a `CashTransaction type="Deposits/Withdrawals"` row, that date is counted **twice**. The seven known cash deposits already in Turso `cash_flows` (2026-01-13 +80,007.13; 2026-01-21 -80,007.13 and +100,000.00; 2026-01-26 +24,925.00; 2026-01-27 +24,925.00; 2026-02-02 +24,925.00; 2026-02-04 +24,925.00; 2026-02-05 +24,925.00) are the exact dates to check for a newly-appearing duplicate Transfer row. §6b makes this an explicit check. A double count fails loud rather than silent — the inflated `C` drives the residual negative and re-quarantines the session — but it will read as "the fix didn't work", so rule it out first.
- The quarantine itself never looks at `C` (`scripts/lib/twr_math.py:309-325`). It judges the residual `r_t = (E - C - B) / B` only. That is why a *partially* captured transfer (cash leg recorded, securities leg missing) still quarantines instead of publishing a wrong number — which is exactly the state production is in today.

---

## 4. Code changes required: **none**

Nothing in the repo needs to change. Proof, by file and line:

| Concern | Verdict | Evidence |
|---|---|---|
| Does `flex_flows.py` parse `<Transfer>` correctly once present? | **Yes, already correct.** | `scripts/lib/flex_flows.py:161-169` iterates `.//Transfer`, calls `_transfer_amount` (`:102-120`) which handles direction, asset category, base-currency preference and the `transferPrice` trap, then nets into `by_date` alongside cash rows. Covered by `tests/test_perf_twr_flows.py:218` (ACATS IN uses `positionAmountInBase`), `:232` (OUT is negative), `:236` (missing direction raises), `:241` (CASH reads `cashTransfer`), `:246` (no amount raises), `:251` (cash + transfer net on one date). |
| Does the section detector see Transfers? | **Yes.** | The function is `_missing_flow_sections` (`scripts/lib/flex_flows.py:123-130`), not `_has_flow_section` — it returns every name in `_FLOW_SECTIONS = ("CashTransactions", "Transfers")` (`:54`) whose container element is absent, and `parse_flows` turns "zero external rows **and** a missing section" into `FlowSet.failed("flows_section_absent:…")` (`:171-174`). That is precisely today's production state. Independently, the builder has `_has_transfers_section` (`scripts/perf_twr_builder.py:458-462`) which raises the operator-facing `FLOWS_TRANSFERS_SECTION_ABSENT` error warning (`:465-486`) naming this exact fix. Both detectors key on the **container** element `<Transfers>`, which IBKR emits (empty, as `<Transfers/>`) when the section is enabled with no rows in range — confirm in §6a that the container is present. |
| Is `IB_FLEX_FLOWS_QUERY_ID` read in the right place? | **Yes.** | `_flows_query_id()` (`scripts/perf_twr_builder.py:454-455`) — `IB_FLEX_FLOWS_QUERY_ID or IB_FLEX_NAV_QUERY_ID`. |
| What happens when it equals the NAV query? | **The single fetched document is reused; no second fetch.** | `resolve_flows` (`:500-504`): `document.xml if document is not None and document.query_id == query_id else fetch_flex_xml(...)`. The NAV document travels on `NavResolution.document` (`:428-442`) for exactly this reason, documented at `:420-427`. Regression-pinned at `tests/test_perf_twr_ingest.py:283`. |

Two **verification** items (not code defects, not blockers) carried into §6b:

1. `_amount` returns `0.0` for a literal `"0"`, so `positionAmountInBase="0"` on a securities transfer yields a silent zero flow rather than the `no_amount` error.
2. A cash-category `<Transfer>` duplicating an existing `<CashTransaction>` on the same date is summed twice (§3).

If either shows up in the real XML, that is a follow-up code change with its own red test — do not patch it speculatively now.

---

## 5. Env wiring

**No environment variable is needed.** With the single-query recommendation, `IB_FLEX_NAV_QUERY_ID=1497709` already resolves flows via the `_flows_query_id()` fallback, and the amended document arrives through the fetch that already happens. Nothing to edit, nothing to restart, nothing to deploy.

Recorded here only in case §1 is overridden and a second query id is created:

- **File:** `/home/radon/radon-cloud/.env` on Hetzner, mode `0600`, owner `radon`. Add `IB_FLEX_FLOWS_QUERY_ID=<id>`.
- **Values containing `$` must be single-quoted** (repo rule; bash `set -a` expands `$VAR` under `set -u` and aborts silently from systemd). A numeric query id has no `$`, so this is a non-issue for this variable specifically.
- **Who reads it:**
  - `radon-perf-twr.service` — `EnvironmentFile=/home/radon/radon-cloud/.env` (`cloud/services/radon-perf-twr.service:12`). It is `Type=oneshot` fired by `radon-perf-twr.timer` (Mon–Fri 20:45 America/New_York), and systemd re-reads `EnvironmentFile` at every `ExecStart` — **no restart needed**, the next timer firing picks it up.
  - `radon-api` — `POST /performance/background` shells the builder via `run_script("perf_twr_builder.py", ["--json"])` (`scripts/api/server.py:3951`), and that subprocess inherits radon-api's process environment, which was captured at radon-api start. This one **does** need `radon unit restart radon-api`.
- **Never `radon restart`** — it cycles the IB Gateway and fires a 2FA push. App-tier-only work uses `radon unit restart <unit>` (`docs/incident-runbook.md:17-18`).
- Mirror the variable into root `.env`, `.env.example` and `cloud/.env.example` so local runs and the next operator match production.

---

## 6. Verification — in order

**Throttle discipline: exactly ONE manual Flex request in this whole section.** Steps a–d all run against a single saved XML file. Do not re-pull between steps. Read §7 before running anything.

### a. Pull the amended query once and confirm the Transfers section appears

Run **after 17:15 ET** (so `cash_flow_sync`'s daily 17:00 ET call has already completed) or **before 16:00 ET**, never in the 17:00–17:10 ET window.

```bash
cd /Users/joemccann/dev/apps/finance/radon/.claude/worktrees/perf-pnl-refactor
/Users/joemccann/dev/apps/finance/radon/.venv/bin/python - > /tmp/flex_1497709.xml <<'PY'
import os, sys
from dotenv import load_dotenv
load_dotenv('.env')
sys.path.insert(0, '.')
from scripts.perf_twr_builder import fetch_flex_xml
sys.stdout.write(fetch_flex_xml(os.environ['IB_FLEX_TOKEN'], os.environ['IB_FLEX_NAV_QUERY_ID']))
PY

grep -c '<Transfers' /tmp/flex_1497709.xml
grep -c '<Transfer ' /tmp/flex_1497709.xml
```

Pass: the container count is `>= 1` (one per `<FlexStatement>`). A `0` here means the section did not save in the Portal — go back to §2 step 6, and **wait 24h** before re-pulling.
If the pull fails with `Flex SendRequest failed code=1018` (or 1001/1019), you are throttled: stop, do not retry, and read §7.

### b. Confirm the 2026-02-06 transfer specifically, with its field values

```bash
/Users/joemccann/dev/apps/finance/radon/.venv/bin/python - <<'PY'
import xml.etree.ElementTree as ET
root = ET.parse('/tmp/flex_1497709.xml').getroot()
print('Transfers container present:', root.find('.//Transfers') is not None)
rows = root.findall('.//Transfer')
print('Transfer rows:', len(rows))
fields = ('accountId','reportDate','dateTime','type','direction','assetCategory',
          'symbol','quantity','transferPrice','cashTransfer',
          'positionAmount','positionAmountInBase')
for n in rows:
    print({k: n.get(k) for k in fields})

print('--- duplicate-date check against known CashTransaction deposits ---')
known = {'2026-01-13','2026-01-21','2026-01-26','2026-01-27','2026-02-02','2026-02-04','2026-02-05'}
dupes = [n.get('reportDate') for n in rows if (n.get('reportDate') or '')[:10] in known]
print('transfer rows on known cash-deposit dates (expect []):', dupes)

print('--- zero-amount check ---')
for n in rows:
    if (n.get('assetCategory') or '').upper() != 'CASH':
        v = (n.get('positionAmountInBase') or n.get('positionAmount') or '').replace(',','')
        if v in ('', '0', '0.0', '0.00'):
            print('SILENT-ZERO RISK:', n.get('reportDate'), n.get('type'), n.get('symbol'))
PY
```

Pass criteria:
- exactly one row with `reportDate` `2026-02-06`, `direction="IN"`, `assetCategory` not `CASH`, and `positionAmountInBase` ≈ `725,502.03` (the NAV delta `972,215.53 - 246,713.50`, less that session's true trading P&L — a few hundred dollars of difference is expected and correct, see §6d);
- `transfer rows on known cash-deposit dates` prints `[]` (no double counting — §3);
- no `SILENT-ZERO RISK` lines.

### c. Run the TWR math against that XML and confirm 2026-02-06 is no longer quarantined

Offline, no network, no second Flex pull:

```bash
/Users/joemccann/dev/apps/finance/radon/.venv/bin/python - <<'PY'
import sys, json
sys.path.insert(0, '.')
from scripts.lib.flex_flows import parse_flows
from scripts.perf_twr_builder import parse_nav_entries, build_payload, NavObservation

xml = open('/tmp/flex_1497709.xml').read()
nav = parse_nav_entries(xml)
flows = parse_flows(xml)
print('flows status :', flows.status.value, '| reason:', getattr(flows, 'reason', None))
print('flow dates   :', json.dumps(dict(sorted(flows.by_date.items())), indent=2))

obs = [NavObservation(date=d, nav=v) for d, v in sorted(nav.items())]
payload = build_payload(obs, flows, nav_source='flex_live', nav_sessions_behind=0)

print('status       :', payload['status'])
print('flows_status :', payload['flows_status'], '| source:', payload['flows_source'])
print('twr          :', payload['twr'])
sp = next((s for s in payload['subperiods'] if s['date'] == '2026-02-06'), None)
print('2026-02-06   :', json.dumps(sp, indent=2))
print('suspects     :', [s['date'] for s in payload['subperiods'] if s['skip_reason'] == 'suspect_no_flow'])
print('error warns  :', [w['code'] for w in payload['warnings'] if w['severity'] == 'error'])
PY
```

Pass:
- `flows status: ok` (not `failed`), and `2026-02-06` present in `flow dates` with a value near `+725,502`;
- the `2026-02-06` subperiod has `"skip_reason": null`, no `"suspect"` in `flags`, and `r` around `+0.002` (≈ +0.20%, the EOD-convention value from `docs/performance-refactor-spec.md` §B.3);
- `suspects` is `[]`;
- `error warns` no longer contains `FLOWS_TRANSFERS_SECTION_ABSENT` or `SUBPERIOD_SUSPECT`;
- `status: "ok"` (or `"stale"` if the NAV happens to be sessions behind — that is a freshness matter, not this fix).

`"flow_dominant"` **will** still be in that subperiod's `flags`, and a `FLOW_DOMINANT` warning will appear. That is correct and expected: it is severity `info`, it is a disclosure not an exemption, and it does not move the status (`docs/performance-refactor-spec.md` §B.4, `scripts/lib/twr_gates.py:26`).

### d. Expected total TWR

The earlier acceptance run — with all three flow classes applied, the ACATS amount **inferred** from the NAV delta via `--allow-inferred-flows` — produced **+24.88%**. Once the transfer is a real Flex row the number should land **near** +24.88%, but **it will not be identical, and it should not be.**

The inferred amount is `E_t - B_t` by construction (`scripts/perf_twr_builder.py:746-751`), which forces `r_t` to exactly `0.00%` for that session — it assumes the account earned nothing on 2026-02-06. The true `positionAmountInBase` is the market value of the arriving securities and differs from the NAV delta by that session's actual trading P&L (on the spec's worked example, `+502.03`, i.e. `r_t = +0.2035%`). Because TWR chains multiplicatively, that one session's real return propagates into the total. Expect a total within roughly ±1 percentage point of +24.88%. A total that is *wildly* different (say, above +40% or below +10%) means the transfer amount is wrong — go back to §6b and check for a double count or a missing leg.

Sanity check while you have the payload: `1 + total_return` should equal the product of `(1 + r)` over every non-null subperiod, and `n_subperiods == n_nav_observations - 1`.

### e. Confirm the published payload is `status: "ok"` in Turso

**Do not run the builder manually to make this happen.** Let `radon-perf-twr.timer` fire on its own at 20:45 ET (Mon–Fri) — that run makes the one Flex request it already makes every evening, so it costs nothing extra against the window. Then read the published row:

```bash
cd /Users/joemccann/dev/apps/finance/radon
RADON_DB_NO_REPLICA=1 /Users/joemccann/dev/apps/finance/radon/.venv/bin/python - <<'PY'
import json
from dotenv import load_dotenv
load_dotenv('.env')
from scripts.db.client import get_db

db = get_db()
row = db.execute(
    "SELECT taken_at, payload FROM performance_snapshots ORDER BY taken_at DESC LIMIT 1"
).fetchone()
taken_at, payload = row[0], json.loads(row[1])
print('taken_at     :', taken_at)
print('status       :', payload['status'])
print('flows_status :', payload['flows_status'], '| source:', payload['flows_source'])
print('nav_source   :', payload['nav_source'])
print('twr          :', payload['twr'])
print('error warns  :', [w['code'] for w in payload['warnings'] if w['severity'] == 'error'])
PY
```

Pass: `taken_at` is from tonight's run, `status` is `ok`, `flows_status` is `ok`, `flows_source` is `flex_cash_transactions+transfers`, `twr` is non-null, and `error warns` is `[]`. Then load /performance in the browser and confirm the degraded banner is gone and the hero number matches `twr.total_return`.

Optional cross-check that the audit tables filled (`scripts/perf_twr_builder.py:1288-1299`):

```sql
SELECT report_date, amount, flow_type FROM external_flows ORDER BY report_date;
```

`2026-02-06` should now be a row.

---

## 7. Throttle safety

**The risk.** The Flex Web Service rate-limits per token on a sliding window. Every request made *while* throttled pushes the reset further out. On 2026-08-04 this repo took a 24h→48h→72h→168h embargo ladder from exactly this (`scripts/monitor_daemon/handlers/_throttle_backoff.py:15-37`, codes `1001` / `1018` / `1019`). A week-long embargo on `1497709` takes down **both** the cash-flow sync and the TWR builder.

**Existing traffic on `1497709`, per weekday:**

| Consumer | When | Requests |
|---|---|---|
| `cash_flow_sync` (monitor_daemon handler) | 17:00 ET, trading days only | 1 SendRequest + polling |
| `radon-perf-twr.timer` → builder | 20:45 ET ±5min randomized delay, Mon–Fri | 1 SendRequest (document reused for flows) |

**Sequencing rules for the manual work:**

1. Amending the query in the Portal costs **zero** Flex requests. Do the Portal edit at any time.
2. Make **one** manual pull (§6a), and place it outside both windows — best is 18:00–20:00 ET, or any time before 16:00 ET. Never 16:55–17:15 ET and never 20:40–20:55 ET.
3. §6b, §6c and §6d all read `/tmp/flex_1497709.xml`. Do not re-pull for them.
4. Do **not** run `python scripts/perf_twr_builder.py` manually — it makes a fresh SendRequest. Let the 20:45 timer publish (§6e).
5. If §6a returns `1001`/`1018`/`1019`: stop immediately. Do not retry. Wait a full 24h, and check the daemon's embargo state before trying again:

```bash
cd /Users/joemccann/dev/apps/finance/radon
RADON_DB_NO_REPLICA=1 /Users/joemccann/dev/apps/finance/radon/.venv/bin/python -c "
from dotenv import load_dotenv; load_dotenv('.env')
from scripts.db.client import get_db
for r in get_db().execute(\"SELECT service, state, last_error, updated_at FROM service_health WHERE service='cash-flow-sync'\").fetchall():
    print(r)
"
```

`last_error` is JSON-encoded and carries `next_attempt_at`; that timestamp is when the embargo lifts.

**Does the amendment change what `cash_flow_sync.py` receives?** No, and no change is needed. `fetch_cash_transactions` iterates `root2.findall(".//CashTransaction")` only (`scripts/cash_flow_sync.py:254`), skips rows without a `transactionID` and rows with `amount == 0`, and classifies via `_classify` on the CashTransaction `type` string. `<Transfer>` elements live in a sibling container and are never visited. Adding the Transfers section adds elements it does not look at. The one real effect is a **larger XML document and a slightly longer statement-generation time** — it already tolerates that with capped exponential polling out to ~9.5 minutes (`:219-226`).

---

## 8. Rollback

**Trigger conditions:**
- `cash_flow_sync` starts erroring or writing fewer rows than before;
- Flex pulls start timing out (`Flex statement not ready after N polls` from `cash_flow_sync.py:249`, or `Flex GetStatement timeout after 30 polls` from `perf_twr_builder.py:261`);
- the TWR builder starts failing with `unknown_flow_type` or `parse_failed`.

**Rollback (Portal-only, zero deploy, zero code):**
1. IBKR Portal → Performance & Reports → Flex Queries → Activity Flex Query `1497709` → edit → **untick Transfers** → Save.
2. That is the entire rollback. The system returns to exactly today's behaviour: `parse_flows` sees the missing container, returns `FlowSet.failed("flows_section_absent:Transfers")` (`scripts/lib/flex_flows.py:171-173`), the builder publishes `degraded` with `twr: null` plus the `FLOWS_TRANSFERS_SECTION_ABSENT` warning, and `cash_flow_sync` is untouched throughout. Degraded, but never wrong.
3. No restart is required — the next timer firing re-fetches. If you want it immediate, `radon unit restart radon-api` then re-trigger; **never `radon restart`** (2FA push).

**If the failure is a timeout rather than a parse error**, first narrow the Transfers section's field list to the eight required fields in §2 (drop `symbol`/`quantity`/`transferPrice`/`accountId`) before rolling the whole section back — statement-generation time scales with fields × rows, and a smaller section may generate inside the budget.

**If the failure is `unknown_flow_type` / `no_direction` / `no_amount`**, that is not a rollback case — it is the parser telling you a field is missing from the Portal config. Re-open the field picker (§2 step 6) and tick the named field. The build stays degraded in the meantime, which is the correct state.

**Do not "fix" a rollback by deleting and recreating the query.** A new query id breaks `IB_FLEX_NAV_QUERY_ID=1497709` in three places (root `.env`, `web/.env`, Hetzner `/home/radon/radon-cloud/.env`) and takes down both consumers.
