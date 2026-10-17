# Grok page responder ledger transport resilience

Follow-up to PR #1113 (`ae2a6a0f` on main) and `af773f0f` (drop Hrana
keep-alives idle >= 5s before the next send). This document is the
design for PR #1130.

Python 3.13 only.

## Why this is still open after #1113 and af773f0f

`radon-grok-page-responder.service` is a Type=oneshot. It claims a
`watchdog_pages` row, runs grok for minutes, then writes `complete_page`
and sends a priority-0 follow-up. A transport blip on that ledger used
to escape `run_cycle`, exit 1, and page this unit about itself.

#1113 makes `RemoteDisconnected` on any ledger call non-fatal (exit 0,
skip the ok heartbeat, release the lock) and drops the idle socket after
grok. `af773f0f` already retires keep-alives idle >= 5s inside
`scripts/db/hrana_http.py` **before** the next send, so the always-drop
after grok is mostly redundant on current main (production grok runs
for minutes). Remaining gaps:

1. Classification is still substring matching on `str(exc)`. Connection
   reset, broken pipe, SSL EOF, `URLError`, and HTTP 502/503/504 still
   fail the oneshot. A statement error that happens to contain those
   words would be swallowed.
2. A lost `complete_page` response still drops the follow-up. The page
   stays `claimed` until `STALE_CLAIM_SECS` (2h), then another grok run.
3. A lost `claim_page` can leave the row claimed with this run's token
   while the cycle thinks it never owned it. The page is orphaned for 2h.
4. The only signal is `grok page ledger non-fatal` on stderr. Operators
   and existing health checks cannot see a one-off without paging.

## Stacking on #1113

#1113 is on `main` (`ae2a6a0f`). This branch merges `origin/main` and
keeps #1113's idle drop, disconnect runbook (folded #1110/#1112 peaks),
and lessons entry. The type-based classifier is the single path:
`_ledger_transport_error` wraps `is_transport_error` and replaces
#1113's `RemoteDisconnected` string match without dropping any case
that string match covered. `_ledger_read_timeout` is an alias.

| #1113 file | This PR | Overlap |
|---|---|---|
| `scripts/grok_page_responder.py` | Replace string match with type-based classification; keep `_drop_idle_hrana_socket`; add complete/claim recovery and the non-paging signal | Classifier, post-grok finish helper, existing `HranaHttpError` handler |
| `scripts/tests/test_grok_page_ledger_timeout.py` | Wrap `RemoteDisconnected` as `__cause__` so type classification hits; mock `get_page`; keep #1113 idle-drop and timeout cases | Private runtime lock fixture |
| `docs/incident-runbook.md` | Keep timeout, disconnect, and transport headings. Disconnect keeps the folded #1110/#1112 write-up | Cross-links between the three cases |

New files this PR owns:

- `plans/grok-page-responder-ledger-resilience.md` (this file)
- `scripts/tests/test_grok_page_ledger_transport.py`
- `scripts/db/hrana_http.py` (`HttpStatusError`, `is_transport_error`)
- `scripts/watchdog/pages.py` (claim readback, complete token guard)
- `docs/grok-page-responder.md` (states / health)

RemoteDisconnected is `http.client.RemoteDisconnected`, a
`ConnectionResetError`, so type matching covers it without looking at
the message.

## Design

### 1. Transport vs statement errors

Hrana wraps wire failures as:

```text
HranaHttpError(f"{type(exc).__name__}: {exc}") from exc
```

Statement failures (`no such table`, `empty pipeline response`, missing
creds) are raised as `HranaHttpError` with **no** `__cause__`.

Add in `scripts/db/hrana_http.py`:

- `HttpStatusError(OSError)` with `.status: int`, message still
  `HTTP {status}`. Raised from `_pooled_open` instead of bare
  `OSError(f"HTTP {resp.status}")`. Subclassing `OSError` keeps existing
  tests that catch `OSError`.
- `is_transport_error(exc) -> bool` that inspects types, never message
  text:

| Type | Non-fatal? |
|---|---|
| `TimeoutError` (builtin / socket) | yes |
| `http.client.RemoteDisconnected` | yes |
| `ConnectionResetError` | yes |
| `BrokenPipeError` | yes |
| `ssl.SSLEOFError` | yes |
| `urllib.error.URLError` that is not `HTTPError` | yes |
| `urllib.error.HTTPError` / `HttpStatusError` with status in `{502, 503, 504}` | yes |
| `HranaHttpError` whose `__cause__` matches any row above | yes |
| `HranaHttpError` with no cause (SQL, empty pipeline, missing creds) | **no** |
| `HttpStatusError` 401/403/404/429 | **no** |
| `OSError` that is not one of the types above (including `FileNotFoundError`) | **no** |

