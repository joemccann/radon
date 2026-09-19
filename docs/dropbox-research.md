# Dropbox research ingestion

The operator approved the relevance policy and chart format on 2026-09-07. Joe locked on 2026-09-13 that Zero Hedge / intermediary market recaps are valuable: do not empty-hold solely for Tyler Durden/ZH wrap provenance, a missing original bank PDF, or omnibus aggregator shape. `scripts/research/policy.md` is the durable selection contract. Charts are original PDF regions with complete axes/legends, a lead preview, secondary thumbnails and the existing feed lightbox. PDF pages are supplementary evidence. Source report dates are distinct from folder and publication dates; a complete aggregator byline date is valid publication-date evidence. Feed attribution names the sourced desk or person, or the best allowable ZH-safe form (institutional desk commentary); generated titles, bodies, captions and tags omit ZeroHedge branding. The publisher rejects copy that names ZeroHedge.

## Components and data boundary

- `research.dropbox`: offline OAuth refresh, account/namespace/folder identity checks, recursive per-date cursors and bounded read-only downloads. Only `/Joe McCann/Current` is accepted. No Dropbox writes.
- `research.state`: private SQLite discovery, revisions, retries and outbox. Cursor advancement and queue insertion are transactional; remote publication acknowledgement follows successful commit. Revisions supersede unfinished work. The daemon watches the current day; one-shot discovery also checks previously watched scopes. Existing historical queue entries remain eligible for processing.
- `research.pdf`: local Firecrawl `pdf-inspector==1.17.0`, PDFium `5.13.0`, Pillow `12.3.0`. Extraction and rendering run in bounded subprocesses. PDFs over 100 pages or 100 MiB are held rather than partially published. Image-only pages receive visual review.
- `research.pipeline`: tool-free multimodal selection and independent claim/crop verification via the shared HTTP model ladder (`scripts/clients/model_ladder.py`). Default model `claude-sonnet-4-6` (override `RADON_RESEARCH_MODEL`). Provider order matches CTA vision: Anthropic → Grok → Cursor (unwired) → Codex → Gemini → NVIDIA → Cerebras; subscription-tier rungs use consumer OAuth/auth.json only by default (prepaid wallets require ``RADON_LADDER_ALLOW_PREPAID=1``); credit/billing/quota/auth/5xx fall through to the next keyed rung (NVIDIA before Cerebras when `NVIDIA_API_KEY` is set). Missing dates, incomplete charts, source conflicts and duplicate claims fail publication. Crops receive a separate crop-only inspection with visible label transcription before claim review. A failed crop gets at most one correction against its original page, aided by PDFium glyph bounds only for verified unrotated zero-origin frames, and must pass a fresh crop-only inspection. Source content never supplies executable instructions or destinations. Every numeric assertion must also match a literal quote from a cited source page, with numeric values, currencies and units checked locally. Source grounding normalizes only paired Markdown strong-emphasis wrappers; it preserves numerical meaning and rejects changed signs, currencies or units. Range endpoint excerpts retain their interpretation in the full source and proposal. Explicitly selected date-bearing pages are supplied to final verification within the existing eight-page evidence cap. Automated publication also requires a complete, unambiguous report, publication or aggregator-byline date quoted literally from a cited page and checked locally against the proposed document date. The reviewer must independently confirm its date role. Folder dates, filenames, copyright years, event dates and coverage ranges do not establish publication dates; undated reports are held. A complete aggregator byline date is preferred over holding for a missing original bank report date. Image-only numeric claims and unsourced calculations are held; the human-approved seed retains its separately verified calculations. A 90-day feed corpus is read in bounded pages; comparison uses a lexical/recent shortlist, not a claim of exhaustive semantic novelty.
- `research.publish`: validated assets and atomic `posts` plus `research_post_sources` writes over bounded Turso HTTP. Reserved `research-` IDs remain idempotent across outbox retries. No competing posts.json writer.
- Private files live in `RADON_RESEARCH_DIR/assets`, default `/var/lib/radon/research/assets` inside containers, outside public media. The host bind source is `/var/lib/radon-private/research`, beneath a root-owned `0700` anchor; the child is owned by radon with mode `0700`. Worker mount is read/write; API mount is read-only. The authenticated Next route bypasses image optimization and caches. Research rows are excluded from demo reads and the public demo mirror.

## Runtime setup and activation

Existing private runtime credentials are `DROPBOX_APP_KEY`, `DROPBOX_REFRESH_TOKEN`, `DROPBOX_ACCOUNT_ID`, `DROPBOX_ROOT_NAMESPACE_ID`, `DROPBOX_FOLDER_ID`, `DROPBOX_FOLDER_PATH`. Keep them and provider/Turso credentials in `/etc/radon/env`; never commit or paste their values.

Deployment order is mandatory:

