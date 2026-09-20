# Research PDF intake: relax hold and invalidation gates

> **STATUS (2026-09-20): PLAN ONLY.** Design and done-when. No product code in this PR.
> Requested by Joe 2026-09-20. Implementation lands in a follow-up PR. No merge of this PR
> until that PR is scoped.

## 1. Problem

Operators open the Held tab and see documents held for reasons that have nothing to do with
whether the finding is true or new: a body over 2500 characters, a claim key over 160, a
figure caption that is missing or over 300 characters, a cited page number one past the
document's last page, an em dash the model wrote, and above all `NUMBER_NOT_ON_PAGE`, where the
code-only number matcher in `research.ground` could not find a token on the cited page text
even though the VERIFY model call would have judged the same claim against the same pages.

Every one of those paths ends the candidate with `continue` before VERIFY runs, so the one
gate that actually judges fidelity and novelty never sees the draft. The operator sees
`Draft failed validation` or `Number not on the cited page` and has to re-queue by hand.

## 2. Root cause (verified on `main` @ `0ed892c0`)

Over-strict, pre-model holds layered in front of the model gate. Three layers, all `continue`
or raise before VERIFY:

| Layer | File | Fact |
|---|---|---|
| Shape + length | `scripts/research/intake.py:validate_candidate` (v2) | `('title', 180), ('content', 2500), ('claim_key', 160)` raise `Invalid <key>`. `pages` must be `1..page_count`, at most 8, ints only, else `Invalid evidence pages`. A figure whose page is not cited is appended, but raises `Figure page exceeds the evidence page budget` at 8. `captions` must be a dict; every attached figure needs a caption `1..300` chars. All raise `EvidenceError`, caught in `_process` as `INVALID_CANDIDATE`. |
| Shape + length | `scripts/research/pipeline.py:validate_candidate` (v1) | Same limits plus `publisher <= 120`; `Chart must cite an evidence page` (no append); caption `1..300`; calls `validate_rendered_copy` and re-raises as `EvidenceError`. Caught in `_process` as `held: 'invalid candidate'`. `prepare_figures` re-calls it after a crop correction with `page_count = max(candidate['pages'])`. |
| Copy style | `scripts/research/publish.py:validate_rendered_copy` | Em dash (including HTML entities) or any ZeroHedge spelling in title, body, publisher, tags or captions raises `ValueError`. Called at intake (v2 loop, v1 validator) AND again inside `publish()`. |
| Code-only numbers | `scripts/research/intake.py` loop + `scripts/research/ground.py:ground` | `ground()` tokenizes every number, date, period, tenor, year range and figure reference in title, body and captions and looks each up on the cited pages by regex. Any miss sets `passed: False` and the loop appends `held: 'NUMBER_NOT_ON_PAGE'` and `continue`s. VERIFY is never called for that candidate. |
| Publish | `scripts/research/publish.py:publish` | `every figure needs a cited page and caption` (non-empty), title `<= 500`, body `<= 30000`, then `validate_rendered_copy` again. |
| Outbox | `scripts/research/worker.py:flush_outbox` | `publisher.publish(row['payload'])` is not wrapped. A `ValueError` from `publish()` propagates out of `cycle()`, the row stays at the head of the outbox and blocks every later flush. This is why a publish-time copy or caption raise is a silent publish failure, not a Held reason. |
| Prompts | `intake.py:SELECT_INSTRUCTION`, `pipeline.py:SELECT_SCHEMA` | Both state the limits as hard: `Limits: title<=180, content<=2500, claim_key<=160, caption<=300 characters` and `Write concise captions under180characters (hard maximum300)`. |
| Verify prompt | `intake.py:VERIFY_INSTRUCTION` | Says `The numbers have already been matched to the page text by code; judge meaning, not arithmetic`. True only while the ground hold exists. |
| UI | `web/lib/researchReasonCodes.ts` | Labels `NUMBER_NOT_ON_PAGE`, `INVALID_CANDIDATE`, `TEXT_ONLY_WITH_FIGURES`, `VERIFY_FAILED`. Historical `research_outcomes` rows carry these codes forever. |

Which pipeline production runs is set in the private runtime env (`RADON_RESEARCH_PIPELINE=v2`
selects `research.intake`; anything else keeps `research.pipeline`), so both validators change.

## 3. Design (smallest)

### 3.1 Gate disposition

