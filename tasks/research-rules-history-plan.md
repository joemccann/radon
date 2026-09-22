# Held tab triage rules: durable approved / rejected history + after-the-fact flip

> **STATUS (2026-09-20): PLAN ONLY.** Design and done-when. No product code in this PR.
> Requested by Joe 2026-09-20 (narrowed scope). Implementation lands in a follow-up PR.

## 1. Problem

The Held tab (`DashboardNewsFeed.tsx` → `ResearchHeldReview.tsx` → `ResearchRuleProposals.tsx`)
shows triage rule proposals with Approve / Reject on the pending card and Revoke on an
active card. After Reject or Revoke the rule disappears from the UI, although the row keeps
`status = 'rejected'` in Turso. The operator cannot:

1. **See** a durable history of rules already approved OR rejected (only pending and active are listed).
2. **Change state after the fact** (flip approved ↔ rejected, undo a reject).

Approve / Reject on proposed cards stays as is. **No** edit UI for rule text, scope, threshold or action.

## 2. Root cause (verified on `main` @ `e5674ccc`)

| Layer | File | Fact |
|---|---|---|
| GET | `web/app/api/newsfeed/research/rules/route.ts` | `WHERE status IN ('proposed', 'approved') ORDER BY created_at DESC LIMIT 200`. Rejected rows are filtered out server-side. Pre-migration shape is `{ proposed: [], approved: [] }`. |
| POST | same file | `DECISIONS = { approve: "approved", reject: "rejected", revoke: "rejected" }`. Plain `UPDATE ... SET status = ?, decided_at = ?, decided_by = ? WHERE id = ?`. **No transition guard**: posting `approve` for a rejected id already flips it to `approved`. |
| UI | `web/components/ResearchRuleProposals.tsx` | Two lists (`Proposed rules`, `Active rules`). `decide()` removes the rule from `proposed` and, unless the decision is `approve`, from `approved`. A rejected / revoked rule has no list to land in, so it vanishes. `body.rejected` is never read. |
| Table | `scripts/db/migrations/0080_research_rule_proposals.sql` | `status CHECK IN ('proposed','approved','rejected')`, `decided_at`, `decided_by`, index `(status, created_at DESC)`. Nothing missing for this feature. |
| Worker | `scripts/research/learn.py:sync` | Upsert is `ON CONFLICT(id) DO UPDATE SET downs, ups, evidence_json` and **never writes `status`**; a rejected rule keeps its counts refreshed and stays rejected. Reads `status='approved'` into `rules.json` every `FEEDBACK_POLL_SECS = 60` (`scripts/research/ingestion.py`). |
| Tests | `web/tests/research-rules-api.test.ts` | Asserts "never rejected ones" and the two-key empty shape. `web/tests/research-rules-ui.test.tsx` asserts Reject / Revoke remove the rule from the DOM. `web/e2e/research-held-review.spec.ts` mocks GET with two keys. |

So the persistence and the state machine already support the request; the gap is one SQL
filter and one missing list in the client. No migration, no worker change.

## 3. Design (smallest)

### 3.1 API — `GET /api/newsfeed/research/rules`

Return a third key: `{ proposed, approved, rejected }`.

- Keep the existing query for `proposed` + `approved` (`LIMIT 200`, `created_at DESC`) unchanged.
- Add one query for rejected history so it can never starve the pending list:
  `SELECT ... FROM research_rule_proposals WHERE status = 'rejected' ORDER BY decided_at DESC LIMIT 50`.
  Newest decision first; `decided_at` is set on every POST so it is never NULL for a rejected row.
- Row → `Rule` mapping reused (`id, kind, key, downs, ups, evidence`). No new fields.
- Pre-migration branch returns `{ proposed: [], approved: [], rejected: [] }`.
- POST unchanged. `approve` on a rejected id is the flip; `revoke` on an approved id is the reverse.
  `radonCapability` stays `internal` on both verbs; rate keys unchanged.

### 3.2 UI — `ResearchRuleProposals.tsx`

Third list under the existing two, same `styles.list` / `styles.item` / `styles.ruleButton` classes:

```
Proposed rules   [Approve rule] [Reject]        (unchanged)
Active rules     [Revoke]                        (unchanged)
Rejected rules   [Approve rule]                  (new)
```

- `const [rejected, setRejected] = useState<Rule[]>([])`, hydrated from `body.rejected`.
- `decide()` becomes a move between three lists:
  `approve` → remove from proposed + rejected, prepend to approved;
  `reject` / `revoke` → remove from proposed + approved, prepend to rejected.
- Rejected card copy: `describe(rule)` plus a one-line note using existing `styles.note`
  (e.g. `Rejected. Approve to make it active.`). No em dashes, no new tokens.
- Empty-render guard becomes `!proposed.length && !approved.length && !rejected.length && !error`.
- `aria-label="Rejected rules"` on the new `<ul>` so tests and e2e select it the same way as the other two.
- No collapse / details widget, no timestamps, no edit fields. If the rejected list crowds the Held
  cards in the screenshot, the fallback is `<details>` around that one list only; decide on evidence
  in the implement PR, not up front.

