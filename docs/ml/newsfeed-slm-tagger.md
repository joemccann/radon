# Newsfeed text-tagger SLM v1: build spec

Status: IMPLEMENTED (code + fixture bakeoff; live Turso extract / Mini train / serve-host latency unmeasured). Rung stays `off`. Merged only by Joe.

Fixture-limited bakeoff (n=5 synthetic posts; not gold_human; G0 unmeasured):

### test.jsonl / gold_human.jsonl (same fixture)

| metric | A | B | C |
|---|---|---|---|
| n | 5 | 5 | 5 |
| micro_f1 | 1.0000 | 0.6667 | 0.9333 |
| macro_f1 | 1.0000 | 0.5700 | 0.9048 |
| rare_label_recall | 1.0000 | 0.5000 | 0.5000 |
| invalid_rate | 0.0000 | 0.4000 | 0.0000 |
| exact3 | 1.0000 | 0.4000 | 0.8000 |
| jaccard | 1.0000 | 0.5000 | 0.9000 |
| human_accept | 1.0000 | 0.2500 | 1.0000 |
| latency_p50_s | 1.2000 | 3.1000 | 2.1000 |
| latency_p95_s | 1.3800 | 3.3600 | 2.2800 |
| throughput_ppm | 50.0000 | 19.2308 | 28.5714 |

Verdict: `C FAILS G0,G2,G3,G7,G8`. `RADON_SLM_TAGGER_MODE` stays `off`. B0 and D.4 host_metrics are unmeasured (no Turso / no serve host in this VM).

Operator extract on a credentialed host:

    python3.13 scripts/newsfeed/slm/extract_cli.py --out data/slm/tagger/v1

Honesty label, to be carried verbatim on the model card, the service unit description, the ladder log prefix and any UI or PR text that mentions the model:

> Radon SLM tagger: internal specialist, not a SotA replacement for open research.

(The label is rendered with a colon, never an em dash; see the copy rule in `CLAUDE.md`.)

Prior research this spec reconciles with and does not repeat:

- [`docs/research/radon-small-model.md`](../research/radon-small-model.md), sections 2 and 5: the tagger alone is worth tens of dollars a year, so the dollar case is thin; a bounded Phase-1 distillation experiment on a 0.8B to 4B model for tagging is still recommended.
- [`docs/research/radon-harness-and-model-roadmap.md`](../research/radon-harness-and-model-roadmap.md), roadmap row M1: a local tagger behind the provider layer, callable as a narrow tool, never the decision surface.

This spec is that M1 rung, scoped to the Market Ear **text** tagger only.