`urllib.error.HTTPError` is a `URLError`. Treat only 502/503/504 as
transport; a 404/401 `HTTPError` must still fail.

Replace `grok_page_responder._ledger_read_timeout` with a thin wrapper
around `hrana_http.is_transport_error`. Keep the same `except
HranaHttpError` in `run_cycle`: transport => log, `completed = False`
unless a recovery helper already finished the ticket, exit 0; anything
else re-raises.

Do **not** change `scripts/api/db_http.py` in this PR. The responder
uses `scripts/db/hrana_http.py` only.

### 2. Lost `complete_page`

`complete_page` today:

```sql
UPDATE watchdog_pages
SET status = ?, finished_at = ?, result = ?, claim_token = NULL
WHERE page_id = ?
```

The UPDATE is not replayed by the pool (`_retry_safe_read` is SELECT
only). A disconnect after the peer committed looks like failure: no
follow-up, row already `done` or still `claimed`.

**Guard.** `complete_page(..., claim_token: str | None = None)`:

```sql
WHERE page_id = ?
  AND (? IS NULL OR claim_token = ?)
```

- Token match: this run still holds the claim.
- Already `done` with a cleared token: 0 rows. Readback (`get_page`)
  sees `done` and still sends the follow-up. Do not rewrite the row.
- Foreign live claim: 0 rows. Do not steal.
- `claim_token is None`: legacy unguarded path for existing unit tests
  that do not pass a token. The responder always passes the token it
  used in `claim_page`.

**Retry once on a fresh connection.** Helper in the responder
(`_complete_page_with_followup`):

1. Call `complete_page` with the token.
2. On transport error: `_close_keepalive()`, call `complete_page` once
   more with the same args.
3. If either write returned without error: send the follow-up, treat as
   recovered if step 1 failed.
4. If both writes raised transport: `pages.get_page(page_id)` on the
   fresh connection. If `status` is `done` or `skipped`, send the
   follow-up (the first UPDATE committed). If still `claimed`/`pending`
   or the read fails: do not send, skip the ok heartbeat, exit 0.
5. A statement error on either write still propagates.

Use the same helper for the oneshot-rerun `complete_page` path.

`record_attempt_failure` after grok timeout/nonzero is the same lost
write class. Give it the same token guard + one retry + readback, but
do not send a follow-up (today it does not). Keep that in `pages.py`
so the responder stays small.

### 3. Lost `claim_page`

`claim_page` is `UPDATE` then `SELECT claim_token`. A disconnect on
either step can leave `status=claimed` with this token while the caller
sees an exception. `list_actionable_pages` hides fresh claims, so the
next cycle prints `pending: 0` and the ticket sits until the 2h stale
window.

Put recovery **inside** `pages.claim_page` (every caller benefits):