| # | Gate today | Decision | Where |
|---|---|---|---|
| 1 | `content` length `<= 2500` | **Remove.** Keep non-empty string. | intake, pipeline |
| 2 | `claim_key` length `<= 160` | **Remove.** Keep non-empty string (it is hashed into the post id). | intake, pipeline |
| 3 | `title` length `<= 180` | **Remove.** Keep non-empty string. | intake, pipeline |
| 4 | `pages` in `1..page_count`, `<= 8`, figure page over budget | **Soft-clamp + warn.** Never `INVALID_CANDIDATE`. See 3.2. | intake, pipeline |
| 5 | Unknown `figure_ids` / non-list / `> 6` | **Keep.** | intake (v1: figures list shape, crop geometry) |
| 6 | Empty figures without `text_only: true` | **Keep.** | intake, pipeline |
| 7 | Caption required, `<= 300` | **Remove as hold.** Default when missing; no length cap. See 3.3. | intake, pipeline, publish |
| 8 | `tags` shape `1..10`, `TAG_RE` | **Keep.** | intake, pipeline |
| 9 | `validate_rendered_copy` (em dash, ZeroHedge) | **Remove the function and all three call sites.** Voice and attribution stay as prompt instructions (`SELECT_INSTRUCTION`, `SELECT_SCHEMA`, `policy.md`). | publish, intake, pipeline |
| 10 | `ground.ground` hold `NUMBER_NOT_ON_PAGE` | **Remove the hold.** Grounding becomes advisory input to VERIFY. See 3.4. | intake |
| 11 | `VERIFY_FAILED` | **Keep.** Becomes the single fidelity gate. | intake, pipeline |
| 12 | `TEXT_ONLY_WITH_FIGURES` | **Keep.** | intake |
| 13-18 | triage drops, `DUPLICATE_OF_PUBLISHED`, `SERIES_DENYLIST`, `RULE_*`, deadline, provider park | **Keep.** Untouched. | triage, novelty, learn |

Not in Joe's list, left as is: v1 `publisher <= 120` (never observed as a Held reason; v1 only),
v1 crop geometry checks, v1 `Crop references a page not visually supplied to selector` (a raise
outside the per-candidate `try`, pre-existing), v1 `numeric_evidence_passed` /
`date_evidence_passed` (model-quoted numeric checks, a different mechanism from `ground.py`).
Flag any of these for a follow-up if the Held tab shows them.

### 3.2 Evidence pages: soft-clamp

Shared behaviour in both validators, one small helper per file (no cross-module import to
keep v1 and v2 independent):

1. Coerce `pages` to a list (non-list or missing becomes `[]`).
2. Keep ints only (`type(p) is int`), drop `p < 1` or `p > page_count`, dedupe, preserve order.
3. Figure pages (v2: `catalogue[i]['page']` for each attached id; v1: `figure['page']`) are
   always included, appended when missing.
4. If more than 8 remain, keep every figure page, then fill with cited pages in order until 8.
5. Record what was dropped: `value['page_warnings'] = ['dropped 9 (page_count 8)', 'trimmed 2 pages to budget']`
   or similar short strings. The v2 loop copies `page_warnings` into the audit entry so the
   operator can see it on a published or verified item; never a `held`.
6. **Residual hard case:** zero pages remain after 1-4 (the model cited nothing usable and
   attached no figure). Raise `EvidenceError('No usable evidence pages')`. VERIFY has no page
   text to read otherwise. This is the only page-shaped hold left and it is a shape defect in
   the same class as 5 and 8. Flagged in section 8 for Joe to confirm.

Clamp must return a new list rather than mutate in place: v1 `prepare_figures` re-validates
`dict(candidate, figures=[figure])`, which shares the `pages` list with the original.

### 3.3 Captions: default at intake, tolerate at publish

Decision: **both sides relax, intake fills a default so the feed never renders an empty
caption.**

- The feed renders `${publisher} · p. ${page} · ${caption}` (`DashboardNewsFeed.tsx`), so an
  empty caption shows a trailing separator. Defaulting at intake avoids a UI change.
- v2 `validate_candidate`: `captions` that is not a dict becomes `{}`. For each attached id, a
  caption that is not a non-empty string becomes `catalogue[i].get('title') or catalogue[i].get('source_line') or f'Figure, page {catalogue[i]["page"]}'`.
  No length cap, no truncation.
