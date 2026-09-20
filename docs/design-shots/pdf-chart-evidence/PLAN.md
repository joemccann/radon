# PDF chart evidence: published research posts must carry the source charts, not "Text-only source evidence"

**Date:** 2026-09-20  
**Scope:** `scripts/research/figures.py` (deterministic chart detection) and `scripts/research/intake.py` (intake v2 SELECT validation). Nothing in `web/` changes; the footer label is downstream of `post.images`.  
**Status:** PLAN ONLY. No product Python or TSX in this PR. Implement branch: `fix/pdf-chart-evidence-not-text-only`. Merge of the implement PR is held until Joe says ship.  
**Bug (Joe via CoS):** the published item citing J.P. Morgan Daily Credit Strategy Update (2026-09-10) renders `J.P. Morgan · Source PDF · 2026-09-10 · pp. 2, 4 · Text-only source evidence`. Pages 2 and 4 of the PDF carry Fig 1 to 4 (hyperscaler HG fundamentals bars, JULI 3s5s / 10s30s curves). Operator screenshots: PDF pages with the figures; Radon feed card and lightbox with the text-only footer.  
**Sources:** `main` @ `e5674ccc`; `docs/dropbox-research.md` (Intake v2); `docs/research/agent-evidence-and-controls.md`; synthetic PDF probes run against `research.figures.detect` with `pypdfium2==5.13.0` (section 3).

---

## 1. Code map (verified on `main` @ `e5674ccc`)

| Stage | File and symbol | What it does today | Relevance |
| --- | --- | --- | --- |
| UI label | `web/components/DashboardNewsFeed.tsx`, `firstImage = post.images?.[0] ?? null`; footer appends `· Text-only source evidence` when `!firstImage` | Pure presentation of the stored post. | Downstream only. Not the fix site. The e2e `web/e2e/newsfeed-research-media.spec.ts` and Vitest `web/tests/newsfeed-research-markdown.test.tsx` already assert the label for `images: []`; they stay as-is. |
| Detection | `scripts/research/figures.py`, `detect(pdf_path, page_number)` | PDFium page objects filtered to `DRAWN = (PATH, IMAGE, SHADING)`, greedy single-link clustering with `GAP = 0.012`, then per cluster `count < MIN_OBJECTS (3) or area < MIN_AREA (0.02) or min side < MIN_SIDE (0.06)` drops it. Before any of that: `if page.get_rotation() != 0 or bbox != (0, 0, width, height): return []` for the whole page. | Two hard gates that silently empty the catalogue. Both confirmed live in section 3. |
| Catalogue | `scripts/research/figures.py`, `catalogue(pdf_path, pages, output_dir, dpi=216)` | Calls `detect` for every page, renders each bbox through `research.pdf.render(..., crop=bbox)`, ids `f1..`. | An empty `detect` means no ids exist for SELECT to pick. |
| Intake v2 | `scripts/research/intake.py`, `Pipeline._process` and `validate_candidate` | Catalogue for pages `1..count` is built once, then `review['figures']` is written; SELECT gets `{id, page, title, source_line}` per figure; `validate_candidate` requires `text_only: true` when `figure_ids` is empty and rejects unknown ids. No check that a `text_only` candidate cites pages that have catalogue figures. | Hypothesis 3 site. |
| Older path (v1) | `scripts/research/pipeline.py`, `validate_candidate` and `Pipeline.prepare_figures` | Model proposes crops; `text_only: true` required when `figures` is empty; crops go through one inspection and one bounded correction. | Only active when `RADON_RESEARCH_PIPELINE` is not `v2`. The flag is not in `cloud/.env.example`; production selection lives in the private runtime env. Section 2 step 0 decides which path published the JPM post. |
| Publish contract | `scripts/research/publish.py`, `publish(post)` | `images` must equal `[figure["url"] for figure in source["figures"]]`; every figure needs a cited page in `source.pages` and a caption; asset URLs are verified through `read_asset`. | Stays strict. Intake builds `images` from the same `asset_figures` list, so `images` cannot be empty while `source.figures` is populated (hypothesis 4 discarded by code). |
| Held review | `scripts/research/publish.py`, `outcome_row`; `web/lib/researchReasonCodes.ts` | `context.figureCount = len(review["figures"])` is shown on the Held card as `N charts`; `reason_codes` come from `audit[].held`. | `figureCount` is the fastest live diagnostic for the detection gates. A new hold code needs one label row. |