Additional input folded in 2026-09-19 (Joe via CoS): a QLoRA practitioner guide ([x.com/sairahul1/status/2100882424343265527](https://x.com/sairahul1/status/2100882424343265527)). Its example task and its unverified "beats 70B" claims are ignored. Eight of its rules are adopted as **hard requirements** and are marked `[HR-1]` to `[HR-8]` where they bind:

| Id | Requirement | Binds in |
|---|---|---|
| HR-1 | Three-arm bakeoff before cutover: (A) current ladder, (B) prompt-only `Qwen/Qwen2.5-1.5B-Instruct`, (C) QLoRA-tuned 1.5B. Cutover only if C wins on F1 **and** on cost/latency vs A, and beats B | F.1, F.3, I.2 |
| HR-2 | Default base is `Qwen/Qwen2.5-1.5B-Instruct`; upgrade size only if eval fails acceptance | D.2 |
| HR-3 | Constrained output: exactly 3 uppercase kebab tags, validated against the taxonomy, with an abstention path; invalid or empty output abstains and falls through the ladder. Never free text | H.3, H.4 |
| HR-4 | Split by time and/or publisher, never a random shuffle | C.3 |
| HR-5 | Fine-tuning encodes tagging behaviour only; taxonomy and policy stay in versioned data and rules; shifting news topics are not baked into weights | C.2, D.3, J |
| HR-6 | Gates are micro-F1, macro-F1, rare-label recall, invalid-output rate, latency and throughput vs the ladder; train loss alone is never a gate | F.2, F.3 |
| HR-7 | Serve as a GGUF (or vLLM) sidecar; after deploy monitor drift, empty or invalid tags, and label-distribution shift | G, I.3 |
| HR-8 | Training data is Turso-owned labelled posts only; no scraped third-party corpora | C.1, C.6 |

---

## A. Problem, honesty, non-goals

### The job

`scripts/newsfeed/tagger.js` labels every Market Ear post with exactly 3 UPPERCASE kebab tags. It runs on every post (text tagger always; vision tagger additionally when the post has an image; see `hydrateTagsDual` in `scripts/newsfeed/vision_tagger.js`). Volume is roughly 20 to 25 posts per day (3,474 posts over about five months as of 2026-07-20, `tasks/knowledge-base-plan.md`). Each post costs one walk of the shared model ladder.

### Why do this at all, in honest order

1. **Ladder pressure relief and Cerebras-last hedge.** Every text tag today spends subscription quota (Anthropic, Grok, Codex, Gemini) or falls to NVIDIA, then Cerebras (paid, last). The tagger shares that ladder with knowledge distill and the research reviewer. Taking the highest-volume, lowest-difficulty caller off the paid and subscription rungs leaves quota for the callers that need frontier quality. When every keyed provider is down or quota-exhausted, posts land untagged (`tagPost` soft-fails to `null`); a local rung keeps the dashboard chips populated.
2. **Determinism and format validity.** A grammar-constrained local model returns `{"tags": [a, b, c]}` every time, and the rung validates every tag against the live taxonomy before Node sees it (HR-3). The current path sometimes returns prose or fewer than 3 tags and is dropped by `normaliseTags(...).slice(0, 3)`.
3. **Latency, conditionally.** On Apple silicon or a 4+ vCPU host a 1.5B model answers in 1 to 4 seconds. On a 2 vCPU shared VPS it can be slower than a subscription API call. Latency is a win only where the hardware permits; section D makes the host choice explicit and section F gates enablement on measured p95.
4. **Reusable pattern.** Dataset extract, LoRA on the Mini, GGUF export, eval harness, sidecar unit, ladder rung. Distill and reviewer heads (phase 2) reuse the pattern; they are OUT of this PR and OUT of the implement PR.

### What this is not

- Not a monetization product. No selling, licensing or publishing of the model or the corpus.
- Not a replacement for the vision tagger (`vision_tagger.js` keeps its Anthropic path; the NVIDIA vision fallback is unchanged).
- Not a research assistant, distill head, reviewer head or general chat model.
- Not a claim of "better than the frontier model". The only claim allowed is the task-specific one in section F: arm C beat arm A on F1 and on cost and latency, and beat the prompt-only base, on the held-out gold set, with the confidence interval printed. Until that table exists the rung is `off`.
- Not a producer of new taxonomy entries. The SLM rung is closed-vocabulary; only the ladder coins tags.
- Not a dollar-savings story. The saving is tens of dollars a year; write that number, not "cost reduction".

---

## B. Current architecture map (verified 2026-09-19)

```
scripts/newsfeed/index.js  (radon-newsfeed.service, Hetzner, 120 s loop)
  └─ cycle.js ── hydrateTagsDual(posts, {textTagger, visionTagger})
        ├─ textTagger  = createTagger(...)            scripts/newsfeed/tagger.js
        │     tagPost(post)
        │       system      = buildSystemPrompt(taxonomySnapshot)   (rules + full taxonomy list)
        │       instruction = "Title: ...\nBody: <=1500 chars"
        │       completeViaLadder() ── spawn python3.13 scripts/clients/model_ladder_cli.py
        │             stdin  {system, instruction, accept:"tags", max_tokens:800, timeout:30}
        │             stdout {ok, data:{tags:[...]}, provider, model}   (soft-fail {ok:false})
        │                └─ clients.model_ladder.complete_text_json(accept=accept_tags_payload,
        │                                                            log_prefix="newsfeed-tagger")
        │                      _run_ladder over MODEL_LADDER_ORDER:
        │                        anthropic -> grok -> cursor(unwired) -> codex -> gemini -> nvidia -> cerebras
        │                      subscription creds only for the first band unless RADON_LADDER_ALLOW_PREPAID=1
        │       normaliseTags(parsed.tags).slice(0,3); exactly 3 or null
        └─ visionTagger = createVisionTagger(...)      OUT OF SCOPE (Anthropic direct)

  post.tags_text   = 3 tags from the text tagger            <- the SLM replaces THIS producer
  post.tags_vision = 3 tags from the vision tagger
  post.tags        = enrichWithParentTags(union(tags_text, tags_vision))   (dashboard-facing, may exceed 3)

Persistence: web/public/data/posts.json (runtime, gitignored) + Turso `posts`
             (columns tags, tags_text, tags_vision; migration 0001).
Taxonomy:    data/tag_taxonomy.json (runtime-owned, UNTRACKED since f88b413a 2026-07-15; 212 tags at that commit)
             + Turso `tag_taxonomy` (canonical). New tags are appended by the caller, never by the tagger.
Backfill:    scripts/newsfeed/backfill_tags.js [--retag], ~24 posts/min, dual-classifies, dual-writes.
Other ladder callers (must not be touched): scripts/knowledge/distill.py (accept_distill_payload),
             the research reviewer (complete_multimodal_json), CTA vision cascade (extract_via_vision).
```

Two facts to correct in the surrounding docs during the implement PR, because the tagger contract depends on them: `scripts/newsfeed/CLAUDE.md` and `scripts/newsfeed/AGENTS.md` still say `data/tag_taxonomy.json` is force-tracked; root `CLAUDE.md` is right (untracked, runtime-owned, Turso canonical).

The SLM replaces the **producer of `tags_text`** and nothing else. `tags`, `tags_vision`, the union, parent-tag enrichment, the taxonomy append and the dashboard chips are unchanged.

---

## C. Dataset

### C.1 Source of truth and where to run the extract

The corpus is not in the repository and cannot be counted from a cloud VM (probe 2026-09-19: no `posts.json`, no `tag_taxonomy.json`, no Turso credentials in the checkout; test fixtures hold 1 to 3 synthetic posts each). The extract runs on a host with Turso read credentials (Hetzner or the Mac Mini with `.env`).

Turso `posts` is canonical. `web/public/data/posts.json` plus `RADON_NEWSFEED_ARCHIVE_DIR` is the disk fallback and must give the same rows after rollover archives are merged.

`[HR-8]` The training set is drawn from Turso `posts` rows that Radon itself labelled, and from nothing else. No scraped third-party corpus, no public tagging dataset, no synthetic posts generated from outside news, no augmentation by paraphrasing with an API model. `build_dataset.py` has exactly one input (the `posts` query below) and a test asserts the manifest's `sources` field equals `["turso.posts"]`.

### C.2 Extract recipe (`scripts/newsfeed/slm/build_dataset.py`, new)

Read with an id cursor, never one unbounded SELECT (Hrana rule 1 in `scripts/CLAUDE.md`):

```sql
SELECT p.id, p.title, p.content, p.timestamp, p.tags, p.tags_text, p.tags_vision, p.images,
       CASE WHEN s.post_id IS NULL THEN 0 ELSE 1 END AS is_research
FROM posts p
LEFT JOIN research_post_sources s ON s.post_id = p.id
WHERE p.id > ? ORDER BY p.id LIMIT 200;
```

Row rules, applied in this order:

1. `is_research = 1` rows are excluded. They are Joe's private research posts (migration 0071), a different distribution and a different provenance. v1 is the Market Ear tagger.
2. Label = `tags_text` when it is a JSON array of exactly 3 strings that survive `normaliseTags` unchanged. Rows with no `tags_text` are excluded; `tags` is a union with vision tags and parent tags and is not the text tagger's contract. Do not fall back to `tags`.
3. Input = `Title: {title}\nBody: {content[:1500]}`, byte-identical to `buildUserPrompt` in `tagger.js`. Import the truncation constant rather than re-typing it; pin it with a test.
4. Deduplicate on `sha256(title + "\n" + content)`. Market Ear re-publishes; keep the earliest `timestamp`.
5. Drop rows whose body is under 40 characters after whitespace collapse (title-only stubs carry no signal for a text model).
6. Record `label_source = "ladder"` and, when the ladder provider is known for the row (it is not stored today; see C.5), the provider name. Never train a future version on rows the SLM itself produced (`label_source = "slm"`); the shadow table in section I carries the provenance that makes this filter possible.

Output (all under `data/slm/tagger/v1/`, gitignored, never committed, never synced to media or B2 public prefixes):

```
data/slm/tagger/v1/
  train.jsonl      chat format: [{"role":"system","content":SLM_SYSTEM},{"role":"user","content":input},{"role":"assistant","content":"{\"tags\":[...]}"}]
  valid.jsonl
  test.jsonl       time-split held-out (C.3); never read by the trainer
  gold_human.jsonl the 200-post human-ok slice (C.4)
  manifest.json    {rows, split_counts, taxonomy_size, taxonomy_sha256, cutoff_dates, extract_sha256, extracted_at}
```

`SLM_SYSTEM` is one fixed string (section H.3). The training pairs use it; the ladder rung sends it. The full taxonomy list is deliberately not in the SLM prompt: the prompt stays short enough for CPU inference, and vocabulary correctness is enforced at serve time by validation against the live taxonomy (H.3), not by the prompt.

`[HR-5]` What the weights are allowed to learn: the mapping from a post's text to three tags, and nothing else. Concretely: loss is masked to the assistant JSON (`mask_prompt: true`), so no post body is ever a training target; there is no unsupervised or continued-pretraining pass over post text; the taxonomy is not a training artifact but a validation input read at call time (Turso `tag_taxonomy` is canonical; `data/tag_taxonomy.json` is its runtime mirror; the snapshot hash used at eval time is recorded in `manifest.json` as `taxonomy_sha256`); the tagging policy (priority order, VOL vs VIX, GAMMA means dealer gamma) lives in `buildSystemPrompt` in `tagger.js` and is what produced the labels. A policy change therefore means relabel with the ladder and retrain, never a prompt patch on the SLM. A new topic in the news is handled by the ladder coining the tag and the taxonomy growing; the SLM abstains on it (H.3) until the next retrain.

### C.3 Splits: time only (`[HR-4]`)

Random splits leak near-duplicate posts across train and test and flatter the score. A random shuffle is forbidden anywhere in `build_dataset.py`; a test asserts `max(train.timestamp) < min(valid.timestamp) <= max(valid.timestamp) < min(test.timestamp)`. Split on `timestamp`:

| Split | Window | Purpose |
|---|---|---|
| test | most recent 60 days at extract time | held-out gold for section F; frozen with its cutoff dates in `manifest.json` |
| valid | the 30 days before test | early stopping, hyperparameter selection |
| train | everything older | training |

Expected sizes from the last verified count (3,474 posts at 2026-07-20, about 20 to 25 per day): roughly 4,500 total by 2026-09-19, of which test is about 1,200 to 1,500 and train about 2,500 to 3,000 after exclusions. If train drops below 1,500 usable rows, shrink test to 45 days before touching valid. Report the actual counts in the implement PR body.

Publisher axis: Market Ear is the only publisher in v1 (research posts are excluded by C.2 rule 1, and `research_post_sources.provenance_json` carries a `publisher` field). If a second publisher is ever admitted to the corpus, the split becomes time **and** publisher: hold the newest publisher out of train entirely and report its metrics as a separate row, so a score is never inflated by publisher-specific phrasing seen in training.

### C.4 Quality bar (three tiers, all reported separately)

| Tier | Definition | Use |
|---|---|---|
| machine | any row passing C.2 | train, valid, most of test |
| dual-agree | row where `tags_text` and `tags_vision` both exist and share at least 2 of 3 tags, OR the ladder re-tag run (C.5) reproduces at least 2 of 3 | preferred subset of test; the "stable tags" signal |
| human-ok | 200 posts from the test window, each shown to Joe with the ladder's 3 tags and a yes/no plus optional corrected tags; stored in `gold_human.jsonl` with `reviewed_at` | the only slice the word "gold" is allowed to mean without qualification; drives gate G6 |

Sampling for human-ok: 200 posts stratified by month across the test window, at least 40 with images (so chart-pattern vocabulary is represented), no research posts. Tooling: a one-file CLI (`scripts/newsfeed/slm/review_cli.py`) that prints title, body excerpt and tags and records `y`, `n` or `c TAG1,TAG2,TAG3`. Budget: about one hour of Joe's time. No web UI.

### C.5 Label-noise ceiling (required baseline)

The labels are machine outputs. Before any SLM number is interpreted, the implement PR measures how much the current tagger agrees with itself: re-tag 200 valid-window posts through the ladder (`backfill_tags.js --retag` semantics, but written to a scratch file, not to `posts.json` or Turso) and compute exact-3 and Jaccard between the stored `tags_text` and the re-tag. That agreement is the noise ceiling; an SLM cannot be expected to exceed it against machine labels. Run this on a host with subscription credentials and `RADON_LADDER_ALLOW_PREPAID` unset (about 200 subscription calls, zero prepaid spend).

### C.6 Content licence, PII, publication

- Market Ear posts are paid subscription content scraped under Joe's account (`THEMARKETEAR_EMAIL`). The corpus is for internal use only. It is never committed, never published, never uploaded to a public bucket, never attached to a PR or issue.
- Weights trained on the corpus are a derived work of that content. They stay private (section E). No Hugging Face upload, no public release.
- Labels were produced by Claude, Grok, GPT, Gemini, NVIDIA and Cerebras models. Content categorization is on Anthropic's permitted list for training non-competing models (`radon-small-model.md` section 2 cites the policy). The model is a 3-tag classifier and stays one.
- PII: posts are market commentary; the extract still strips email addresses and URLs from `content` with two regexes and records the count in `manifest.json`. Joe's research posts are excluded outright (C.2 rule 1).
- The `manifest.json` (counts, hashes, dates) may be committed as part of the model card; it contains no text from any post.

---

## D. Base model, train stack, hyperparameters, hardware target

### D.1 Hardware reality

| Host | What is known | Role in v1 |
|---|---|---|
| Joe's Mac Mini (`joes-mac-mini` on the tailnet; runs the nightly loops under launchd) | Apple silicon; RAM unknown (open question 1 in `radon-small-model.md`) | **Train** and **export**. mlx-lm LoRA on a 1.5B bf16 base needs about 6 GB; any 16 GB Mini qualifies. Also runs the eval harness against the ladder baseline because it holds subscription credentials. |
| Hetzner app host (runs `radon-newsfeed.service`) | `cloud/radon-cloud-deployment-guide.html` names CPX11 (2 vCPU, 2 GB) as the reference size; that guide predates the current unit set and is not to be trusted for sizing. Actual RAM and vCPU must be read from the box (`nproc; free -m`) and from the 14-day `host_metrics` table (`mem_avail_mb` p05). No GPU. | **Serve**, if and only if the memory gate in D.4 passes. The caller (`model_ladder_cli.py`) runs here, so serving here keeps the rung a localhost call with no cross-host dependency. |
| Rented GPU | none today | Not needed. Named only as the alternative if the Mini is unavailable (D.3). |

Decision: **train on the Mini, serve on the Hetzner app host, localhost only.** Serving on the Mini over Tailscale is rejected for v1: it makes a production unit depend on a desktop machine and on an ACL change (`tasks/lessons.md` 2026-09: the tagged broker node could ping the Mini but not reach port 22).

### D.2 Base model (`[HR-2]`)

Default and only v1 base: **`Qwen/Qwen2.5-1.5B-Instruct`** (Apache 2.0). It is also bakeoff arm B (prompt-only, no fine-tune) in section F, so arms B and C share one base and the fine-tune's contribution is measured directly.

Size changes are allowed in exactly two directions, each with a trigger:

- **Up** (`Qwen/Qwen2.5-3B-Instruct`, same family, same toolchain) only if arm C fails the F.3 acceptance gates and the failure is a quality gate (G2, G3, G6, G7), not a format or latency gate. A 3B Q4_K_M is about 2 GB resident and re-runs the D.4 memory gate.
- **Down** (`Qwen/Qwen2.5-0.5B-Instruct`) only if the D.4 memory gate fails at 1.5B and Joe declines a host upsize. A downsized model must pass the same gates; it is not exempt.

Why this and not the others in the 1 to 3B class:

| Candidate | Licence | Fit | Verdict |
|---|---|---|---|
| Qwen2.5-1.5B-Instruct | Apache 2.0 | Mature paths in mlx-lm (`mlx_lm.convert -q`, `mlx_lm.lora`, `mlx_lm.fuse`) and llama.cpp (`convert_hf_to_gguf.py`, `llama-server` with json_schema grammars); 1.5B is the largest size whose Q4_K_M (about 1.1 GB file, about 1.5 GB resident at 2k context) can plausibly share a small VPS; strong instruction following for its size | **v1 base, arms B and C** |
| Qwen2.5-3B-Instruct | Apache 2.0 | Same toolchain; about 2 GB resident at Q4_K_M | **Upgrade only on a quality-gate failure** |
| Qwen2.5-0.5B-Instruct | Apache 2.0 | Half the memory (Q8_0 about 0.6 GB); weaker on rarer tags | **Downgrade only on a memory-gate failure** |
| Qwen3.5-0.8B / 2B (recommended in `radon-small-model.md`) | Apache 2.0 | Newer; hybrid thinking must be disabled for classification; toolchain support for the newer architecture is a per-version check | Not in v1. Newness is not a reason on its own; a family change is a v2 decision after the 3B upgrade path is exhausted. |
| Llama-3.2-1B / 3B-Instruct | Llama 3.2 Community License | Attribution and naming obligations, acceptable-use terms | Rejected on licence friction for zero capability gain |
| Phi-4-mini | MIT | 3.8B, too large for the serve host; no evidence it beats Qwen at 1.5B on short classification | Rejected on size |
| Gemma 4 E4B | Apache 2.0 | 4.5B effective; the image path is the only reason to want it and vision is out of scope | Rejected for v1 |

Exactly one base ships in v1. Changing it is a version bump (section E), not a config flag.

### D.3 Train stack

Recommended: **mlx-lm QLoRA on the Mac Mini** (4-bit quantized base, LoRA adapters; `[HR-1]` arm C). Zero spend, no prepaid key anywhere in the loop.

QLoRA versus plain LoRA on this hardware, stated honestly: a 1.5B bf16 base trains comfortably in about 6 GB, so QLoRA buys little memory on a 16 GB Mini. It is the default because it is the recipe Joe asked for, it is the recipe that would be used on a rented GPU with Unsloth, and it keeps the training footprint small enough to run beside the nightly loops. The one technical cost is export: an adapter trained against a 4-bit base is fused onto the **bf16** base for GGUF conversion (a fuse from a quantized base does not convert cleanly). That mismatch is measured, not assumed: gate G0 below requires the fused GGUF's eval to match the adapter-on-quantized-base eval within 0.01 micro-F1. If G0 fails, retrain as plain LoRA on the bf16 base (`mlx_lm.lora` without the `-q` base) and record the switch in the model card; the gates in section F are unchanged.

The task brief named Unsloth or Axolotl. Both are CUDA-first; Axolotl has documented MPS constraints and Unsloth's core library does not train on Apple silicon (`radon-small-model.md` section 2 tooling table). Unsloth is the sanctioned alternative only if Joe would rather rent one GPU hour than use the Mini; that path costs about one to two dollars on a T4/L4 spot instance and is the only case where a cloud key enters the training loop. It is not the recommendation.

Files (implement PR):

```
scripts/newsfeed/slm/
  build_dataset.py     C.2 extract, C.3 split, manifest
  review_cli.py        C.4 human-ok capture
  train.sh             mlx_lm.lora with the YAML below; refuses to run if any *_API_KEY is in env
  export.sh            mlx_lm.fuse -> convert_hf_to_gguf.py -> llama-quantize; writes manifest.json with sha256s
  eval.py              section F metrics; reads predictions JSONL, prints one JSON object
  predict_slm.py       held-out posts -> llama-server -> predictions JSONL with latency per row (arms B and C; --arm flag)
  predict_ladder.py    held-out posts -> complete_text_json (subscription only) -> predictions JSONL (arm A)
  bakeoff.py           runs eval.py over A, B, C predictions; prints the F.3 gate table and the HR-1 verdict
  monitor.py           section I.3 post-deploy drift query; one JSON object, exit 3 on threshold breach
  shadow.py            section I shadow-mode row writer (imported by model_ladder_cli.py)
  configs/qwen25-1p5b-qlora-v1.yaml
```

Starting configuration (`configs/qwen25-1p5b-qlora-v1.yaml`), to be tuned only against `valid.jsonl`:

```yaml
# Base for TRAINING is the 4-bit MLX quantization of the bf16 base:
#   mlx_lm.convert --hf-path Qwen/Qwen2.5-1.5B-Instruct -q --q-bits 4 --mlx-path models/slm-tagger/v1/base-q4
# Base for EXPORT is the bf16 original (E.1); the adapter is fused onto it and G0 checks the parity.
model: models/slm-tagger/v1/base-q4
train: true
fine_tune_type: lora                      # QLoRA = LoRA adapters over the quantized base above
data: data/slm/tagger/v1
adapter_path: models/slm-tagger/v1/adapter   # local only; gitignored (section E)
num_layers: 16
lora_parameters:
  rank: 16
  scale: 20.0
  dropout: 0.05
  keys: [self_attn.q_proj, self_attn.k_proj, self_attn.v_proj, self_attn.o_proj,
         mlp.gate_proj, mlp.up_proj, mlp.down_proj]
batch_size: 8
iters: 1200                 # about 2.5 to 3 epochs at 2,500 to 3,000 train rows
learning_rate: 1.0e-4
max_seq_length: 1024        # system + 1500-char body + answer fits with margin
mask_prompt: true           # loss on the assistant JSON only
steps_per_eval: 100
val_batches: 50
save_every: 200
grad_checkpoint: false      # set true on a 16 GB Mini if memory is tight
seed: 20260919
```

Stop rule: stop at the checkpoint with the lowest validation loss; if validation loss has not improved for 300 iterations, stop early. Expected wall clock on an M-series Mini: under one hour. Record the final train/valid loss, iteration and wall clock in the model card. `[HR-6]` Loss is a training-time stop signal and a model-card fact; it is not an acceptance gate and is never quoted as evidence of quality. Checkpoint selection between candidates uses `valid.jsonl` micro-F1 from `eval.py`, not loss.

Decoding at serve and eval time: greedy (`temperature 0`), `max_tokens 64`, grammar-constrained to the schema in H.3. Same settings in `predict_slm.py` for arms B and C and in the ladder rung; a mismatch invalidates the eval.

Export parity gate **G0** (`[HR-1]` arm C is the GGUF that will serve, not the adapter on the Mini): run `eval.py` on `valid.jsonl` twice, once with the adapter over the quantized base in MLX, once with the fused Q4_K_M GGUF through `llama-server`. `|micro_f1_mlx - micro_f1_gguf| <= 0.01` and `invalid_rate_gguf <= invalid_rate_mlx + 0.002`. All section F numbers for arm C are then taken from the GGUF path only.

Arm B recipe (prompt-only baseline, same base, no adapter): the bf16 base converted to Q4_K_M with the same `convert_hf_to_gguf.py` and `llama-quantize` steps, served by the same `llama-server` flags, prompted with the **full** production system prompt from `buildSystemPrompt(taxonomy)` (the taxonomy list included, because an un-tuned model has no other way to know the vocabulary) plus the same user prompt, same grammar. Arm B is the honest "did the fine-tune do anything" control; it is also what would ship if C failed but B passed, which the HR-1 rule forbids, so B passing and C failing means no cutover.

### D.4 Serve-host memory gate

The unit is enabled on the Hetzner app host only if, over the trailing 14 days of `host_metrics`, the 5th percentile of `mem_avail_mb` exceeds the model's resident size plus 512 MB (1.5B Q4_K_M: about 2,048 MB threshold; 0.5B Q8_0: about 1,152 MB). The implement PR prints this query and its result in the PR body. If the gate fails, the choices are Joe's (section L): upsize the host, fall back to the 0.5B model, or keep the rung off.

---

## E. Export artifacts and versioning

### E.1 Artifacts per version

| Artifact | Produced by | Approx size | Where it lives |
|---|---|---|---|
| `adapter/adapters.safetensors` + `adapter_config.json` | `mlx_lm.lora` | 20 to 60 MB | B2 |
| `fused/` (bf16 safetensors) | `mlx_lm.fuse` | about 3 GB | Mini only; not uploaded; regenerable from base + adapter |
| `radon-slm-tagger-v1.Q4_K_M.gguf` | `convert_hf_to_gguf.py --outtype f16` then `llama-quantize ... Q4_K_M` | about 1.1 GB | B2; installed to `/var/lib/radon/models/` on the serve host |
| `radon-slm-tagger-v1.Q8_0.gguf` | same, `Q8_0` | about 1.7 GB | B2; used only if the Q4_K_M build fails G0 export parity or a quality gate while the memory gate still passes |
| MLX quantized dir (`mlx_lm.convert -q`) | optional | about 1 GB | Mini only, for local eval speed; not a production artifact |
| `MODEL_CARD.md` | hand-written from template | text | **git** |
| `manifest.json` | `export.sh` | text | **git** |

### E.2 Storage decision: B2 canonical, manifest in git, weights never in git

- The repository has no git-lfs and `deploy.sh` runs a tracked-drift guard; a 1 GB binary in git is rejected outright, LFS or not.
- Backblaze B2 already holds the nightly DB dumps with credentials on the Hetzner host (`RADON_ARCHIVE_S3_*` in `cloud/config/required-env.txt`). Weights go to the same account under a **separate private bucket or prefix** `radon-models/slm-tagger/v1/`. Whether that is a new bucket (new credentials, new secret) or a prefix under the existing archive bucket (no new secret, but the archive key gains write scope over model files) is Joe's call (section L). Default proposal: prefix under the existing bucket, read with the existing key, so no new secret is minted.
- GitHub release assets are rejected: they need a tag on `main`, the repo's release surface is not used for binaries today, and a 1 GB asset per version is awkward to rotate.
- `models/` in git holds only text:

```
models/
  README.md                       one paragraph: weights are in B2, see the manifest; never commit binaries
  slm-tagger/
    v1/
      MODEL_CARD.md               base, licence, honesty label, dataset manifest hashes, eval table, known failure modes
      manifest.json               {"version":"1.0.0","base":"Qwen/Qwen2.5-1.5B-Instruct","adapter_sha256":...,
                                   "gguf":{"Q4_K_M":{"sha256":...,"bytes":...,"b2_key":"radon-models/slm-tagger/v1/..."}},
                                   "dataset_manifest_sha256":...,"prompt_contract_sha256":...,"trained_at":...,"eval":{...}}
```

`.gitignore` gains `models/**/*.gguf`, `models/**/*.safetensors`, `models/**/adapter/`, `models/**/fused/`, `data/slm/`.

### E.3 Versioning

- Semantic version in `manifest.json`. Major = base model or prompt contract change; minor = retrain on new data; patch = re-quantization or serving fix with identical weights.
- The serve host installs by sha256: `cloud/scripts/install-slm-model.sh <version>` downloads the GGUF named in the committed manifest, verifies the hash, writes `/var/lib/radon/models/radon-slm-tagger-<version>.gguf` and updates the `current` symlink. Rollback is repointing the symlink and restarting the unit. The deploy does not touch weights; a version bump is a manual, Joe-approved step.

---

## F. Eval protocol, three-arm bakeoff, acceptance gates

### F.1 The bakeoff (`[HR-1]`): three arms, one frozen test split, one table

| Arm | System | Prompt | Where quality runs | Where latency runs |
|---|---|---|---|---|
| A | current ladder tagger (`complete_text_json`, subscription creds only, `RADON_LADDER_ALLOW_PREPAID` unset) | production `buildSystemPrompt(taxonomy)` + user prompt | Mini | **serve host** (the CLI runs there in production; measure from there) |
| B | prompt-only `Qwen/Qwen2.5-1.5B-Instruct`, Q4_K_M, no adapter | production system prompt (taxonomy included) + user prompt, grammar on | Mini or serve host (identical weights) | **serve host** |
| C | QLoRA-tuned 1.5B, fused Q4_K_M GGUF (after G0) | `SLM_SYSTEM` + user prompt, grammar on | Mini or serve host | **serve host** |

Baselines that frame the table, measured first:

| Id | Measurement | Why |
|---|---|---|
| B0 | Label-noise ceiling (C.5): arm A re-tag vs stored `tags_text`, 200 valid posts | how much A agrees with its own past labels; the ceiling for any machine-label metric |
| B3 | Cost per 1,000 posts: A = subscription calls (count) plus Cerebras dollars for any fall-through (tokens times list price); B and C = 0 marginal dollars, report CPU-seconds per post and resident MB; throughput as posts per minute sequential | the cost half of the HR-1 rule |

Every arm is scored on both `test.jsonl` (machine labels, n about 1,200) and `gold_human.jsonl` (n = 200). `bakeoff.py` prints one JSON object and one Markdown table with all arms side by side; both go into the implement PR body verbatim. Confidence intervals are paired bootstrap (1,000 resamples) on the per-post difference C minus A and C minus B.

### F.2 Metric definitions (`[HR-6]`)

Predictions are normalised with the same function the product uses (`__normaliseTags` semantics, reimplemented in Python with a parity test against the JS fixtures in `web/tests/newsfeed-tagger.test.ts`). "Valid" means the H.3 validator accepted the output: exactly 3 tags after normalisation, all present in the taxonomy snapshot.

- `invalid_rate`: fraction of posts whose output is not valid (unparseable, empty, fewer or more than 3 after normalisation, or any tag outside the taxonomy snapshot). Reported with its breakdown (`unparseable`, `count`, `out_of_taxonomy`, `abstained`).
- `micro_f1`: each `(post, tag)` pair is a positive; precision and recall pooled over all pairs. Invalid outputs count as zero predicted tags (they are recall losses, not excluded).
- `macro_f1`: F1 computed per tag over the test split, averaged over tags with test support >= 1. Reports how the tail behaves, which micro-F1 hides.
- `rare_label_recall`: recall restricted to tags whose train-split support is in the lowest quartile (and >= 3, so a tag seen once is not a "label" for this purpose); the tag list is written to `manifest.json` as `rare_tags`.
- `exact3`: the 3-tag sets are equal.
- `jaccard`: `|P ∩ G| / |P ∪ G|` per post, mean over posts.
- `human_accept`: on `gold_human.jsonl`, fraction of posts Joe marked `y`, or whose corrected set equals the prediction.
- `latency_p50_s`, `latency_p95_s`: wall clock per post including HTTP, measured on the serve host with the unit's real settings, 300+ posts, sequential. For arm A this is the full ladder walk as the CLI performs it.
- `throughput_ppm`: posts per minute, sequential, same run.
- `cost_per_1k_usd`, `subscription_calls_per_1k`: from B3.

Train loss and validation loss are not metrics here; they appear only in the model card.

### F.3 Acceptance gates and the HR-1 cutover rule

All on the frozen test split (n >= 300 for machine metrics) and `gold_human.jsonl` (n = 200). "vs A" and "vs B" are on the same posts, same day, same serve host for latency.

Hygiene gates (arm C on its own):

| Gate | Rule | Meaning |
|---|---|---|
| G0 | export parity (D.3): `|micro_f1_mlx - micro_f1_gguf| <= 0.01` | the GGUF that serves is the model that was evaluated |
| G1 | `invalid_rate(C) <= 0.005` on test, with `out_of_taxonomy <= 0.003` | grammar plus validator work; the model does not degenerate or invent vocabulary |
| G5 | `latency_p95_s(C) <= 10` on the serve host and every call under the rung timeout (H.4) | fits inside the per-post budget with room for a fall-through |

Win gates (the HR-1 rule; C must **beat**, not merely match):

| Gate | Rule | Meaning |
|---|---|---|
| G2 | `micro_f1(C) > micro_f1(A)` on `gold_human`, and the paired bootstrap 95% CI of the difference has lower bound `> 0`; on `test.jsonl`, `micro_f1(C) >= micro_f1(A) - (1 - B0_micro_f1)` | wins on F1 against the only unqualified gold; not worse than A's own noise floor on machine labels |
| G3 | `macro_f1(C) >= macro_f1(A)` and `rare_label_recall(C) >= rare_label_recall(A) - 0.05` on test | the win is not bought by dropping the tail |
| G4 | `invalid_rate(C) <= invalid_rate(A)` | at least as clean as today (A returns prose or short lists sometimes) |
| G6 | `human_accept(C) >= human_accept(A)` | not worse where a human judged |
| G7 | `cost_per_1k_usd(C) < cost_per_1k_usd(A)` and `subscription_calls_per_1k(C) < subscription_calls_per_1k(A)` and `latency_p95_s(C) <= latency_p95_s(A)` measured on the serve host | wins on cost **and** latency vs A, as HR-1 requires; if the host is too slow for the latency half, there is no cutover, whatever the F1 says |
| G8 | `micro_f1(C) > micro_f1(B)` with CI lower bound `> 0`, and `invalid_rate(C) <= invalid_rate(B)` | the fine-tune did something a prompt could not |

Decision rule:

- Any hygiene gate fails: no arm C number is reported as final; fix export, grammar or host first.
- Any win gate fails: rung stays `off`. The model card records the full table and which gate failed. The next step is the D.2 size rule (up on a quality-gate failure, never a threshold change) or a dataset fix; if G7 failed on latency alone, the next step is a hardware decision (section L), not a retrain.
- All gates pass: **C wins**. `shadow` is allowed immediately; `prefer` after the shadow window; `primary` after the prefer window (I.2). This is the only path to cutover.

Note on why the win is judged on `gold_human`: `test.jsonl` labels were produced by arm A, so on that split A is competing against its own earlier answers and C cannot be expected to beat it beyond the B0 noise floor; the human-ok slice is the one arena where "better than A" has a meaning. Its n = 200 makes the CI wide; a narrow win will not clear it, and that is the intended conservatism.

---

## G. Serve design

### G.1 Stack: `llama-server` from llama.cpp, one binary, localhost (`[HR-7]` GGUF sidecar)

- HR-7 allows a GGUF or a vLLM sidecar. vLLM is rejected for v1 because it wants a GPU and neither host has one; the GGUF path is the CPU path. If a GPU host ever appears, vLLM with the same OpenAI-compatible surface is a drop-in behind `RADON_SLM_TAGGER_URL` and needs no ladder change.
- OpenAI-compatible `POST /v1/chat/completions` with `response_format: {"type":"json_schema", ...}` so the grammar is enforced server-side; the caller still normalises and validates (H.3).
- CPU build (`GGML_NATIVE=ON`, no CUDA, no Metal), pinned to a llama.cpp release tag recorded in the model card and in `cloud/config/` alongside the other approved tool pins.
- Ollama is rejected for the serve host: it adds a model manager daemon, its own storage layout and a second update channel for a single model. MLX serving is only for the Mini during eval.
- Port `8331` on `127.0.0.1` (in-use ports on the host today: 8321 api, 8330 health, 8334, 8340, 8765 relay; verify with `ss -ltn` before pinning).

### G.2 Unit sketch (`cloud/services/radon-slm-tagger.service`, new)

```ini
[Unit]
Description=Radon SLM tagger: internal specialist, not a SotA replacement for open research (llama-server sidecar, newsfeed text tags only)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
User=radon
UMask=0077
EnvironmentFile=/etc/radon/env
ExecStart=/usr/local/bin/llama-server \
  --model /var/lib/radon/models/current.gguf \
  --alias radon-slm-tagger \
  --host 127.0.0.1 --port 8331 \
  --ctx-size 2048 --parallel 1 --threads ${RADON_SLM_TAGGER_THREADS} \
  --temp 0 --no-webui --log-disable
Restart=on-failure
RestartSec=30
MemoryMax=2400M
CPUQuota=150%
Nice=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Notes for the implementer:

- `MemoryMax` is a hard ceiling so a leak cannot take `radon-nextjs` down; the value is the D.4 threshold plus headroom and is re-derived if the model changes.
- `Nice=10` and `CPUQuota` keep the sidecar behind the API and Next.js on a shared-vCPU host.
- `--parallel 1`: the newsfeed tags one post at a time; backfill is throttled to about 24 posts per minute. No batching.
- The unit is pinned in `cloud/config/installed-units.sha256` (the deploy's `install-units` verb installs anything listed there). The unit watchdog (`scripts/watchdog/units.py`, continuous bucket) already probes every `radon-*` unit via `systemctl show` and pages on `failed` / `start-limit-hit`, so a dead sidecar is covered with no new heartbeat. Call-level health is the `slm_status` column of the shadow table (section I) plus the `attempted` codes in the `newsfeed-tagger` ladder log lines. Do not add a `service_health` writer for this unit and do not put one inside `llama-server`.
- `RADON_SLM_TAGGER_THREADS` defaults to `nproc - 1`, minimum 1.
- Health: `GET http://127.0.0.1:8331/health` returns 200 when the model is loaded; the ladder rung treats anything else as `slm-tagger:unavailable` and falls through.

---

## H. Ladder integration: rung `slm-tagger`, tags contract only

### H.1 Where the hook goes

Two files change. Nothing in Node changes.

1. `scripts/clients/model_ladder.py`
   - Add provider name `"slm-tagger"` with tier `"local"`. It is **not** added to `MODEL_LADDER_ORDER` and **not** returned by `wired_providers()` by default, so every existing caller's `attempted` tuple and every existing test stay byte-identical.
   - `_auth_for("slm-tagger", env)` returns `AuthMaterial(token="", kind="local", mechanism="RADON_SLM_TAGGER_URL")` when `RADON_SLM_TAGGER_URL` is set, else `None`.
   - `_call_text_provider` gains a branch that POSTs to `f"{url}/v1/chat/completions"` with the OpenAI body, `model: "radon-slm-tagger"`, `temperature: 0`, `max_tokens: 64`, `response_format` = the H.3 schema, `timeout = RADON_SLM_TAGGER_TIMEOUT_S`. It sends `SLM_SYSTEM` as the system message, **replacing** the caller's taxonomy-bearing system prompt for this rung only; the user message is passed through unchanged.
   - `complete_text_json` gains a keyword `providers: Sequence[str] | None = None` that is forwarded to `_run_ladder` (which already accepts it). Default `None` keeps today's order.
2. `scripts/clients/model_ladder_cli.py`
   - When `req["accept"] == "tags"` (the newsfeed contract; `accept_distill_payload` callers never hit this branch) and `RADON_SLM_TAGGER_MODE` is `shadow`, `prefer` or `primary`, build the provider order per H.2 and pass `providers=` to `complete_text_json`. `off` or unset: identical to today.
   - Write the shadow row (section I) and the `slm-tagger` heartbeat.

The distill head, the research reviewer and the CTA vision cascade never see the rung: they do not go through the CLI, they never pass `providers=`, and `MODEL_LADDER_ORDER` is unchanged. A pin test asserts that `complete_text_json(...)` with no `providers` argument never attempts `slm-tagger` even when `RADON_SLM_TAGGER_URL` and `RADON_SLM_TAGGER_MODE=primary` are set.

### H.2 Modes (`RADON_SLM_TAGGER_MODE`, default `off`)

| Mode | Provider order for the tags contract | Returned to Node | Purpose |
|---|---|---|---|
| `off` | `MODEL_LADDER_ORDER` | ladder result | today |
| `shadow` | `slm-tagger` runs first, bounded; its result is written to the shadow table and **discarded**; then `MODEL_LADDER_ORDER` | ladder result | measure agreement in production without changing output |
| `prefer` | `slm-tagger` then `MODEL_LADDER_ORDER` | SLM result when it passes `accept_tags_payload` and normalisation, else the ladder's | cutover step 2 |
| `primary` | `slm-tagger` then `nvidia` then `cerebras` | as `prefer`, but the subscription band is skipped for this contract to keep its quota for distill and reviewer | cutover step 3, "retire expensive path" for text tagging |

The order strings are constants with a test each; the mode is read once per CLI invocation from the environment (`/etc/radon/env`), never from a preference row, so `ib_place_order`-style env inheritance is not involved and the newsfeed unit needs only an env change plus restart to move modes.

### H.3 Prompt contract v1 (pinned by sha256 in `manifest.json`)

System message, single fixed string, identical in training pairs and in the rung:

```
You tag Market Ear posts for the Radon dashboard. Return exactly 3 UPPERCASE tags as JSON: {"tags":["A","B","C"]}. Multi-word tags are UPPERCASE-KEBAB-CASE. Prefer a specific technical signal, then the instrument, then the sector, then the theme. No prose.
```

User message: `buildUserPrompt(post)` output, unchanged.

JSON schema sent as `response_format`:

```json
{"type":"object","required":["tags"],"additionalProperties":false,
 "properties":{"tags":{"type":"array","minItems":3,"maxItems":3,
   "items":{"type":"string","minLength":1,"maxLength":40,"pattern":"^[A-Z0-9&-]+$"}}}}
```

If the pinned llama.cpp release does not honour `pattern`, drop it from the schema and rely on the validator below; record which in the model card. The schema has no `$ref`.

Constrained output and abstention (`[HR-3]`). The grammar is the first fence; the validator in `model_ladder.py` (`accept_slm_tags_payload`, used for this rung only, distinct from `accept_tags_payload`) is the contract:

1. Parse JSON; `tags` must be a list. Anything else: `slm-tagger:unparseable`.
2. Normalise each item with the Python twin of `__normaliseTags`; drop empties; dedupe case-insensitively. The result must have **exactly 3** items. Fewer or more: `slm-tagger:abstain:count`.
3. Every item must be present (case-insensitive) in the taxonomy snapshot read at call time (Turso `tag_taxonomy` via the CLI's existing DB access, or `data/tag_taxonomy.json` when the DB is unreachable; the snapshot source is logged). Any miss: `slm-tagger:abstain:out_of_taxonomy`, with the offending tags logged for the drift monitor.
4. An empty list `{"tags":[]}` is the model's explicit abstention: `slm-tagger:abstain:empty`. (The grammar's `minItems: 3` prevents this in the default schema; if the implementer relaxes `minItems` to allow explicit abstention, the rule stays the same.)

Any outcome other than `slm-tagger:ok` falls through to the next rung exactly as a provider error does; the SLM never returns free text, a partial list, or a coined tag to Node. This is a closed-vocabulary rung by construction: the **ladder** keeps the open vocabulary and remains the only producer of new taxonomy entries, which is how HR-5 ("taxonomy stays in versioned data, not weights") is enforced at the wire rather than by hoping the model behaves.

The taxonomy read in step 3 is not in the training data and is not frozen in the weights; growing it (via the ladder) immediately widens what the SLM may return, and pruning it immediately narrows it, with no retrain.

### H.4 Timeouts and budgets

- Rung read timeout `RADON_SLM_TAGGER_TIMEOUT_S`, default `12`. Health probe 1 s before the call; a failed probe is `slm-tagger:unavailable` and costs nothing.
- The Node per-post budget is `DEFAULT_FETCH_TIMEOUT_MS = 30_000` (R-466). With the rung first, worst case is 12 s SLM plus one subscription call. If the measured `p95(SLM) + p95(ladder) > 25 s` in shadow, raise the Node budget to 45 s in the same implement PR; a bound of any size satisfies R-466, an unbounded wait does not.
- Failure taxonomy appended to `attempted` exactly like other rungs: `slm-tagger:unavailable`, `slm-tagger:http_5xx`, `slm-tagger:unparseable`, `slm-tagger:abstain:count`, `slm-tagger:abstain:out_of_taxonomy`, `slm-tagger:abstain:empty`, `slm-tagger:network:<Exc>`, `slm-tagger:ok`. Abstentions are counted separately from errors in the shadow table and the monitor (I.3) because a rising abstention rate is a vocabulary-drift signal, not an outage.

### H.5 Env inventory

New optional variables, all added to `cloud/.env.example` and to `_OPTIONAL_LADDER_ENV` so the inventory tests stay green, none added to `cloud/config/required-env.txt`:

`RADON_SLM_TAGGER_URL` (`http://127.0.0.1:8331`), `RADON_SLM_TAGGER_MODE` (`off`), `RADON_SLM_TAGGER_TIMEOUT_S` (`12`), `RADON_SLM_TAGGER_THREADS`.

---

## I. Shadow-mode metrics, cutover checklist, post-deploy monitor

### I.1 Shadow table (migration `0077_slm_tagger_shadow.sql`)

```sql
CREATE TABLE IF NOT EXISTS slm_tagger_shadow (
  post_id        TEXT NOT NULL,
  observed_at    TEXT NOT NULL,             -- ISO-8601 UTC
  model_version  TEXT NOT NULL,             -- manifest.json version
  mode           TEXT NOT NULL,             -- shadow | prefer | primary
  tags_slm       TEXT,                      -- JSON array (validated) or NULL
  tags_slm_raw   TEXT,                      -- JSON as returned, pre-validation (for out_of_taxonomy forensics)
  tags_ladder    TEXT,                      -- JSON array or NULL (NULL in prefer/primary unless sampled)
  ladder_provider TEXT,
  ladder_sampled INTEGER NOT NULL DEFAULT 0, -- 1 when the ladder was called only for the I.3 drift sample
  slm_status     TEXT NOT NULL,             -- ok | unavailable | timeout | unparseable | abstain:count | abstain:out_of_taxonomy | abstain:empty | http_<code>
  slm_latency_ms INTEGER,
  ladder_latency_ms INTEGER,
  exact3         INTEGER,                   -- 1/0/NULL
  jaccard        REAL,
  PRIMARY KEY (post_id, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_slm_shadow_observed ON slm_tagger_shadow(observed_at DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (77, datetime('now'));
```

Written by `shadow.py` from the CLI with a fresh connection per call (Hrana rule 3), one row per post in every mode except `off`, never blocking the returned result (write failure is logged and dropped). Retention: 90 days, pruned by the existing `radon-db-retention.service` (add the table to its list). This table is also the provenance source for C.2 rule 6.

### I.2 Cutover checklist (each step is a separate Joe approval)

1. **Bakeoff: C wins** (`[HR-1]`, all F.3 gates including G7 and G8) on the frozen test split and `gold_human`; the A/B/C table is in the implement PR body; model card committed. Without this row nothing below is reachable.
2. **Serve host gate passes** (D.4); unit installed and pinned; `/health` 200; unit visible to the unit watchdog probe.
3. **`shadow` for 14 calendar days** (about 300 posts). Exit criteria to move on: `slm_status = ok` rate `>= 0.99`, abstention rate `<= 0.02`, production `jaccard` and `exact3` against the ladder within 0.03 of the offline test numbers (otherwise the test split is not representative and the extract is re-run), `slm_latency_ms` p95 under 10,000, zero unit restarts, no `radon-nextjs` memory alerts.
4. **`prefer`** for 14 days. `tags_text` now comes from the SLM on a validated success. I.3 monitor thresholds all green for the window. Rollback is `RADON_SLM_TAGGER_MODE=off` and a newsfeed unit restart, nothing else.
5. **`primary`** only after step 4 showed no monitor breach. Subscription band skipped for the tags contract; NVIDIA and Cerebras remain as fall-through.
6. **Retire** means the mode stays `primary` and the subscription quota freed is visible in the ladder logs (`newsfeed-tagger try provider=` lines). No code is deleted; the ladder order is unchanged for every other caller.

### I.3 Post-deploy monitor (`[HR-7]`): drift, invalid tags, label-distribution shift

`scripts/newsfeed/slm/monitor.py` runs daily from a new oneshot pair `cloud/services/radon-slm-tagger-monitor.{service,timer}` (07:10 UTC, `RandomizedDelaySec=300`, `EnvironmentFile=/etc/radon/env`, no-op exit 0 when `RADON_SLM_TAGGER_MODE` is `off` or `shadow`). It reads `slm_tagger_shadow` for the trailing 7 and 28 days, prints one JSON object, writes a `service_health[slm-tagger-monitor]` heartbeat (26 h window, scheduled bucket) and exits 3 on any breach. A breach pages through the existing watchdog path (oneshot `Result=exit-code` on a new `InactiveEnterTimestamp`) and the documented operator action is `RADON_SLM_TAGGER_MODE=off` plus a retrain decision; nothing auto-flips. The existing `radon-drift-audit` unit is a config-drift audit and is not reused for this. The new timer falls under the `ops-timers` owners rule, so the implement PR adds its row to `docs/operations.md` and `docs/cloud-services.md`.

| Signal | Definition | Breach |
|---|---|---|
| invalid or empty | share of rows with `slm_status` in `unparseable`, `abstain:count`, `abstain:empty` (7 d) | `> 0.01` |
| vocabulary drift | share of rows with `slm_status = abstain:out_of_taxonomy` (7 d), plus the distinct out-of-taxonomy tags seen (from `tags_slm_raw`) | `> 0.03`, or any single unknown tag seen `>= 10` times |
| label-distribution shift | Jensen-Shannon divergence between the 28-day SLM tag frequency vector and the train-split label frequency vector in `manifest.json` (`label_distribution`), over the union of tags | `> 0.10`; also report top-20 tag share and the count of train tags with zero predictions in 28 d |
| agreement with the ladder | in `prefer`/`primary` the CLI still calls the ladder for 1 in 20 posts (`ladder_sampled = 1`, result discarded, subscription cost about 1 call per day) and records `exact3` and `jaccard` | 28-day `jaccard` below the shadow-window mean by `> 0.05` |
| availability | `unavailable` plus `timeout` share (7 d) | `> 0.05` |
| latency | `slm_latency_ms` p95 (7 d) on the serve host | `> 10,000` |

Retrain trigger (a Joe decision, prompted by the monitor, executed per D.3 with a version bump): any vocabulary-drift breach, or taxonomy growth `> 10%` since `taxonomy_sha256`, or a label-shift breach that persists two consecutive runs. Retraining never changes the taxonomy; it only teaches the model the tags the ladder has since coined.

A read-only summary (documented, not a UI panel):

```sql
SELECT date(observed_at) d, mode, count(*) n,
       avg(slm_status='ok') ok_rate,
       avg(slm_status LIKE 'abstain:%') abstain_rate,
       avg(slm_status='abstain:out_of_taxonomy') oot_rate,
       avg(CASE WHEN tags_ladder IS NOT NULL THEN exact3 END) exact3_vs_ladder,
       avg(CASE WHEN tags_ladder IS NOT NULL THEN jaccard END) jaccard_vs_ladder,
       avg(slm_latency_ms) slm_ms
FROM slm_tagger_shadow WHERE observed_at > datetime('now','-14 days') GROUP BY 1,2 ORDER BY 1,2;
```

---

## J. Risks

| Risk | Why it is real here | Mitigation in v1 |
|---|---|---|
| Open-vocabulary drift | The vocabulary the model has seen is fixed at train time (212 tags at 2026-07-15, more now). New market concepts get mapped to the nearest known tag, or the model produces a plausible tag that is not in the taxonomy. | `[HR-3]` The rung is closed-vocabulary at the wire: an out-of-taxonomy tag is an abstention and the post falls through to the ladder, which keeps the open vocabulary and remains the only producer of new taxonomy entries. The I.3 monitor tracks `abstain:out_of_taxonomy` and unknown-tag counts; a breach is the retrain trigger. The SLM never appends to the taxonomy. |
| Baking news topics into weights | A model trained on months of posts learns that period's topics as well as the tagging behaviour. | `[HR-5]` Loss on the 3-tag answer only; no text-modelling objective; taxonomy read live, not from weights; time split so the eval window post-dates training; label-distribution shift monitored (I.3); retrain from scratch on the full set, never incrementally on a recent slice. |
| Taxonomy growth outpaces retraining | Retraining needs the Mini and a Joe-approved version bump. | Growing the taxonomy immediately widens the validator with no retrain; only the model's *use* of a new tag waits for the next retrain, and the ladder covers those posts meanwhile. Retrain is a one-hour job with a documented `train.sh`; the model card lists the taxonomy hash so staleness is visible. |
| Bakeoff gamed by the arena | `test.jsonl` labels were produced by arm A, so machine-label F1 favours A's habits; a small human slice has a wide CI. | `[HR-1]` The win is judged on `gold_human` with a bootstrap CI that must exclude zero; machine-label F1 is a floor test against the B0 noise ceiling only; arm B controls for "the base could already do it". |
| No GPU anywhere | Training is Mini-only; serving is CPU-only. | 1.5B Q4_K_M is sized for CPU; volume is 25 posts per day; G5 gates on measured p95; the 0.5B fallback exists. |
| Serve host memory contention | A resident 1.5 GB process on a shared-vCPU VPS next to Next.js, FastAPI, the relay and Playwright. | D.4 gate on 14-day `host_metrics`; `MemoryMax`; `Nice`; `CPUQuota`; rung is soft-fail. |
| Content licence | Market Ear subscription content; weights are a derived work. | Corpus and weights private (C.6, E.2); no public upload; model card states it. |
| Label quality | Training on machine labels distils a distilled label. | Noise ceiling B0 reported; human-ok slice is the only unqualified gold; G6. |
| Self-distillation loop | A v2 trained on `tags_text` rows the SLM itself produced would drift. | Shadow table provenance; C.2 rule 6 filter; model card lists `label_source` counts. |
| Prompt contract drift | If `buildUserPrompt` or the normaliser changes in Node, the SLM sees an input distribution it was not trained on. | Parity tests import the constants; `prompt_contract_sha256` in the manifest; a change to either is a major version bump. |
| Deploy interaction | `deploy.sh` tracked-drift guard, unit manifest, `EX_TEMPFAIL` conventions. | Weights outside the repo tree; unit pinned in `installed-units.sha256`; install script separate from deploy. |
| Time and attention | Joe's one hour of labelling and three approvals are the critical path, not compute. | Section L lists them; nothing else blocks on Joe. |

---

## K. Done-when checklist for the implement PR (draft, no merge)

Red/green order is mandatory (`CLAUDE.md` TDD rule). Every item names its test or its evidence. Items tagged `[HR-n]` are the hard requirements from the intro table; each must be individually checkable from the PR body.

**Dataset**
- [x] `[HR-8]` `scripts/newsfeed/slm/build_dataset.py` has one input, the `posts` query in C.2; `test_slm_build_dataset.py` asserts `manifest.sources == ["turso.posts"]` and that no other reader, file or URL is referenced by the module.
- [x] `[HR-4]` No random shuffle: test asserts the split boundaries are strictly time-ordered (C.3) and greps the module for `shuffle` / `random` (none permitted).
- [x] Row rules covered by tests: research rows excluded, `tags` never used as a label, dedupe keeps earliest, `buildUserPrompt` parity fixture, PII regex counts in manifest, `rare_tags` and `label_distribution` written to the manifest.
- [x] `review_cli.py` writes `gold_human.jsonl` with `reviewed_at`; test on a 3-row fixture; 200 rows captured by Joe.
- [ ] Extract run on a credentialed host; `manifest.json` counts in the PR body; corpus not in the diff (`git status` clean of `data/slm/`).

**Train and export**
- [ ] `[HR-2]` `configs/qwen25-1p5b-qlora-v1.yaml` names the 4-bit conversion of `Qwen/Qwen2.5-1.5B-Instruct`; a test pins the base id in the config and in `manifest.json`.
- [ ] `[HR-5]` `mask_prompt: true` pinned by test; `train.sh` refuses any `*_API_KEY` in env (subprocess test with a fake key) and refuses a data dir whose manifest lists a source other than `turso.posts`.
- [ ] Training run on the Mini; loss curve numbers in the model card only (`[HR-6]`: not in the gate table).
- [ ] `export.sh` produces adapter, F16 GGUF, Q4_K_M GGUF; `manifest.json` sha256s match uploaded B2 objects; `models/slm-tagger/v1/MODEL_CARD.md` carries the honesty label verbatim.
- [ ] G0 export parity measured and in the PR body.

**Bakeoff and eval**
- [ ] `[HR-6]` `eval.py` metrics unit-tested on hand-computed 5-row fixtures: `invalid_rate` with breakdown, `micro_f1`, `macro_f1`, `rare_label_recall`, `exact3`, `jaccard`, bootstrap CI shape; invalid outputs counted as zero predictions.
- [ ] `[HR-1]` `bakeoff.py` run over arms A, B, C on the frozen test split and `gold_human`; the A/B/C table (every F.2 metric, both splits, latency and throughput from the serve host, B3 cost row) pasted verbatim in the PR body.
- [ ] `[HR-1]` Gates G0 to G8 each stated pass/fail with the number; the verdict line reads exactly one of `C WINS`, `C FAILS <gate list>`. `RADON_SLM_TAGGER_MODE` may leave `off` only on `C WINS`.
- [ ] B0 noise ceiling measured and quoted next to the machine-label F1 rows.

**Serve**
- [ ] `[HR-7]` `cloud/services/radon-slm-tagger.service` committed and pinned in `cloud/config/installed-units.sha256`; `cloud/scripts/install-slm-model.sh` verifies sha256; llama.cpp release tag recorded.
- [ ] D.4 memory-gate query and result in the PR body.
- [ ] Unit-watchdog coverage confirmed by name (`radon-slm-tagger.service` appears in the `systemctl show 'radon-*'` probe on the host); no `service_health` row for the sidecar itself.

**Ladder and constrained output**
- [ ] `test_model_ladder.py`: `slm-tagger` absent from `MODEL_LADDER_ORDER` and `wired_providers()`; `complete_text_json` without `providers=` never attempts it even with URL and `primary` set; with `providers=` and a stubbed `post`, the OpenAI body carries `SLM_SYSTEM`, the schema, `temperature 0`, `max_tokens 64`, and the full URL string `http://127.0.0.1:8331/v1/chat/completions`; a 503 falls through to `anthropic` and `attempted` reads `("slm-tagger:http_503", "anthropic:ok")`.
- [ ] `[HR-3]` `accept_slm_tags_payload` tests: 3 valid in-taxonomy tags pass; 2 tags, 4 tags, empty list, prose, lowercase-only-after-normalise duplicates, and one out-of-taxonomy tag each produce the named `abstain:*` or `unparseable` code and fall through; the fall-through result is the ladder's, never a partial SLM list. A wire test asserts Node receives exactly the same `{ok, data, provider, model}` shape it receives today.
- [ ] `test_model_ladder_cli.py`: each mode's provider order; `off` and unset are byte-identical to today's request; `accept == "distill"` never gets the rung in any mode; shadow row written with the right `slm_status` on success, timeout, unavailable and each abstention; 1-in-20 ladder sampling in `prefer`/`primary` sets `ladder_sampled = 1`.
- [ ] `cloud/.env.example` and `_OPTIONAL_LADDER_ENV` list the four new variables; `required-env.txt` unchanged; `cloud/tests/test_env_example.py` green.
- [ ] Node untouched; `web/tests/newsfeed-tagger.test.ts` and `newsfeed-vision-tagger.test.ts` green unchanged.

**Shadow and monitor**
- [ ] Migration `0077_slm_tagger_shadow.sql` with the I.1 columns; `migrate.py` accepts it; retention list updated; row verified in Turso from the serve host in `shadow` mode (one real post) before the PR leaves draft.
- [ ] `[HR-7]` `monitor.py` unit-tested on fixtures for each I.3 signal and threshold (exit 0 / exit 3); `radon-slm-tagger-monitor.{service,timer}` committed, pinned in the unit manifest, rows added to `docs/operations.md` and `docs/cloud-services.md` (`ops-timers` owners rule); no-op verified when mode is `off`.

**Docs**
- [ ] This file updated from PLAN to IMPLEMENTED with the measured A/B/C table; `scripts/newsfeed/CLAUDE.md` and `AGENTS.md` tagging section mention the rung, the closed-vocabulary rule and correct the taxonomy tracking claim; `docs/owners.json` rule `slm-tagger` satisfied.
- [ ] Full suites green (pytest and vitest, never concurrently on the laptop); CI green on the PR head; draft PR, not merged; `RADON_SLM_TAGGER_MODE` remains `off` in production until section L item 6.

---

## L. What Joe must approve

| # | Decision | Default proposal if Joe says "your call" | Blocks |
|---|---|---|---|
| 1 | Use the Mac Mini for about one hour of training plus the arm A, B, C eval runs (arm A spends roughly 1,400 subscription calls across test and gold, zero prepaid); confirm its RAM | yes; 16 GB is enough for the 1.5B QLoRA path | D.3, F.1 |
| 2 | Read `nproc`, `free -m` and 14-day `host_metrics` on the Hetzner app host and, if the D.4 gate fails, choose: upsize the host (monthly spend), fall back to 0.5B, or keep the rung `off` | fall back to 0.5B before spending | D.4, G.2 |
| 3 | One hour labelling 200 posts through `review_cli.py` | required; the HR-1 win is judged on this slice | C.4, G2, G6 |
| 4 | Weights storage: prefix under the existing B2 archive bucket (no new secret) or a new private bucket (new `RADON_MODELS_S3_*` secrets) | existing bucket, new prefix | E.2 |
| 5 | Sign off the bakeoff table: confirm `C WINS` is read from the numbers in the PR body, or accept a `C FAILS` outcome and choose between the D.2 size step, a dataset fix, a hardware step (G7 latency), or parking v1 | agent proposes, Joe decides; no threshold is moved to manufacture a win | F.3, I.2 step 1 |
| 6 | Enable `shadow` in production (`/etc/radon/env` change plus newsfeed restart) | after item 5 reads `C WINS` and D.4 passes | I.2 step 3 |
| 7 | Move to `prefer`, later `primary` | each after its 14-day window with I.3 green | I.2 steps 4 and 5 |
| 8 | Merge the implement PR | never by an agent | K |
| 9 | Any llama.cpp release pin added to the approved tool pins | pin the release used for export and serve | G.1 |
| 10 | Act on a monitor breach or retrain trigger after cutover (mode to `off`, retrain, version bump) | mode to `off` first, retrain second | I.3 |

Decided by the 2026-09-19 enhancement and no longer open: the SLM rung is closed-vocabulary (HR-3); the default base is `Qwen/Qwen2.5-1.5B-Instruct` (HR-2); the training set is Turso posts only (HR-8).

No new API keys, no prepaid spend, no cloud GPU are required by the default proposals. The only spend decision is item 2 and only if the memory gate fails.

---

## Appendix: probe results from the cloud VM (2026-09-19)

- `web/public/data/posts.json`: absent (runtime file, gitignored). No historical version in git.
- `data/tag_taxonomy.json`: absent; untracked at `f88b413a` (2026-07-15). Last tracked version: 212 tags, 67 multi-word, 62 ticker-shaped.
- Test fixtures containing `tags_text`: `web/tests/newsfeed-db-dual-write.test.ts`, `newsfeed-posts-api.test.ts`, `newsfeed-vision-tagger.test.ts`, `newsfeed-tag-hierarchy.test.ts`, `scripts/lib/demoMirrorReliability.test.js`; all synthetic, 1 to 3 posts each. Not usable as a corpus.
- Turso credentials: none in the VM environment. Corpus size must be counted on a credentialed host; last verified figure is 3,474 posts (2026-07-20, `tasks/knowledge-base-plan.md`).
- Hetzner host size: not recorded anywhere current in the repo; the deployment guide's CPX11 figure predates the present unit set.
- `research_post_sources` (migration 0071) exists, so the research-post exclusion in C.2 is required, not hypothetical.
- Ports in use per `cloud/services/*.service` and Caddy config: 8321, 8330, 8334, 8340, 8765. 8331 is free on paper.