- v1 `validate_candidate`: a figure caption that is not a non-empty string becomes
  `f'Chart, page {figure["page"]}'`. No length cap.
- `publish()`: caption check becomes type-only, `isinstance(figure.get("caption"), str)`
  (empty string allowed). `page in pages` and the PNG asset check stay. This is the
  belt-and-suspenders half: a payload built before the intake default (an outbox row from the
  current code, or the harness) still publishes instead of blocking the outbox.
- `publish()` title and body: non-empty string only. Drop `len(title) > 500` and
  `len(content) > 30000`. Reason: intake no longer bounds them, so a publish-time cap would be
  exactly the silent outbox failure Joe named. Turso `posts.title` / `content` are TEXT.

### 3.4 Numeric grounding: advisory, not a gate

Keep `ground.py` and its call. Change what happens with the result:

- The v2 loop no longer `continue`s on `not grounded['passed']` and no longer appends
  `held: 'NUMBER_NOT_ON_PAGE'`.
- The audit entry for the candidate already carries `grounding: grounded['tokens']` on the
  verified path; keep that, and add `unmatched: [t['token'] for t in tokens if t['page'] is None]`
  so the operator sees the same list `NUMBER_NOT_ON_PAGE` used to show.
- Pass the unmatched tokens to VERIFY as one extra prompt block:
  `\nTOKENS NOT MATCHED BY CODE ON THE CITED PAGES (confirm each against the page text; a value the pages do not state fails supported):\n` + `json.dumps(unmatched)`.
  Empty list when everything matched.
- `VERIFY_INSTRUCTION`: replace `The numbers have already been matched to the page text by code; judge meaning, not arithmetic:` with
  `Code has tried to match every number, date, tenor and period to the page text and lists the ones it could not find; confirm those against the cited pages (a value the pages do not state fails supported) and judge meaning:`.
  The rest of the instruction is unchanged.
- `ground.py` code unchanged. Docstring first line `Numeric grounding gate:` becomes
  `Numeric grounding (advisory):`. No behaviour change, `test_research_ground.py` unchanged.

Why advisory rather than delete: the module already produces the token table the audit stores,
it costs one prompt block to hand the verifier a focused checklist, and it keeps the code path
that an operator would want back if VERIFY alone proves too loose. Deleting the call would make
`ground.py` and its 90-line test file orphans and lose the `unmatched` detail on the Held card.

### 3.5 `validate_rendered_copy`: delete cleanly

- Delete the function from `publish.py`, its call inside `publish()`, and the now-unused
  imports `unescape` and `unicodedata`.
- Delete the import and call in `intake.py` (loop) and `pipeline.py` (validator + import).
- The `except (EvidenceError, ValueError)` in the v2 loop becomes `except EvidenceError`
  (the `ValueError` arm existed for this function).
- Em dash and ZeroHedge rules stay in `SELECT_INSTRUCTION`, `SELECT_SCHEMA` and `policy.md` as
  authoring instructions. `policy.md` line 9 loses the clause
  `Hold noncompliant candidates instead of silently changing substantive meaning.` (it describes
  a pipeline behaviour that no longer exists; `policy_sha256` in v1 `review.json` changes, which
  is informational).

### 3.6 Prompt length strings

- `SELECT_INSTRUCTION`: `Limits: title<=180, content<=2500, claim_key<=160, caption<=300 characters, 1..10 uppercase kebab-case tags, at most 8 pages per item.` becomes
  `Targets, not gates: title about 180, content about 2500, caption about 300 characters. Hard shape: 1..10 uppercase kebab-case tags, at most 8 pages per item (extra pages are trimmed).`
- `SELECT_SCHEMA`: `Write concise captions under180characters (hard maximum300)` becomes
  `Write concise captions, about 180 characters`; `Limits: title<=180characters, content<=2500characters, publisher<=120characters, claim_key<=160characters, caption<=300characters.` becomes
  `Targets, not gates: title about 180, content about 2500, caption about 300 characters; publisher at most 120.`
  `Maximum8evidencepages and6figures peritem` stays (still shape).

### 3.7 UI and historical rows

- `web/lib/researchReasonCodes.ts`: **no change.** `NUMBER_NOT_ON_PAGE` and `INVALID_CANDIDATE`
  labels stay so `research_outcomes` rows written before the change still read correctly on the
  Held tab. `outcome_row` in `publish.py` is label-agnostic and needs no change.