1. Apply migration `0071_research_post_sources.sql` before deploying the joined feed query (the API container already runs the migration runner at startup).
2. Deploy source-aware feed, authenticated media route, demo exclusion and the Python image with pinned PDF dependencies.
3. Provision the private research directory via the control-plane runtime helper. The optional `radon-research.service` is installed but is not implicitly enabled on hosts without configured credentials.
4. Import and verify the private approved calibration batch. `PYTHONPATH=scripts python3.13 -m research.worker --root <private-root> --seed-reviewed <private-batch>` creates a durable outbox without publication; rerunning adds no duplicate work. Batch images require `image_sha256` and PDFs retain source hashes. Bundles must use paths within their private import parent.
5. Run the same seed command with `--publish` only after the private endpoints and provenance schema are active. Verify six accepted items and charts against their source pages.
6. Set `RADON_RESEARCH_PUBLISH=1` in the private runtime configuration, then enable/start `radon-research.service`. The default is dry-run/outbox preparation. No further per-item approval is required after calibrated automation is enabled.

`python3.13 -m research.worker --daemon` polls the current-day directory every 60 seconds, using America/New_York for automatic date rollover. Discovery, PDF download/text extraction, and model review/publication run in separate bounded processes. Poll scheduling is independent of long model reviews. New arrivals take priority over older backlog and retries for extraction; parsed documents wake the reviewer immediately, and accepted results flush after each document. A busy parser completes its bounded current operation before taking the next document; the poll interval is a detection target, not a guarantee that a large PDF finishes parsing or publishes within a minute. SIGTERM stops the workers within a bounded grace period and terminates remaining process groups; durable state recovers interrupted claims on restart. Dropbox rate limits override the poll cadence and are shared between listing and download workers. Transient failures back off; after six processing attempts an item is held with a recorded error classification. Discovery, extraction and review each expose stage health. Aggregate local and remote health remains in error while any stage reports an error. Turso telemetry runs separately with a bounded timeout, so reporting outages cannot block discovery. Parsed PDF and extracted Markdown caches are integrity-checked before reuse. Journal output contains stage counts/error classes only. Completed pipeline stages refresh their own health; both freshness catalogs monitor the worker continuously with a 15-minute window. Document review uses a 30-minute lease and reserves the reviewer call timeout before starting another call. Direct crop review initializes the same lease lazily; later correction attempts retain its original deadline. Each `process()` invocation starts a fresh document lease and clears it on exit. Expired document work is retried through the durable queue.

## Intake v2 (`RADON_RESEARCH_PIPELINE=v2`)

`research.intake.Pipeline` replaces the v1 orchestration behind an environment switch (`research.model.build_pipeline`). Per document: `research.identify` (publisher from the Dropbox folder, series from the filename, document type by rule, report-date ladder text → PDF `CreationDate` → Dropbox `client_modified`, never a hold), `research.triage` (recall-first: only FX pair notes, economic calendars and an explicit series denylist drop, each with a reason code), `research.novelty` (64-bit simhash of the whole document against `fingerprints.json` of published documents), `research.figures` (charts from PDFium page-object clusters; title and source line attached when found, never required), one text-only SELECT call (`Reviewer.ask_text`, override the rung with `RADON_RESEARCH_TEXT_MODEL`) that receives the identity facts, the figure catalogue by id and the full page text, `research.ground` (every number, date, period, tenor and figure reference in the copy looked up on the cited pages by code; no model quotes), then one VERIFY call per grounded candidate with at most the attached crops. Provider outages (`research.model.is_provider_outage`) park the work item for `PROVIDER_PARK_SECS` without consuming an attempt. `evidence/<key>/review.json` carries `pipeline: v2`, the identity block, the triage decision, the figure catalogue and one audit entry per candidate with `held` reason codes (`INVALID_CANDIDATE`, `NUMBER_NOT_ON_PAGE`, `VERIFY_FAILED`) or the post. Provenance gains `publisherSource`, `dateSource`, `dateQuote`, `series` and `pipeline`.

## Offline replay harness

`research.harness` scores any per-document stage function against the cached private corpus without network or model calls. It reads `state.sqlite` read-only, joins the golden set (published outbox posts plus operator scope labels, `triage-labels-<date>.json` keyed by the 16-character work-key prefix) and reports positive recall, in-scope drops, out-of-scope drops and model calls per document. `--baseline` scores the v1 production audits from `evidence/<key>/review.json`. Run it where the private directory is readable, i.e. inside the research container: `PYTHONPATH=scripts python -m research.harness --root /var/lib/radon/research --labels <labels.json> --baseline`. Baseline 2026-09-19: 633 live documents, 3,373 model calls, 1,194 candidates, 37 documents with posts, 73 quota holds, 94% of in-scope documents reached the model. Every v2 stage lands with an `evaluate()` runner and its measured exit criterion.

## Verification and limits

Focused tests cover account/path enforcement, cursors, restart/revision handling, outbox replay, asset/hash privacy, atomic publication, source metadata, visual review gates and browser chart interactions. Native rendering has CPU/memory bounds on Linux and wall-clock/pixel bounds everywhere.

Model-reviewed publication remains probabilistic: every proposal needs a second source/figure review and a recorded audit. The accepted calibration batch is a reference, not proof that future model selections match human judgement. Chart crop failures after the bounded correction are held; original pages and failed attempts are retained. Unresolved failures require inspection of private `evidence/<work-key>/review.json`, queue state and `health.json`. A corrected file revision is ingested anew. A whole-document duplicate may reach selection; claim-level comparison suppresses repeated publication.