Pipeline selection: `scripts/research/model.py`, `build_pipeline` picks `research.intake.Pipeline` only when `RADON_RESEARCH_PIPELINE=v2`. v2 provenance carries `source.pipeline: "v2"`; v1 provenance has no `pipeline` key.

---

## 2. Root-cause verification on the live document (implementer runs these first, in order)

Run inside the research container where the private root is readable (`/var/lib/radon/research`, per `docs/dropbox-research.md`). Stop at the first step that answers.

0. **Which pipeline published it.** Query Turso: `SELECT provenance_json FROM research_post_sources WHERE post_id = '<id of the JPM post>'`. `"pipeline":"v2"` present means intake v2 (this plan). Absent means v1 (`research.pipeline`); then the model declined to propose crops and the fix below still applies once v2 is the running pipeline, but the immediate cause is a v1 selection, not detection. Record the answer in the implement PR body. Also read `RADON_RESEARCH_PIPELINE` from the private runtime env to confirm what is running now.
1. **Did detection see any figure.** Open `evidence/<work-key>/review.json` for the document (`work-key` is in `research_outcomes.work_key`; the file id is in `provenance_json.fileId`). Read `figures`. Cross-check the Held tab card: `N charts` in the context line is `len(review["figures"])`.
   - `figures: []` with charts visible on pages 2 and 4 confirms a detection gate (hypothesis 1 or 2). Continue to step 2.
   - `figures` non-empty with entries on pages 2 or 4 confirms hypothesis 3. Go to step 4.
2. **Which gate.** With the same `pypdfium2` wheel as the container: for pages 2 and 4 print `page.get_rotation()`, `page.get_size()`, `page.get_bbox()` and the count of `page.get_objects(filter=figures.DRAWN, max_depth=8)` by `obj.type` (`2 = PATH`, `3 = IMAGE`, `4 = SHADING`).
   - Rotation not 0, or `get_bbox()` not equal to `(0, 0, width, height)` (typical: a `CropBox` inset, or a `MediaBox` with a non-zero origin) confirms hypothesis 1: `detect` returns `[]` before clustering.
   - Frame passes and each chart is 1 or 2 `IMAGE` objects with no path neighbours confirms hypothesis 2: the cluster count is below `MIN_OBJECTS`.
   - Frame passes and charts are paths but clusters still fail: dump the cluster list from `_cluster(boxes, GAP)` with `count`, `w * h` and `min(w, h)`; a chart split into sub-clusters by a gap wider than 1.2% of the page, or a chart smaller than 6% of a side, is a threshold miss (not observed in probes; treat as a new hypothesis).
3. **Confirm the fix would have caught it.** Re-run `detect` on the JPM pages with the section 4 changes applied on a scratch branch and check that the rendered crops contain Fig 1 to 4 with their titles and `Source:` lines. Keep the crops as the implement PR's evidence.
4. **SELECT behaviour (only when step 1 shows figures).** Read `selection.json` for the document: the JPM candidate has `text_only: true` and `figure_ids: []` while the catalogue lists `f*` on pages 2 or 4. That is hypothesis 3 and is closed by section 4 P2.

Do not skip step 0. If production is still on v1, ship P1 and P2 anyway (they are the v2 path this repo is converging on) and state in the PR that the live post will only change once v2 runs.

---

## 3. Hypothesis verdicts (synthetic probes, `research.figures.detect` on `main`, `pypdfium2==5.13.0`)

Seven one-page PDFs built with the same helper as `scripts/tests/test_research_figures.py::_pdf` (a Helvetica page with a content stream), plus one raster `XObject` image drawn with `cm ... /Im1 Do`. Vector chart = the test file's `chart()` (axes, gridlines, one series, three bars, 11 path objects).