- `web/tests/research-held-*.test.*` and `web/e2e/research-held-review.spec.ts` keep their
  `NUMBER_NOT_ON_PAGE` fixtures; they test rendering of stored codes, not the intake.

## 4. Files touched by the implement PR

| File | Change |
|---|---|
| `scripts/research/intake.py` | `validate_candidate`: drop length limits on title/content/claim_key; soft-clamp pages with `page_warnings` (3.2); caption default and no cap (3.3). Loop: remove `validate_rendered_copy` import and call, narrow `except`; remove the `NUMBER_NOT_ON_PAGE` branch, add `unmatched` to the audit entry and to the VERIFY prompt (3.4); `VERIFY_INSTRUCTION` and `SELECT_INSTRUCTION` wording (3.4, 3.6). |
| `scripts/research/pipeline.py` | `validate_candidate`: drop length limits on title/content/claim_key; soft-clamp pages and append figure pages (3.2); caption default and no cap (3.3); remove `validate_rendered_copy` import and call. `SELECT_SCHEMA` wording (3.6). |
| `scripts/research/publish.py` | Delete `validate_rendered_copy` and its call; drop unused imports; caption check type-only; title/body non-empty only (3.3, 3.5). |
| `scripts/research/ground.py` | Docstring first line only. |
| `scripts/research/policy.md` | Line 9: remove the `Hold noncompliant candidates` clause. |
| `scripts/tests/test_research_intake.py` | See section 5. |
| `scripts/tests/test_research_pipeline.py` | See section 5. |
| `scripts/tests/test_research_runtime.py` | See section 5. |
| `scripts/tests/test_research_publish.py` | See section 5. |
| `scripts/tests/test_research_outcomes.py` | Keep the `NUMBER_NOT_ON_PAGE` fixture (historical). Add one case: an audit entry with `grounding` + `unmatched` and no `held` contributes no reason code. |
| `scripts/tests/test_research_ground.py` | Unchanged. |
| `docs/dropbox-research.md` | Lines 10 and 31: grounding is advisory input to VERIFY; `held` codes list drops `NUMBER_NOT_ON_PAGE` for new rows; `INVALID_CANDIDATE` now means shape only (figure id, text_only, tags, no usable page). Note that historical rows keep the old codes. |

Nothing under `web/`, no migration, no `tools/codemap/*`.

## 5. Test plan (red/green, one failing test per removed gate first)

`test_research_intake.py`
- `test_ungrounded_number_is_held_before_verification` inverts to
  `test_ungrounded_number_reaches_verify_with_unmatched_hint`: two reviewer calls, the second
  prompt contains `TOKENS NOT MATCHED BY CODE` and `$47bn`, the audit entry has
  `unmatched == ['$47bn']` and no `held`, and the post publishes when VERIFY passes.
- `test_selector_cannot_invent_figures_pages_or_dates`: split. Unknown `f9` still holds
  `INVALID_CANDIDATE` (gate 5). `pages=[7]` on the 2-page fixture with `f1` on page 1 is
  clamped to `[1]`, `page_warnings` recorded, VERIFY called.
- New: 3000-character body, 200-character title and 200-character claim_key pass validation and
  reach VERIFY; the post id is still `research-` + sha256 of the full claim key.
- New: attached figure with no caption publishes with `caption == catalogue title`; attached
  figure with a 400-character caption publishes unchanged.
- New: an em dash in the title reaches VERIFY (no `INVALID_CANDIDATE`).
- New: `pages=[]`, `figure_ids=[]`, `text_only=True` holds `INVALID_CANDIDATE` with
  `No usable evidence pages` (residual, section 3.2 step 6).
- New: 12 cited pages with figures on pages 11 and 12 trims to 8 with both figure pages kept.

`test_research_pipeline.py`
- `test_invalid_evidence_cannot_publish`: remove `('pages',[0])`, `('pages',[True])`,
  `('pages',[])` (clamp then figure page 1 fills in) and `('title','')` stays (non-empty).
  Add a text-only `pages=[]` case that still raises.
- `test_candidate_holds_authored_em_dashes_before_evidence_review`: delete.
- `test_candidate_preserves_literal_source_quotes_with_em_dashes`: keep (still true).
- New: figure on page 2 with `pages=[1]` appends 2 instead of raising `Chart must cite`.
- New: caption `''` becomes `Chart, page 1`; 400-character caption passes.