Provider references: [Dropbox change detection](https://developers.dropbox.com/detecting-changes-guide), [Claude vision messages](https://platform.claude.com/docs/en/build-with-claude/vision).

### Model ladder (shared HTTP)

New multimodal or text-JSON model callers must use `scripts/clients/model_ladder.py` (import `complete_text_json`, `complete_multimodal_json`, or `extract_via_vision`). Text-JSON responses stream through a 2 MiB cap and close before parsing on every provider rung. Do not add a third cascade. `scripts/clients/vision_cascade.py` is a thin re-export for CTA/MenthorQ. The newsfeed text tagger reaches the same helper through `scripts/clients/model_ladder_cli.py`. Weekend bash CLI ladders (`RADON_WEEKEND_MODEL_LADDER` in `scripts/*_weekend.sh`) remain separate subprocess rungs; see `scripts/tests/test_weekend_model_ladder.py`.

#### Auth matrix (research / CTA / knowledge / newsfeed)

Knowledge distill (`scripts/knowledge/distill.py` → `complete_text_json`) and the newsfeed tagger (`model_ladder_cli.py`) share this ladder with research review and CTA vision. Subscription-tier rungs (Anthropic / Grok / Codex / Gemini) use **subscription credentials only** by default — prepaid console wallets that hit `credit_balance` are skipped unless `RADON_LADDER_ALLOW_PREPAID=1`. Inject secrets via `/etc/radon/env` (mode `0640` `root:radon`); never commit values. Composer is the coding agent, not a ladder rung. Cerebras and `radon-knowledge.timer` stay paused on Hetzner until Joe yes.

| Rung | Auth (default) | Prepaid escape (`RADON_LADDER_ALLOW_PREPAID=1`) | Notes |
|------|----------------|--------------------------------------------------|-------|
| anthropic | `CLAUDE_CODE_OAUTH_TOKEN` / `CLAUDE_CODE_OAUTH_TOKEN_FILE`; Linux `~/.claude/.credentials.json` (or `CLAUDE_CONFIG_DIR`) | `ANTHROPIC_API_KEY`, `CLAUDE_CODE_API_KEY`, `CLAUDE_API_KEY` | OAuth uses Messages + `anthropic-beta: oauth-2025-04-20`. Sibling to weekend wrappers that *unset* prepaid so Claude Code bills claude.ai. Skip rung when no subscription material. |
| grok | `~/.grok/auth.json` (device-auth) | `XAI_API_KEY`, `GROK_API_KEY` | Skip rung when auth.json absent (default). |
| cursor | — | — | Unwired (no vision HTTP path). |
| codex | `${CODEX_HOME:-~/.codex}/auth.json` ChatGPT+Codex OAuth `tokens.access_token` | `OPENAI_API_KEY` | Skip rung when auth.json absent (default). |
| gemini | `GEMINI_OAUTH_TOKEN`, `GOOGLE_OAUTH_ACCESS_TOKEN` | `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_GENAI_API_KEY` | Skip rung when no OAuth token (default). |
| nvidia | `NVIDIA_API_KEY` (always OK) | — | First-class before Cerebras when keyed. **Text** default `nvidia/nemotron-3-super-120b-a12b` (override `NVIDIA_TEXT_MODEL` / `NVIDIA_MODEL`). **Vision** default `meta/llama-3.2-90b-vision-instruct`; on timeout/error falls back to `meta/llama-3.2-11b-vision-instruct` (`NVIDIA_VISION_MODEL`). |
| cerebras | `CEREBRAS_API_KEY` | — | Last rung. Leave paused; prepaid auto-reload stays OFF. |

Hetzner hosts that only mount prepaid Anthropic/XAI/OpenAI keys will skip those rungs and use NVIDIA when keyed — they will not burn prepaid wallets then fall through to Cerebras. Mount subscription tokens for fuller band coverage.

Auth-file API keys obey the same prepaid opt-in as environment keys. Codex
`OPENAI_API_KEY` and Grok `api_key`, `apiKey`, or `credentials.api_key` are
classified as `api_key` only with `RADON_LADDER_ALLOW_PREPAID=1`; they do not
wire a subscription rung by default. Codex `auth_mode=apikey` also prevents
stale OAuth fields in that file from being selected. Supported OAuth access
token fields remain subscription credentials.

### Verified host placement

As of 2026-09-08 the worker runs as `radon-research.service` in the Python app container on 5.78.148.38. That host has `RADON_HOST_ROLE=app` and routes broker access to 10.0.0.4; its legacy Linux and Hetzner hostname remains `ib-gateway`. The service does not run in the broker Gateway container.

Subscription Responses streams must carry a `response.completed` event whose
response status is `completed` before the shared Python ladder or web adapter
accepts text. EOF, failed/incomplete events and malformed terminal data discard
partial output, including otherwise valid reviewer JSON. Failure messages omit
provider payloads; configured provider fallback remains available. Python keeps
its existing byte limit and response-close guarantee; the web adapter consumes
the response body before validating completion.