| Case | Page frame | Drawn objects | `detect` result | Verdict |
| --- | --- | --- | --- | --- |
| A control: vector chart | `MediaBox [0 0 612 792]`, rotation 0 | 11 PATH | 1 figure, 10 objects | Baseline works. |
| B single raster chart | zero origin, rotation 0 | 1 IMAGE (37% of page area) | 0 figures | **H2 confirmed.** A raster chart that is one image object never reaches the area test; `count < MIN_OBJECTS` drops it. |
| C `MediaBox [10 10 622 802]` | `get_bbox() = (10, 10, 622, 802)`, `get_size() = (612, 792)` | 11 PATH | 0 figures | **H1 confirmed.** Whole page rejected by the frame gate. |
| D `CropBox [20 20 592 772]` on a zero-origin MediaBox | `get_bbox() = (20, 20, 592, 772)`, `get_size() = (572, 752)` | 11 PATH | 0 figures | **H1 confirmed, common variant.** PDFium's bbox is MediaBox intersected with CropBox; publisher templates with a crop inset fail the gate on every page. |
| E `/Rotate 90` | `get_size() = (792, 612)`, `get_bbox() = (0, 0, 612, 792)` | 11 PATH | 0 figures | **H1 confirmed, rotation variant.** Landscape-by-rotation pages are rejected. |
| F two lone raster charts side by side | zero origin | 2 IMAGE | 0 figures | H2 again: each image is its own cluster of 1. |
| G one IMAGE plus two axis PATHs | zero origin | 1 IMAGE, 2 PATH | 1 figure, 3 objects | Confirms the count gate is the only thing stopping B and F. |

Frame mapping check for the fix in section 4 P1: normalizing object bounds against `get_bbox()` (left, bottom, right, top) instead of `(0, 0, width, height)`, then rendering the page with `page.render(scale=2)`, the right edge of the chart cluster matched the right edge of the rendered dark pixels to within 0.002 of page width in cases A, C and D. `render()` draws the displayed box, so bbox-relative coordinates are the right frame for unrotated pages with any MediaBox origin or CropBox inset.

| Hypothesis | Verdict | Evidence |
| --- | --- | --- |
| 1. Cited pages rejected by the rotation / MediaBox gate | Real gate, confirmed on synthetic pages (C, D, E). Whether the JPM PDF trips it is decided by section 2 step 2. | `figures.py` `detect`, first branch. |
| 2. Bank charts are single embedded IMAGE XObjects and fail `MIN_OBJECTS=3` | Real gate, confirmed (B, F, G). | `figures.py` `detect`, cluster filter. |
| 3. Catalogue has figures but SELECT sets `text_only` and nothing refuses | Confirmed by code: `intake.validate_candidate` only checks `text_only is True` when ids are empty; no cross-check against catalogue pages. | `intake.py` `validate_candidate`. |
| 4. Figures detected but `images` empty on the post | Discarded. `intake._process` builds `images` and `source.figures` from one `asset_figures` list; `publish.publish` rejects any mismatch. | `intake.py` `_process`, `publish.py` `publish`. |

Reproduce: rebuild the seven PDFs from the recipe in section 6 (they become the new test fixtures). The raw probe output is `PROBE.md` in this folder.

---

## 4. Chosen approach (deterministic detection stays primary; provenance stays strict)

### P1. `figures.py`: detect in the displayed frame and accept raster charts