`test_research_runtime.py`
- `test_candidate_missing_shapes_cannot_be_accepted`: drop the empty-caption param; the
  `{"page":2}` figure param still raises, now on `Invalid chart crop`, so assert the message.
- `test_invalid_caption_holds_only_that_candidate_not_valid_siblings`: the 308-character caption
  no longer holds. Rewrite with a bad crop (`[.5,.5,.5,.6]`) so the "only that candidate"
  property is still pinned.

`test_research_publish.py`
- `test_authored_em_dash_never_reaches_publication`, `test_rendered_copy_gate_checks_tags_before_caller_normalization`,
  `test_intermediary_never_enters_rendered_research_copy`, `test_invisible_format_controls_do_not_bypass_attribution_guard`: delete.
  Replace with one test: a post whose body contains an em dash and whose publisher is
  `Goldman Sachs` publishes (the guard is gone, not moved).
- New: a figure with `caption: ""` publishes; the stored provenance keeps the empty string.
- New: 600-character title and 40000-character body publish.
- `test_invalid_figure_page_or_asset_extension`: keep (`cited` page match still enforced; adjust
  the regex if the message wording changes).

Verification commands: `python3.13 scripts/run_pytest_affected.py --files scripts/research/intake.py scripts/research/pipeline.py scripts/research/publish.py -- -q`
then the full `scripts/tests/test_research_*.py` set. Harness (`python -m research.harness --root <root> --labels <labels.json>`)
inside the research container before and after, to record how many previously held documents
now reach VERIFY.

## 6. Held reasons operators will no longer see (new rows)

| Code today | Detail text today | After |
|---|---|---|
| `NUMBER_NOT_ON_PAGE` | `Number not on the cited page`, detail = missing tokens | Gone for new rows. Same tokens appear as `unmatched` on the audit entry; a wrong number surfaces as `VERIFY_FAILED` with the verifier's excerpt. |
| `INVALID_CANDIDATE` / `Invalid title` | | Gone (only empty title holds). |
| `INVALID_CANDIDATE` / `Invalid content` | | Gone (only empty body holds). |
| `INVALID_CANDIDATE` / `Invalid claim_key` | | Gone (only empty key holds). |
| `INVALID_CANDIDATE` / `Invalid evidence pages` | | Gone unless zero usable pages remain. |
| `INVALID_CANDIDATE` / `Figure page exceeds the evidence page budget` | | Gone (figure pages win the budget). |
| `INVALID_CANDIDATE` / `Invalid captions` | | Gone (non-dict becomes `{}`). |
| `INVALID_CANDIDATE` / `Every attached figure needs a caption of at most 300 characters` | | Gone (default caption). |
| `INVALID_CANDIDATE` / `Rendered research copy must not contain em dashes` | | Gone. |
| `INVALID_CANDIDATE` / `Rendered research copy must attribute the original provider only` | | Gone. |
| v1 `invalid candidate` / `Chart must cite an evidence page` | | Gone (page appended). |
| v1 `invalid candidate` / `Chart caption must be a nonempty string at most300characters` | | Gone. |
| (not a Held reason) outbox stuck on `publish()` `ValueError` for caption / em dash / ZeroHedge / title length | | Gone; those raises no longer exist. |

Still visible: `INVALID_CANDIDATE` for `Unknown figure id`, `Missing figures must be explicitly text-only`,
`Invalid tags`, `No usable evidence pages`; `TEXT_ONLY_WITH_FIGURES`; `VERIFY_FAILED`;
`NO_CANDIDATES`; triage and duplicate drops. Historical rows keep whatever code they were
written with and the UI labels stay.

## 7. Done-when

- [ ] `intake.validate_candidate` accepts a 3000-character body, 200-character title and
      200-character claim_key; rejects only empty strings for those three.
- [ ] `intake.validate_candidate` and `pipeline.validate_candidate` clamp out-of-range,
      non-int, duplicate and over-budget pages, always keep figure pages, return a new list,
      record `page_warnings`, and raise only when zero usable pages remain.
- [ ] Missing or non-string captions default (v2: catalogue title / source line / `Figure, page N`;
      v1: `Chart, page N`); no caption length check anywhere.
- [ ] `validate_rendered_copy` does not exist in the repo (`rg validate_rendered_copy` returns
      only this plan and the PR body); `publish()` no longer imports `unescape` / `unicodedata`.