### 3.3 Out of scope (explicit)

- Returning a rule to `proposed` (no `DECISIONS` target for it; the worker's upsert never touches `status`).
- Any change to `learn.py`, `triage.py`, `rules.json`, or the 60 s poll.
- Editing key / kind / thresholds / action.
- Migration or index changes (`idx_research_rule_proposals_status` already covers both queries).

## 4. Files touched by the implement PR

| File | Change |
|---|---|
| `web/app/api/newsfeed/research/rules/route.ts` | Second query for rejected (`LIMIT 50`, `decided_at DESC`); `rejected` key in both success and pre-migration shapes. |
| `web/components/ResearchRuleProposals.tsx` | `rejected` state, third list, three-way move in `decide()`, empty guard. |
| `web/tests/research-rules-api.test.ts` | Replace "never rejected ones" with "lists rejected under `rejected`, newest decision first"; pre-migration shape gains `rejected: []`; LIMIT split pinned (proposed survives 60 rejected rows). |
| `web/tests/research-rules-ui.test.tsx` | Reject moves the card into `Rejected rules`; Revoke moves the active card into `Rejected rules`; Approve from `Rejected rules` posts `{ id, decision: "approve" }` to the full URL and moves it into `Active rules`; nothing renders when all three lists are empty. |
| `web/e2e/research-held-review.spec.ts` | GET mock returns `rejected`; after the existing Approve step, Revoke → visible under `Rejected rules` → Approve → back under `Active rules`; screenshot at 1440 and 393. |
| `docs/dropbox-research.md` | Sentence "Revoke removes one" → Revoke moves the rule to the Rejected list, where Approve reactivates it; rejected history is listed. |

Nothing under `scripts/`, no migration, no `tools/codemap/*`.

## 5. Done-when (Joe's narrowed DoD)

- [ ] GET returns `{ proposed, approved, rejected }`; rejected rows appear with the same `Rule` shape, newest `decided_at` first, capped at 50; proposed + approved query and its `LIMIT 200` unchanged.
- [ ] Pre-migration GET returns `{ proposed: [], approved: [], rejected: [] }` with 200.
- [ ] Held tab shows a `Rejected rules` list containing every previously rejected or revoked rule returned by GET.
- [ ] Reject on a proposed card and Revoke on an active card move the card into `Rejected rules` (no disappearance).
- [ ] Approve on a rejected card posts `POST /api/newsfeed/research/rules` with `{ id, decision: "approve" }` and moves the card into `Active rules`; a failed POST raises the `ErrorToast` and leaves the card where it was.
- [ ] Approve / Reject on proposed cards and Revoke on active cards behave exactly as today (same labels, same payloads).
- [ ] No edit controls for key, kind, threshold or action.
- [ ] Red/green: the API test asserting "never rejected" fails first, then passes with the new shape; UI test for Approve-from-Rejected asserts the wire (full URL, method, body), not only the DOM move.
- [ ] Vitest: `research-rules-api.test.ts`, `research-rules-ui.test.tsx`, `research-held-ui.test.tsx`, `dashboard-feed-tabs.test.tsx`, `api-routes-no-cache-contract.test.ts`, `assistant-catalog-pin.test.ts` green.
- [ ] Playwright `research-held-review.spec.ts` green at 1440 and 393 with `held-rules-*.png` showing all three lists; screenshots attached to the implement PR.
- [ ] `docs/dropbox-research.md` updated (owner doc), CI green, PR body carries the root cause above.

## 6. Risks and known behaviour

| Risk | Handling |
|---|---|
| Rejected history starves the pending list under one `LIMIT` | Separate query with its own `LIMIT 50`; pending / active query untouched. |
| `learn.sync` lag | Already true today: `rules.json` refreshes from `status='approved'` every 60 s, so a flip (either direction) reaches triage within one poll. Same lag as the existing Approve. No new risk; state it in the card note only if the operator asks. |
| Rejected list grows without bound in the UI | Capped by `LIMIT 50` server-side; the worker refreshes counts but never resurrects a rejected row. |
| Rejected card note reads as a hint to click | Copy is one line, plain; button label stays `Approve rule` so the wire test and e2e need no new role names. |
| Layout: three stacked lists above Held cards on 393 px | Existing 44 px buttons and grid gap; verify in the mobile screenshot. Fallback is `<details>` on the rejected list only. |
| Test pollution | Tests mock `../lib/routeAccess` and use an in-memory libsql; adding a second `dbExecute` call changes call counts only where a test pins them (none today). |

## 7. Dependency graph

```
T1 route.ts (rejected query + shape)      depends_on: []
T2 ResearchRuleProposals.tsx third list    depends_on: [T1]
T3 API + UI vitest red/green               depends_on: [T1, T2]
T4 e2e + screenshots                       depends_on: [T2]
T5 docs/dropbox-research.md                depends_on: [T2]
```