1. **Frame.** Replace the early `return []` for a non-zero-origin bbox with bbox-relative normalization. `_norm(bounds, frame)` takes `frame = page.get_bbox()` and maps `x` to `(x - left) / (right - left)` and `y` to `1 - (y - bottom) / (top - bottom)`. Object boxes in `detect` and text rectangles in `_lines` both use the same frame, so `_grow_text`, `_attach` and the returned crop stay in the frame `research.pdf.render` draws. Guard: every bbox component finite and `right > left`, `top > bottom`; otherwise return `[]` as today.
2. **Rotation.** Keep detecting in the unrotated frame, then rotate the normalized box into the displayed frame before returning: for `[l, t, r, b]` and `/Rotate 90` return `[1 - b, l, 1 - t, r]`; `180` returns `[1 - r, 1 - b, 1 - l, 1 - t]`; `270` returns `[t, 1 - r, b, 1 - l]`. `render()` applies `/Rotate`, so the crop lands on the displayed page. This is the optional part of P1: ship it only with the pixel test in section 6 green; if the JPM pages are not rotated and the test is not ready, keep `return []` for rotation and record the page in the skipped list (item 4).
3. **Raster charts.** Track per cluster how many members are `IMAGE` objects (extend the `[box, count]` cluster to `[box, count, images]`). A cluster qualifies when `count >= MIN_OBJECTS` **or** `images >= 1`, and it still has to pass `MIN_AREA` and `MIN_SIDE`. Add one exclusion for image-bearing clusters: `w * h > 0.9` (a page-sized background or scan) is not a chart; this mirrors the v1 rule that a near-full-page crop is a failure. Record `kind: "vector" | "raster" | "mixed"` on the figure so SELECT sees it in the catalogue line and the Held card can show it.
4. **Visibility.** `catalogue` returns the same list shape; add a side channel `skipped_pages: [{page, reason}]` for pages that still return nothing because of a frame guard so `review.json` can carry it (intake writes `review["figure_gaps"]`). No behaviour change downstream; it is the diagnostic that step 1 of section 2 lacked.

Thresholds `GAP`, `MIN_AREA`, `MIN_SIDE`, `TEXT_REACH`, `LINE_REACH`, `MARGIN` do not change. Text-only pages (case `test_header_rules_and_text_only_pages_yield_nothing`) still yield nothing: a header rule is one PATH with zero images.

### P2. `intake.py`: refuse to publish `text_only` when a cited page has catalogue figures

In `Pipeline._process`, after `validate_candidate` and `validate_rendered_copy` pass: if `candidate["figure_ids"]` is empty and any catalogue figure has `page in candidate["pages"]`, append `{"held": "TEXT_ONLY_WITH_FIGURES", "claim_key": ..., "figures_on_cited_pages": [ids]}` to `review["audit"]` and `continue`. Nothing is stored; the document lands in the Held tab with the new code, `figureCount` on the card, and the existing "Should have published" vote re-queues it with an operator note (the note path in `_process` already asks SELECT to attach the supporting figure). Add `TEXT_ONLY_WITH_FIGURES: "Text-only draft while the cited pages have charts"` to `web/lib/researchReasonCodes.ts` (one label row; the fallback would render "Text only with figures", which is acceptable but less precise).

Budget: still two model calls per document. No re-ask loop.

### P3. SELECT prompt, one sentence

Append to `SELECT_INSTRUCTION` after "Attach a figure only from the catalogue by id, and only when it directly supports the finding": `When a page you cite has catalogue figures, attach the one that supports the finding; a text_only candidate that cites a page with catalogue figures is held for operator review.` The catalogue line SELECT receives gains `kind`. No other prompt change.

### What does not change

- `publish.publish` contract: `images == [f.url for f in source.figures]`, every figure cites a page in `source.pages`, asset URLs verified.
- VERIFY still sees the crop and can fail the item on a caption mismatch.
- Genuinely chart-free PDFs: empty catalogue, `text_only: true` passes as today, footer keeps `Text-only source evidence`.
- `web/` presentation, the two web tests and the e2e spec.
- v1 `research.pipeline` and `research.pdf.text_anchors` (same frame gate, v1-only anchor reader; out of scope).

---

## 5. Risks and guards