- [ ] The v2 loop never appends `held: 'NUMBER_NOT_ON_PAGE'`; every grounded candidate reaches
      VERIFY; the VERIFY prompt carries the `TOKENS NOT MATCHED BY CODE` block; the audit entry
      carries `grounding` and `unmatched`.
- [ ] `VERIFY_INSTRUCTION`, `SELECT_INSTRUCTION`, `SELECT_SCHEMA` no longer state hard length
      limits or claim numbers were pre-matched.
- [ ] `publish()` requires non-empty title and body only, caption type-only, cited page and PNG
      asset still enforced; a post with an empty caption, an em dash and a 600-character title
      publishes in `test_research_publish.py`.
- [ ] `web/lib/researchReasonCodes.ts` unchanged; `web/tests/research-held-*.test.*` and
      `web/e2e/research-held-review.spec.ts` untouched and green.
- [ ] Red/green: each removed gate has a test that fails on `main` and passes on the branch
      (section 5); `pytest scripts/tests/test_research_*.py -q` green; unrelated baseline
      failures reported separately.
- [ ] Harness before/after counts in the implement PR body (documents that previously stopped at
      `INVALID_CANDIDATE` or `NUMBER_NOT_ON_PAGE` and now reach VERIFY; how many of those pass).
- [ ] `docs/dropbox-research.md` and `scripts/research/policy.md` updated (owner docs); CI
      green; PR body carries the root cause from section 2.

## 8. Decisions flagged for Joe

1. **Residual page hold** (3.2 step 6): keep `No usable evidence pages` as the one page-shaped
   `INVALID_CANDIDATE`, or fall back to `[1]` and let VERIFY fail it? Plan keeps the hold;
   inventing a page is worse than a shape hold.
2. **Grounding advisory vs delete** (3.4): plan keeps `ground.py` and feeds unmatched tokens to
   VERIFY. Say the word and the implement PR deletes the call, the module and
   `test_research_ground.py` instead.
3. **Publish length caps** (3.3): plan drops `500` / `30000` entirely per "publish non-empty
   title/body". If a sanity ceiling is wanted it must be high enough that intake can never
   exceed it silently (say 20000 / 200000) and it must be tested.
4. **v1 `publisher <= 120`** stays. Not in the list, never seen as a Held reason.

## 9. Risks and known behaviour

| Risk | Handling |
|---|---|
| VERIFY passes a wrong number that `ground` would have caught | The unmatched list is in the VERIFY prompt and the instruction says an unstated value fails `supported`. Harness before/after and the first week of `VERIFY_FAILED` reasons show whether the model holds the line; the advisory path is one `continue` away from becoming a gate again. |
| Em dashes or `ZeroHedge` reach the feed | Prompt-only rule now. If it happens, the fix is prompt wording or a post-VERIFY rewrite, not a hold. Joe accepted this trade. |
| Long titles or bodies break the feed card | `posts.title` / `content` are TEXT. Check `DashboardNewsFeed.tsx` for clamping in the implement PR and watch the first published long item; a display cap belongs in `web/`, not in a hold. |
| Empty caption from an old outbox row renders `publisher · p. 3 · ` | Only for payloads built before the intake default; the outbox drains within one cycle after deploy. |
| Larger VERIFY prompts (up to 8 pages plus figure pages) | Page budget is unchanged at 8; figure pages already had to fit today. `PAGE_TEXT_CAP` unchanged. |
| `policy_sha256` changes for v1 `review.json` | Informational field; nothing reads it. |
| Test pollution | Publish tests use the in-memory libsql fixture; deleting the guard tests reduces the matrix, nothing shares state. |

## 10. Dependency graph

```
T1 publish.py: delete validate_rendered_copy, caption type-only, title/body non-empty   depends_on: []
T2 intake.py: validate_candidate relax + clamp + caption default                          depends_on: [T1]
T3 intake.py: loop, advisory grounding, VERIFY prompt, SELECT wording                     depends_on: [T2]
T4 pipeline.py: validate_candidate relax + clamp + caption default, SELECT_SCHEMA         depends_on: [T1]
T5 tests red/green (intake, pipeline, runtime, publish, outcomes)                         depends_on: [T1, T2, T3, T4]
T6 docs/dropbox-research.md, policy.md, ground.py docstring                               depends_on: [T3, T4]
T7 harness before/after counts in PR body                                                 depends_on: [T5]
```
