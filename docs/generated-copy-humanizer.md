# Generated-copy Humanizer gate

Radon applies the user's visible Humanizer scoring rubric and all 24 qualitative
checks to generated news and social copy. The score is calculated from exact
text spans; a model cannot supply its own passing score. Publication requires
at least 90 and no remaining qualitative finding. The unseen rubric pages have
no invented numerical weights. This is an editorial assessment, not a claim
about human authorship.

## Covered paths

| Surface | Generation instructions | Final enforcement |
| --- | --- | --- |
| MarketDesk articles | `scripts/research/marketdesk_copy.py` | Shared Python review, including captions; publisher validates exact-copy certificate |
| PDF research articles, both pipeline versions | `scripts/research/policy.md` | `summary_editor.py` reviews titles, bodies and captions; publisher validates certificate |
| Newsfeed Explain | `web/lib/newsfeedExplain.ts` | Local checks plus model review before caching; cache keys include rubric version |
| CTA social copy | `scripts/utils/cta_llm.py` | Source fidelity, shared scored review, bounded rewrite, final certificate; failure uses deterministic template |
| Operational assistant prose | Shared draft instructions in assistant system prompt | Negative-parallelism and repetition checks, one bounded regeneration; a remaining style finding still answers, only repetition takes the existing error path |
| Generated X watchlist reasons | `scripts/fetch_x_xai.py` | Full rubric in generation instructions and self-review; quoted source text remains unchanged |

Closed-vocabulary tags, numeric extraction, internal knowledge records, original
source quotes and chart pixels are data, not newly authored publication copy.
User edits to an accepted share caption remain under the user's control.

## Publication and failure behavior

Detectable negative parallelisms block even when a reviewer returns empty
findings: `X, not Y`, `not X but Y`, and adjacent-sentence variants. Real numeric
corrections and source uncertainty remain valid. Local checks supplement the
contextual reviewer rather than claiming to recognize every writing defect.

Python certificates hash title, body and every displayed figure caption. A
changed caption or old rubric version invalidates the certificate. Captions are
immutable during editorial rewrites; a failed caption holds the article for
upstream regeneration. Numeric and semantic checks run before the final
editorial review of a rewritten body. Pending outbox records must be recertified
under the current rubric.

Newsfeed Share rewriting was retired on 2026-10-29. Share captions and media
use sanitized original article copy without a model call or a new editorial
review. Compose on X and caption editing are available while media renders.
The protected legacy rewrite endpoint returns `410` with `Cache-Control: no-store`.
Explain still generates and reviews its own prose; its cache keys include the
rubric version and its total deadline covers generation and review together.

Audits remain private: research audit files and CTA stderr records never enter
published prose. Web review failures are returned through the existing error
paths. Already published database rows are not silently changed by deployment;
any correction requires review and an exact-row update preserving provenance.

## Regression evidence

Tests cover the reported DXY sentence, sentence-split variants, false clean
verdicts, unchanged findings, computed scores, malformed reviews, caption
mutation, stale certificates, semantic direction changes, provider outages,
full captions, the retired rewrite endpoint and immediately available sharing.
`web/e2e/newsfeed-sharing.spec.ts` covers desktop/mobile sharing and screenshots.