| Risk | Guard |
| --- | --- |
| Raster acceptance admits logos, headshots, cover images | `MIN_AREA` 2% and `MIN_SIDE` 6% stay; `> 0.9` area excluded; SELECT attaches only what supports the finding; VERIFY sees the crop. Watch the harness figure counts (section 6) for a jump on cover pages and, if needed, add `images == 1 and count == 1 and not (title or source_line)` as a second-stage exclusion. Not in P1 by default: a raster chart may embed its own title. |
| Frame mapping wrong for exotic boxes (negative origin, huge CropBox) | Guarded to finite, positive-extent bbox; the pixel-bbox test in section 6 pins C and D. |
| Rotation transform wrong | Pixel test on case E; the feature is optional in P1 and falls back to today's `return []`. |
| P2 lowers recall: SELECT keeps saying `text_only` on chart pages | Items go to Held with `figureCount` and the new code, not dropped; operator vote re-queues with a note. Measure with the harness before and after; if held volume exceeds published volume for chart-bearing documents, revisit P3 wording before adding any re-ask. |
| Crops now include page-size images on scanned PDFs | `> 0.9` exclusion; `render` still enforces the 24 MP pixel budget. |

---

## 6. Test plan (red first, then green)

**Python, `scripts/tests/test_research_figures.py`** (extend `_pdf` to accept a page-dictionary suffix, an optional raster `XObject` and a MediaBox origin; add `image_chart(x0, y0, x1, y1)` emitting `q w 0 0 h x y cm /Im1 Do Q` with a small `DeviceGray` 8-bit image):

- `test_single_raster_image_chart_is_a_figure`: case B; expect one figure, `kind == "raster"`, bbox covers the image plus the title above and the `Source:` line below, prose stays outside. Red on `main` (0 figures).
- `test_two_lone_raster_charts_are_two_figures`: case F; two figures, left one first.
- `test_near_full_page_image_is_not_a_figure`: one image covering 95% of the page; expect `[]`.
- `test_offset_mediabox_page_detects_in_displayed_frame`: case C; one figure; then `pdf.render(..., crop=bbox)` and assert the crop's dark-pixel extent touches the crop's right and bottom edges within 2% (chart fills its crop). Red on `main`.
- `test_cropbox_inset_page_detects_in_displayed_frame`: case D; same assertions.
- `test_rotated_page_crop_lands_on_displayed_chart` (only if P1 item 2 ships): case E; render the crop and assert the chart occupies it as above.
- `test_catalogue_reports_skipped_pages`: a page that still fails the guard (non-finite bbox is hard to build; use a mocked `get_bbox`) appears in `skipped_pages`.
- Existing five tests unchanged and green (`test_header_rules_and_text_only_pages_yield_nothing` is the regression guard for text-only pages).

**Python, `scripts/tests/test_research_intake.py`:**

- `test_text_only_candidate_on_a_page_with_catalogue_figures_is_held`: reviewer returns `selection(figure_ids=[], text_only=True, captions={}, pages=[1])` with the fixture catalogue holding `f1` on page 1; expect no post, one audit entry `held == "TEXT_ONLY_WITH_FIGURES"`, `publisher.stored == []`, and exactly one model call (no VERIFY). Red on `main` (publishes text-only).
- `test_text_only_candidate_on_chart_free_pages_still_publishes`: same selection but the catalogue figure sits on page 2 and the candidate cites page 1; expect the existing `test_text_only_finding_publishes_without_images` behaviour.
- `test_operator_note_requeue_reaches_select_with_figure_guidance`: unchanged behaviour, assert the prompt still carries the REVISE text so the Held vote path closes the loop.
- `test_research_outcomes.py`: `outcome_row` surfaces `TEXT_ONLY_WITH_FIGURES` in `reason_codes` and `figureCount > 0`.

**Web, `web/tests/research-held-ui.test.tsx`:** one assertion that `reasonCodeLabel("TEXT_ONLY_WITH_FIGURES")` renders the new label on a Held card. No changes to `newsfeed-research-markdown.test.tsx` or `newsfeed-research-media.spec.ts`.

**Commands:** `PYTHONPATH=scripts python3.13 -m pytest scripts/tests/test_research_figures.py scripts/tests/test_research_intake.py scripts/tests/test_research_outcomes.py -q`, then `python3.13 scripts/run_pytest_affected.py --files scripts/research/figures.py scripts/research/intake.py -- -q`; `cd web && npx vitest run tests/research-held-ui.test.tsx`.