1. `hrana_execute(CLAIM_SQL, ...)`.
2. Read `status, claim_token` for `page_id`.
3. If `claim_token == ours`: return `True` (own it, including the case
   where the UPDATE's response was lost and we recovered on the SELECT).
4. On transport error around (1) or (2): `_close_keepalive()`, read
   once on a fresh connection.
   - Token matches: return `True` (continue this cycle; do not skip).
   - `status=pending`: retry the UPDATE once, then re-read.
   - Other token or missing row: return `False` (`lost_claim`).
   - Read also transport-fails: raise the original `HranaHttpError` so
     `run_cycle` takes the non-fatal path (exit 0, no grok). Residual:
     a double-fault can still orphan until `STALE_CLAIM_SECS`. Document
     it; do not add a third try.
5. Statement errors still raise.

Do not reclaim a row this process does not own. Do not shorten
`STALE_CLAIM_SECS`.

### 4. Non-paging signal

Constraints:

- Exit 0 (oneshot `Result=exit-code` is a P1 via `watchdog/units.py`).
- Do **not** write `service_health.state=error` (error bucket P2 after
  two cycles; that is a self-page).
- Do **not** heartbeat `ok` on an unrecovered standing outage (90m
  stale window is how a wedged Turso becomes visible).
- Do **not** send Pushover. Follow-ups and `radon grok:` pages must
  stay priority 0 and must not enqueue another ticket.

Signal (all three, cheap):

1. **Stdout JSON** the oneshot already uses for cycle results:
   `{"event":"grok_page_ledger_transport","op":"...","recovered":bool,"cause":"RemoteDisconnected"}`.
   `journalctl -u radon-grok-page-responder` already captures this.
2. **Stderr line** with a stable prefix:
   `grok page ledger transport: ...` (keep `grok page ledger non-fatal`
   as the unrecovered path so #1113/timeout greps still hit).
3. **Recovered cycles only:** heartbeat `ok` with
   `last_error={"class":"ledger_transport","op":"...","recovered":true}`.
   The error bucket fires only on `state=='error'`. Admin already shows
   `last_error` on `ok` rows (same pattern as `ib_rung_dead`). The next
   clean `pending: 0` cycle writes `ok` with `last_error=null`.

Unrecovered: skip the ok heartbeat (existing timeout behavior) so a
standing outage still goes stale. The JSON/stderr lines remain.

`@measure_operation("database")` already counts hrana errors into
`operation_metrics`. The oneshot's `atexit` flush emits them. Do not
add a new OPERATIONS key; do not make any control path depend on the
counter.

## Exact files and functions

| File | Change |
|---|---|
| `scripts/db/hrana_http.py` | `HttpStatusError`; `_pooled_open` raises it for non-200; `is_transport_error()` |
| `scripts/watchdog/pages.py` | `claim_page` readback/retry; `complete_page(..., claim_token=)`; `record_attempt_failure(..., claim_token=)` token guard |
| `scripts/grok_page_responder.py` | `_ledger_transport_error`; keep `_drop_idle_hrana_socket`; `_note_ledger_transport`; `_complete_page_with_followup`; pass `claim_token` into complete/failure; keep `except HranaHttpError` |
| `scripts/tests/test_grok_page_ledger_transport.py` | **New.** Classifier, complete retry+follow-up, claim ownership recovery, statement still fails, heartbeat/paging contract. `RADON_GROK_RUNTIME_LOCK` on a tmp path in the cycle fixture. |
| `scripts/tests/test_grok_page_ledger_timeout.py` | Set `RADON_GROK_RUNTIME_LOCK` (main is missing it; without it this file flocks the live VPS lock). Existing timeout cases must still exit 0. |
| `scripts/tests/test_watchdog/test_page_responder.py` | Token guard: foreign claim is not completed; retry no-op on `done`; claim transport then matching token returns True |
| `scripts/tests/test_hrana_http_keepalive.py` | Non-200 raises `HttpStatusError` with `.status` (shared module only; api copy unchanged) |
| `docs/grok-page-responder.md` | Ticket states: claim/complete recovery; health: transport signal |
| `docs/incident-runbook.md` | New case `grok-page-responder-ledger-transport`; keep #1113 disconnect case |
| `plans/grok-page-responder-ledger-resilience.md` | This plan |

No migration. `claim_token` already exists (0048).

No unit/timer/env change. Auto-deploy on merge to main is enough;
the dedicated clone fast-forwards `origin/main` on the next clean cycle.

## Tests (TDD, private runtime lock)

Every `run_cycle` fixture **must** set:

```python
monkeypatch.setenv("RADON_GROK_RUNTIME_LOCK", str(tmp_path / "runtime.lock"))
```

The live poller holds `/var/lib/radon/grok-runtime/grok-runtime.lock`
for the whole grok subprocess. A blocking flock on that path deadlocks
the test against the unit under test (the #1113 lesson). Copy the
fixture from `test_watchdog/test_page_responder.py` and
`test_grok_page_missing_binary.py`.

Interpreter: `python3.13` only.

Red then green, in `scripts/tests/test_grok_page_ledger_transport.py`
unless noted:

### Classifier (`TestLedgerTransportClassifier`)

Construct `HranaHttpError(...) from cause` the way `hrana_execute` does.
Assert `is_transport_error` true for: `TimeoutError`,
`RemoteDisconnected`, `ConnectionResetError`, `BrokenPipeError`,
`ssl.SSLEOFError`, `urllib.error.URLError("timed out")`,
`HttpStatusError(502|503|504)` and `HTTPError` with those codes.

Assert false for: `HranaHttpError("no such table: watchdog_pages")` (no
cause), `HranaHttpError("TURSO_DB_URL / TURSO_AUTH_TOKEN not configured")`,
`HttpStatusError(401)`, `HTTPError` 404, a `HranaHttpError` whose
message contains `RemoteDisconnected` but `__cause__` is None
(proves we do not classify by text).

### Lost complete (`TestLostCompletePage`, uses cycle fixture)

- First `complete_page` raises wrapped `RemoteDisconnected`; second
  succeeds. `run_cycle` exits 0, grok ran once, **follow-up sent**,
  ok heartbeat allowed, stdout JSON `recovered: true`.
- Both completes raise transport; `get_page` returns
  `status=done`. Follow-up still sent (committed write, lost responses).
- Both completes raise; `get_page` returns `status=claimed`. No
  follow-up, no ok heartbeat, exit 0, lock released.
- `complete_page` raises `HranaHttpError("no such table")`. Oneshot
  still fails (`pytest.raises`).
- Token guard (sqlite `db_conn` in `test_page_responder.py`): holder A
  cannot `complete_page` with B's token while A still owns the claim;
  after A completes, a retry with A's token (now NULL, status done)
  does not error.

### Lost claim (`TestLostClaimPage`)

- `hrana_execute` raises transport; subsequent query shows
  `claim_token=ours`. `claim_page` returns True; `run_cycle` **does**
  launch grok (not `lost_claim`).
- Execute raises; read shows `pending`. Retry execute succeeds. Returns
  True.
- Execute raises; read shows some other token. Returns False; cycle
  skips grok with `lost_claim`.
- Execute raises; read also transport. Cycle exit 0, no grok, no ok
  heartbeat.

### Signal (`TestLedgerTransportSignal`)

- Recovered complete: heartbeat `ok`, and `record_cycle_health` received
  `error={"class":"ledger_transport",...}`. Watchdog `_check_error` on
  that row stays `status=healthy` / `fired=False` (state is not
  `error`).
- Unrecovered: heartbeats empty, stderr has both
  `grok page ledger transport` and `grok page ledger non-fatal`.
- Statement error: no transport JSON, exception propagates.

### Existing timeout file

`TestLedgerReadTimeout` must stay green. After retry+readback, a
complete that always raises timeout and a `get_page` that returns None
still means no follow-up. Add the runtime lock fixture on main.

Do not run pytest and vitest at the same time. Focused:

```bash
python3.13 -m pytest -q \
  scripts/tests/test_grok_page_ledger_transport.py \
  scripts/tests/test_grok_page_ledger_timeout.py \
  scripts/tests/test_watchdog/test_page_responder.py \
  scripts/tests/test_hrana_http_keepalive.py
```

Then the related importer set used by #1113 (`test_grok_page_missing_binary`,
`test_grok_responder_fails_closed`, `test_grok_push_guard`,
`test_grok_child_env`, `test_ir_ensure_pr`, `test_grok_runtime`).

## Risks and rollback

| Risk | Mitigation |
|---|---|
| Retrying `complete_page` double-applies a different result | Token + terminal-status WHERE; second call is a no-op once `done` |
| Completing a page another cycle has already claimed | Token must match; stale reclaim uses a new token |
| Classifying SQL errors as transport | No `__cause__` => fatal. Text containing `RemoteDisconnected` is not enough |
| HTTP 401/429 treated as blips | Only 502/503/504 on the status type |
| `ok` + `last_error` pages the error bucket | Error bucket is `state == 'error'` only; pin with a check test |
| Skipping ok heartbeat on one-off unrecovered looks like a stall | Next `pending: 0` cycle (30s) heartbeats ok. Standing outage still ages out at 90m |
| Residual double-fault on claim readback | Document 2h stale reclaim; no third try |
| Merge conflict with #1113 on `_ledger_read_timeout` | Type match is the single path; keep idle drop and both runbook cases |
| Test hangs the live poller | Private `RADON_GROK_RUNTIME_LOCK` on every `run_cycle` test |
| Follow-up after a `done` row that another host completed | Token guard plus `status in done/skipped` only when token already NULL (our write). Foreign `done` with NULL token could in theory send a duplicate follow-up if we lost the write **and** another host finished first in the same race; both cycles sending priority 0 is acceptable, P1 is not |

Rollback: revert the merge commit. No schema down-migration. No flag.
Do not restart-flap the oneshot.

## Rollout

1. Merge this PR to `main`. CI then Production deploy. The responder
   clone `ff`s `origin/main` on the next clean cycle (`GROK_PAGE_SYNC_REMOTE=1`).
2. Operator: none. No env, token, or unit install. Grep
   `journalctl -u radon-grok-page-responder --grep grok_page_ledger_transport`
   after the next real page if a disconnect reproduces.
3. If a statement error is newly treated as transport (should not
   happen): revert. Discriminator remains `pytest
   test_statement_error_still_fails_the_oneshot`.

## Out of scope

- Changing `STALE_CLAIM_SECS` or the 90m health window.
- Replaying writes inside `hrana_http._pooled_open` (unsafe; peer may
  have committed).
- Sister `scripts/api/db_http.py` status class (responder does not
  import it).
- Watchdog enqueue path, pickup, or IR PR body.
- Making Yahoo a ledger fallback.