**Offline replay (evidence for the PR body):** `PYTHONPATH=scripts python -m research.harness --root /var/lib/radon/research --labels <labels.json>` before and after, reporting: documents with a non-empty catalogue, total figures, published items with `images`, held items by reason code. The JPM document re-run through `process()` on a scratch branch must yield a post with `images` for Fig 1 to 4 (or the subset SELECT attaches) and `source.figures[*].page` in `{2, 4}`.

---

## 7. Implement PR: labels and Done when

Branch `fix/pdf-chart-evidence-not-text-only` off `main`. Labels: `research`, `bug`, `hold-for-joe`. Draft until Joe says ship. PR body carries: section 2 step 0 and 1 answers, the gate that fired (step 2), before and after harness counts, and the rendered JPM crops.

- [ ] Section 2 steps 0 to 2 answered in the PR body with the `review.json` `figures` value and the page frame readout for pages 2 and 4.
- [ ] `figures.detect` normalizes against `page.get_bbox()`; cases C and D from section 3 detect one figure each; the crop pixel test passes.
- [ ] `figures.detect` accepts image-bearing clusters below `MIN_OBJECTS`; case B and F detect; a 95% page image does not; `kind` is present on every figure.
- [ ] Rotation: either the displayed-frame transform ships with its pixel test, or rotated pages are listed in `skipped_pages` with `reason: "rotation"`.
- [ ] `intake._process` holds `TEXT_ONLY_WITH_FIGURES` when a text-only candidate cites a page with catalogue figures; chart-free text-only items still publish; two model calls per document maximum.
- [ ] `SELECT_INSTRUCTION` gains the one sentence from P3 and the catalogue line includes `kind`.
- [ ] `web/lib/researchReasonCodes.ts` has the new label; `research-held-ui` test covers it.
- [ ] `publish.py` untouched; `test_research_publish.py` green.
- [ ] New tests listed in section 6 were red on `main` (commit the failing test first, fix second).
- [ ] Focused suites green: `test_research_figures`, `test_research_intake`, `test_research_outcomes`, `test_research_pipeline`, `test_research_pdf`; `run_pytest_affected` green; web `research-held-ui` green; `newsfeed-research-markdown` and `newsfeed-research-media` unchanged and green.
- [ ] Harness replay before and after in the PR body; the JPM document produces a post with `images` from pages 2 or 4 on the scratch run.
- [ ] `docs/dropbox-research.md` Intake v2 paragraph updated (raster clusters, displayed-frame detection, `TEXT_ONLY_WITH_FIGURES`, `figure_gaps`) and `docs/research/agent-evidence-and-controls.md` gains one line that chart crops come from PDFium page objects in the displayed frame. `docs/owners.json` has no rule for these paths, so the docs update is the owner update; no `docs-skip`.
- [ ] `docs/design-shots/pdf-chart-evidence/after/` crops of the JPM pages committed as evidence.
- [ ] CI green on the head SHA; no merge until Joe says ship.

---

## Considered but rejected

| Candidate | Rejected because |
| --- | --- |
| Let the model propose crops when the catalogue is empty (v1 `prepare_figures` style fallback) | Violates the deterministic-detection constraint; crop coordinates would again be model-invented and need the inspection and correction loop v2 removed. |
| Auto-attach every catalogue figure on cited pages with `title` as the caption when SELECT says `text_only` | Attaches unrelated charts on multi-chart pages and bypasses SELECT's "directly supports the finding" rule. Holding for the operator keeps the human decision and reuses the existing vote path. |
| One SELECT re-ask when `text_only` collides with catalogue figures | Third model call per document; v2's budget is two. Revisit only if harness shows the hold rate is high after P3. |
| Lower `MIN_OBJECTS` to 1 for all clusters | Every header rule and logo path becomes a figure candidate; `test_header_rules_and_text_only_pages_yield_nothing` would fail. Image-aware acceptance is the narrow version. |
| Render with `page.set_rotation(0)` to avoid the rotation transform | Produces sideways crops for landscape-by-rotation pages; the reader needs the displayed orientation. |
| Change the footer wording or hide the label in `DashboardNewsFeed.tsx` | The label is correct for the stored post; the post is wrong. Fixing the UI hides the evidence gap. |
