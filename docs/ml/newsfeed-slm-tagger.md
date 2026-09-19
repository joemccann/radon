# Newsfeed text-tagger SLM v1: build spec

Status: PLAN. Nothing in this document is implemented. The implement PR is a separate draft PR that follows this file literally and is merged only by Joe.

Honesty label, to be carried verbatim on the model card, the service unit description, the ladder log prefix and any UI or PR text that mentions the model:

> Radon SLM tagger: internal specialist, not a SotA replacement for open research.

(The label is rendered with a colon, never an em dash; see the copy rule in `CLAUDE.md`.)

Prior research this spec reconciles with and does not repeat:

- [`docs/research/radon-small-model.md`](../research/radon-small-model.md), sections 2 and 5: the tagger alone is worth tens of dollars a year, so the dollar case is thin; a bounded Phase-1 distillation experiment on a 0.8B to 4B model for tagging is still recommended.
- [`docs/research/radon-harness-and-model-roadmap.md`](../research/radon-harness-and-model-roadmap.md), roadmap row M1: a local tagger behind the provider layer, callable as a narrow tool, never the decision surface.

This spec is that M1 rung, scoped to the Market Ear **text** tagger only.

---

## A. Problem, honesty, non-goals

### The job

`scripts/newsfeed/tagger.js` labels every Market Ear post with exactly 3 UPPERCASE kebab tags. It runs on every post (text tagger always; vision tagger additionally when the post has an image; see `hydrateTagsDual` in `scripts/newsfeed/vision_tagger.js`). Volume is roughly 20 to 25 posts per day (3,474 posts over about five months as of 2026-07-20, `tasks/knowledge-base-plan.md`). Each post costs one walk of the shared model ladder.

### Why do this at all, in honest order

1. **Ladder pressure relief and Cerebras-last hedge.** Every text tag today spends subscription quota (Anthropic, Grok, Codex, Gemini) or falls to NVIDIA, then Cerebras (paid, last). The tagger shares that ladder with knowledge distill and the research reviewer. Taking the highest-volume, lowest-difficulty caller off the paid and subscription rungs leaves quota for the callers that need frontier quality. When every keyed provider is down or quota-exhausted, posts land untagged (`tagPost` soft-fails to `null`); a local rung keeps the dashboard chips populated.
2. **Determinism and format validity.** A grammar-constrained local model returns `{"tags": [a, b, c]}` every time. The current path sometimes returns prose or fewer than 3 tags and is dropped by `normaliseTags(...).slice(0, 3)`.
3. **Latency, conditionally.** On Apple silicon or a 4+ vCPU host a 1.5B model answers in 1 to 4 seconds. On a 2 vCPU shared VPS it can be slower than a subscription API call. Latency is a win only where the hardware permits; section D makes the host choice explicit and section F gates enablement on measured p95.
4. **Reusable pattern.** Dataset extract, LoRA on the Mini, GGUF export, eval harness, sidecar unit, ladder rung. Distill and reviewer heads (phase 2) reuse the pattern; they are OUT of this PR and OUT of the implement PR.

### What this is not

- Not a monetization product. No selling, licensing or publishing of the model or the corpus.
- Not a replacement for the vision tagger (`vision_tagger.js` keeps its Anthropic path; the NVIDIA vision fallback is unchanged).
- Not a research assistant, distill head, reviewer head or general chat model.
- Not a claim of "better than the frontier model". The only claim allowed is the task-specific one in section F, on the held-out gold set, with the confidence interval printed.
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

`SLM_SYSTEM` is one fixed string (section H.3). The training pairs use it; the ladder rung sends it. The full taxonomy list is deliberately not in the SLM prompt: the taxonomy is what the weights learn, the prompt stays short enough for CPU inference, and taxonomy growth is handled by retraining (section J).

### C.3 Splits: time only

Random splits leak near-duplicate posts across train and test and flatter the score. Split on `timestamp`:

| Split | Window | Purpose |
|---|---|---|
| test | most recent 60 days at extract time | held-out gold for section F; frozen with its cutoff dates in `manifest.json` |
| valid | the 30 days before test | early stopping, hyperparameter selection |
| train | everything older | training |

Expected sizes from the last verified count (3,474 posts at 2026-07-20, about 20 to 25 per day): roughly 4,500 total by 2026-09-19, of which test is about 1,200 to 1,500 and train about 2,500 to 3,000 after exclusions. If train drops below 1,500 usable rows, shrink test to 45 days before touching valid. Report the actual counts in the implement PR body.

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

### D.2 Base model

Recommended: **`Qwen/Qwen2.5-1.5B-Instruct`** (Apache 2.0).

Why this and not the others in the 1 to 3B class:

| Candidate | Licence | Fit | Verdict |
|---|---|---|---|
| Qwen2.5-1.5B-Instruct | Apache 2.0 | Mature paths in mlx-lm (`mlx_lm.lora`, `mlx_lm.fuse`) and llama.cpp (`convert_hf_to_gguf.py`, `llama-server` with json_schema grammars); 1.5B is the largest size whose Q4_K_M (about 1.1 GB file, about 1.5 GB resident at 2k context) can plausibly share a small VPS; strong instruction following for its size | **v1 base** |
| Qwen2.5-0.5B-Instruct | Apache 2.0 | Half the memory (Q8_0 about 0.6 GB); weaker on rarer tags | **RAM fallback** if D.4 fails at 1.5B and Joe declines a host upsize |
| Qwen3.5-0.8B / 2B (recommended in `radon-small-model.md`) | Apache 2.0 | Newer; hybrid thinking must be disabled for classification; toolchain support for the newer architecture is a per-version check | **Upgrade candidate for v1.1**, tried only if the 1.5B fails gate G2 by more than the margin. Newness is not a reason on its own. |
| Llama-3.2-1B / 3B-Instruct | Llama 3.2 Community License | Attribution and naming obligations, acceptable-use terms; 3B is too large for the serve host | Rejected on licence friction for zero capability gain |
| Phi-4-mini | MIT | 3.8B, too large for the serve host; no evidence it beats Qwen at 1.5B on short classification | Rejected on size |
| Gemma 4 E4B | Apache 2.0 | 4.5B effective; the image path is the only reason to want it and vision is out of scope | Rejected for v1 |

Exactly one base ships in v1. Changing it is a version bump (section E), not a config flag.

### D.3 Train stack

Recommended: **mlx-lm LoRA on the Mac Mini.** Zero spend, no prepaid key anywhere in the loop.

The task brief named Unsloth or Axolotl. Both are CUDA-first; Axolotl has documented MPS constraints and Unsloth's core library does not train on Apple silicon (`radon-small-model.md` section 2 tooling table). Unsloth is the sanctioned alternative only if Joe would rather rent one GPU hour than use the Mini; that path costs about one to two dollars on a T4/L4 spot instance and is the only case where a cloud key enters the training loop. It is not the recommendation.

Files (implement PR):

```
scripts/newsfeed/slm/
  build_dataset.py     C.2 extract, C.3 split, manifest
  review_cli.py        C.4 human-ok capture
  train.sh             mlx_lm.lora with the YAML below; refuses to run if any *_API_KEY is in env
  export.sh            mlx_lm.fuse -> convert_hf_to_gguf.py -> llama-quantize; writes manifest.json with sha256s
  eval.py              section F metrics; reads predictions JSONL, prints one JSON object
  predict_slm.py       held-out posts -> llama-server -> predictions JSONL with latency per row
  predict_ladder.py    held-out posts -> complete_text_json (subscription only) -> predictions JSONL
  shadow.py            section I shadow-mode row writer (imported by model_ladder_cli.py)
  configs/qwen25-1p5b-lora-v1.yaml
```

Starting configuration (`configs/qwen25-1p5b-lora-v1.yaml`), to be tuned only against `valid.jsonl`:

```yaml
model: Qwen/Qwen2.5-1.5B-Instruct        # bf16 weights. Never a 4-bit base: a quantized fuse does not convert to GGUF cleanly.
train: true
fine_tune_type: lora
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

Stop rule: stop at the checkpoint with the lowest validation loss; if validation loss has not improved for 300 iterations, stop early. Expected wall clock on an M-series Mini: under one hour. Record the final train/valid loss, iteration and wall clock in the model card.

Decoding at serve and eval time: greedy (`temperature 0`), `max_tokens 64`, grammar-constrained to the schema in H.3. Same settings in `predict_slm.py` and in the ladder rung; a mismatch invalidates the eval.

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
| `radon-slm-tagger-v1.Q8_0.gguf` | same, `Q8_0` | about 1.7 GB | B2; used only if Q4_K_M fails gate G2 and the memory gate still passes |
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

## F. Eval protocol and acceptance gates

### F.1 Baselines the implement PR must measure and print

| Id | Measurement | Against | Where it runs |
|---|---|---|---|
| B0 | Label-noise ceiling (C.5): ladder re-tag vs stored `tags_text`, 200 valid posts | itself | Mini (subscription creds) |
| B1 | Ladder vs test gold: exact-3, Jaccard, micro-F1, format validity, in-taxonomy rate, p50/p95 latency | `test.jsonl` labels and `gold_human.jsonl` | Mini |
| B2 | SLM vs test gold: same metrics | same | Mini for quality; **serve host** for latency (the only latency number that counts) |
| B3 | Cost per 1,000 posts: ladder = subscription calls (count) plus Cerebras dollars if any rung fell through (tokens times list price); SLM = 0 marginal dollars, report CPU-seconds per post and resident MB instead | accounting only | either |

All numbers are printed as one JSON object by `eval.py` and pasted into the implement PR body. Confidence intervals are paired bootstrap (1,000 resamples) on the per-post difference SLM minus ladder.

### F.2 Metric definitions

Predictions are normalised with the same function the product uses (`__normaliseTags` semantics, reimplemented in Python with a parity test against the JS fixtures in `web/tests/newsfeed-tagger.test.ts`).

- `format_valid`: response parsed to JSON, `tags` is a list, exactly 3 survive normalisation.
- `exact3`: the 3-tag sets are equal.
- `jaccard`: `|A ∩ B| / |A ∪ B|` per post, mean over posts.
- `micro_f1`: each `(post, tag)` pair is a positive; precision and recall over all pairs.
- `in_taxonomy_rate`: fraction of predicted tags present in the taxonomy snapshot frozen in `manifest.json`.
- `novel_tag_rate`: `1 - in_taxonomy_rate`, reported for both systems (drift proxy, section J).
- `human_accept`: on `gold_human.jsonl`, fraction of posts Joe marked `y`, or whose corrected set equals the prediction.
- `latency_p50_s`, `latency_p95_s`: wall clock per post including HTTP, measured on the serve host with the unit's real settings, 300+ posts, sequential.

### F.3 Acceptance gates for enabling the rung

All on the frozen test split, n at least 300 for machine metrics, n = 200 for human-ok.

| Gate | Rule | Meaning |
|---|---|---|
| G1 | `format_valid(SLM) >= 0.995` | the grammar works and the model does not degenerate |
| G2 | `jaccard(SLM) >= jaccard(ladder) - 0.02` and the bootstrap 95% CI lower bound of `jaccard(SLM) - jaccard(ladder) > -0.05` | non-inferior on tag overlap |
| G3 | `exact3(SLM) >= exact3(ladder) - 0.03` | non-inferior on exact sets |
| G4 | `in_taxonomy_rate(SLM) >= 0.90` and `novel_tag_rate(SLM) <= novel_tag_rate(ladder) + 0.02` | does not invent vocabulary faster than today |
| G5 | `latency_p95_s(SLM) <= 10` on the serve host, and every call under the rung timeout (H.4) | fits inside the per-post budget with room for a fall-through |
| G6 | `human_accept(SLM) >= human_accept(ladder) - 0.03` | within 3 points on the only true gold, matching the acceptance the prior research proposed |

Decision rule:

- Any gate fails: rung stays `off`; the model card records the failure; the next attempt is the 0.5B/Q8 or Qwen3.5 variant per D.2, not a threshold change.
- All gates pass and the G2 difference CI lower bound is `<= 0`: **non-inferior**. `shadow` then `prefer` are allowed (section I).
- All gates pass and the G2 difference CI lower bound is `> 0` and G6 difference is `>= 0`: **beats**. `primary` is allowed after the shadow window.

"Beat" therefore means: higher Jaccard against held-out gold with a confidence interval that excludes zero, and not worse on the human-ok slice. Nothing else is called a win.

---

## G. Serve design

### G.1 Stack: `llama-server` from llama.cpp, one binary, localhost

- OpenAI-compatible `POST /v1/chat/completions` with `response_format: {"type":"json_schema", ...}` so the grammar is enforced server-side; the caller still normalises.
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

If the pinned llama.cpp release does not honour `pattern`, drop it from the schema and rely on normalisation; record which in the model card. The schema has no `$ref`.

### H.4 Timeouts and budgets

- Rung read timeout `RADON_SLM_TAGGER_TIMEOUT_S`, default `12`. Health probe 1 s before the call; a failed probe is `slm-tagger:unavailable` and costs nothing.
- The Node per-post budget is `DEFAULT_FETCH_TIMEOUT_MS = 30_000` (R-466). With the rung first, worst case is 12 s SLM plus one subscription call. If the measured `p95(SLM) + p95(ladder) > 25 s` in shadow, raise the Node budget to 45 s in the same implement PR; a bound of any size satisfies R-466, an unbounded wait does not.
- Failure taxonomy appended to `attempted` exactly like other rungs: `slm-tagger:unavailable`, `slm-tagger:http_5xx`, `slm-tagger:unparseable`, `slm-tagger:network:<Exc>`, `slm-tagger:ok`.

### H.5 Env inventory

New optional variables, all added to `cloud/.env.example` and to `_OPTIONAL_LADDER_ENV` so the inventory tests stay green, none added to `cloud/config/required-env.txt`:

`RADON_SLM_TAGGER_URL` (`http://127.0.0.1:8331`), `RADON_SLM_TAGGER_MODE` (`off`), `RADON_SLM_TAGGER_TIMEOUT_S` (`12`), `RADON_SLM_TAGGER_THREADS`.

---

## I. Shadow-mode metrics and cutover checklist

### I.1 Shadow table (migration `0077_slm_tagger_shadow.sql`)

```sql
CREATE TABLE IF NOT EXISTS slm_tagger_shadow (
  post_id        TEXT NOT NULL,
  observed_at    TEXT NOT NULL,             -- ISO-8601 UTC
  model_version  TEXT NOT NULL,             -- manifest.json version
  tags_slm       TEXT,                      -- JSON array or NULL on failure
  tags_ladder    TEXT,                      -- JSON array or NULL
  ladder_provider TEXT,
  slm_status     TEXT NOT NULL,             -- ok | unavailable | timeout | unparseable | http_<code>
  slm_latency_ms INTEGER,
  ladder_latency_ms INTEGER,
  exact3         INTEGER,                   -- 1/0/NULL
  jaccard        REAL,
  PRIMARY KEY (post_id, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_slm_shadow_observed ON slm_tagger_shadow(observed_at DESC);
INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (77, datetime('now'));
```

Written by `shadow.py` from the CLI with a fresh connection per call (Hrana rule 3), one row per post, never blocking the returned result (write failure is logged and dropped). Retention: 90 days, pruned by the existing `radon-db-retention.service` (add the table to its list). This table is also the provenance source for C.2 rule 6.

A read-only summary query (documented, not a UI panel) gives the weekly numbers:

```sql
SELECT date(observed_at) d, count(*) n,
       avg(slm_status='ok') ok_rate, avg(exact3) exact3, avg(jaccard) jaccard,
       avg(slm_latency_ms) slm_ms, avg(ladder_latency_ms) ladder_ms
FROM slm_tagger_shadow WHERE observed_at > datetime('now','-14 days') GROUP BY 1 ORDER BY 1;
```

### I.2 Cutover checklist (each step is a separate Joe approval)

1. **Offline gates pass** (section F) on the frozen test split; numbers in the implement PR body; model card committed.
2. **Serve host gate passes** (D.4); unit installed and pinned; `/health` 200; unit visible to the unit watchdog probe.
3. **`shadow` for 14 calendar days** (about 300 posts). Exit criteria to move on: `ok_rate >= 0.99`, production `jaccard` and `exact3` within 0.03 of the offline test numbers (otherwise the test split is not representative and the extract is re-run), `slm_ms` p95 under 10,000, zero unit restarts, no `radon-nextjs` memory alerts.
4. **`prefer`** for 14 days. `tags_text` now comes from the SLM on success. Watch: taxonomy growth rate (new tags per week) versus the trailing 8 weeks; dashboard chip complaints from Joe; `service_health`. Rollback is `RADON_SLM_TAGGER_MODE=off` and a newsfeed unit restart, nothing else.
5. **`primary`** only if the F.3 "beats" rule held offline and step 4 showed no regression. Subscription band skipped for the tags contract; NVIDIA and Cerebras remain as fall-through.
6. **Retire** means the mode stays `primary` and the subscription quota freed is visible in the ladder logs (`newsfeed-tagger try provider=` lines). No code is deleted; the ladder order is unchanged for every other caller.

---

## J. Risks

| Risk | Why it is real here | Mitigation in v1 |
|---|---|---|
| Open-vocabulary drift | The taxonomy is frozen in the weights at train time (212 tags at 2026-07-15, more now). New market concepts get mapped to the nearest known tag, or the model coins a plausible new tag that the normaliser accepts and the caller appends to the taxonomy. | Track `novel_tag_rate` in shadow and after cutover; gate G4; monthly retrain trigger when the ladder's own novel-tag rate over 30 days exceeds the SLM's by 0.05, or the taxonomy grows more than 10% since `taxonomy_sha256` in the manifest. In `prefer`/`primary`, keep appending the SLM's novel tags to the taxonomy exactly as today (no closed-vocab mode in v1 unless Joe asks; section L). |
| Taxonomy growth outpaces retraining | Retraining needs the Mini and a Joe-approved version bump. | Retrain is a one-hour job with a documented `train.sh`; the model card lists the taxonomy hash so staleness is visible; the fall-through to the ladder remains for every failure. |
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

Red/green order is mandatory (`CLAUDE.md` TDD rule). Every item names its test.

**Dataset**
- [ ] `scripts/newsfeed/slm/build_dataset.py` with id-cursor pagination; `test_slm_build_dataset.py` covers: research rows excluded, `tags` never used as a label, dedupe keeps earliest, time split boundaries, `buildUserPrompt` parity fixture, PII regex counts in manifest.
- [ ] `review_cli.py` writes `gold_human.jsonl` with `reviewed_at`; test on a 3-row fixture.
- [ ] Extract run on a credentialed host; `manifest.json` counts in the PR body; corpus not in the diff (`git status` clean of `data/slm/`).

**Train and export**
- [ ] `train.sh` refuses to start with any `*_API_KEY` in env (test via subprocess with a fake key).
- [ ] `configs/qwen25-1p5b-lora-v1.yaml` committed; training run on the Mini; loss curve numbers in the model card.
- [ ] `export.sh` produces adapter, F16 GGUF, Q4_K_M GGUF; `manifest.json` sha256s match uploaded B2 objects; `models/slm-tagger/v1/MODEL_CARD.md` carries the honesty label verbatim.

**Eval**
- [ ] `eval.py` metrics unit-tested on hand-computed 5-row fixtures (exact3, jaccard, micro_f1, in_taxonomy_rate, bootstrap CI shape).
- [ ] B0, B1, B2, B3 measured and pasted as one JSON block in the PR body; G1 to G6 each stated pass/fail with numbers; latency from the serve host, not the Mini.

**Serve**
- [ ] `cloud/services/radon-slm-tagger.service` committed and pinned in `cloud/config/installed-units.sha256`; `cloud/scripts/install-slm-model.sh` verifies sha256; llama.cpp release tag recorded.
- [ ] D.4 memory-gate query and result in the PR body.
- [ ] Unit-watchdog coverage confirmed by name (`radon-slm-tagger.service` appears in the `systemctl show 'radon-*'` probe on the host); no new `service_health` row.

**Ladder**
- [ ] `test_model_ladder.py`: `slm-tagger` absent from `MODEL_LADDER_ORDER` and `wired_providers()`; `complete_text_json` without `providers=` never attempts it even with URL and `primary` set; with `providers=` and a stubbed `post`, the OpenAI body carries `SLM_SYSTEM`, the schema, `temperature 0`, `max_tokens 64`, and the full URL string `http://127.0.0.1:8331/v1/chat/completions`; a 503 falls through to `anthropic` and `attempted` reads `("slm-tagger:http_503", "anthropic:ok")`.
- [ ] `test_model_ladder_cli.py`: each mode's provider order; `off` and unset are byte-identical to today's request; `accept == "distill"` never gets the rung in any mode; shadow row written with the right status on success, timeout and unavailable.
- [ ] `cloud/.env.example` and `_OPTIONAL_LADDER_ENV` list the four new variables; `required-env.txt` unchanged; `cloud/tests/test_env_example.py` green.
- [ ] Node untouched; `web/tests/newsfeed-tagger.test.ts` and `newsfeed-vision-tagger.test.ts` green unchanged.

**Shadow**
- [ ] Migration `0077_slm_tagger_shadow.sql`; `migrate.py` accepts it; retention list updated; row verified in Turso from the serve host in `shadow` mode (one real post) before the PR leaves draft.

**Docs**
- [ ] This file updated from PLAN to IMPLEMENTED with the measured numbers; `scripts/newsfeed/CLAUDE.md` and `AGENTS.md` tagging section mention the rung and correct the taxonomy tracking claim; `docs/operations.md` gets the unit row; `docs/owners.json` rule `slm-tagger` satisfied.
- [ ] Full suites green (pytest and vitest, never concurrently on the laptop); CI green on the PR head; draft PR, not merged; `RADON_SLM_TAGGER_MODE` remains `off` in production until section L item 5.

---

## L. What Joe must approve

| # | Decision | Default proposal if Joe says "your call" | Blocks |
|---|---|---|---|
| 1 | Use the Mac Mini for about one hour of training plus eval runs; confirm its RAM | yes; 16 GB is enough for the 1.5B path | D.3 |
| 2 | Read `nproc`, `free -m` and 14-day `host_metrics` on the Hetzner app host and, if the D.4 gate fails, choose: upsize the host (monthly spend), fall back to 0.5B, or keep the rung `off` | fall back to 0.5B before spending | D.4, G.2 |
| 3 | One hour labelling 200 posts through `review_cli.py` | required; no substitute for gold | C.4, G6 |
| 4 | Weights storage: prefix under the existing B2 archive bucket (no new secret) or a new private bucket (new `RADON_MODELS_S3_*` secrets) | existing bucket, new prefix | E.2 |
| 5 | Enable `shadow` in production (`/etc/radon/env` change plus newsfeed restart) | after gates pass | I.2 step 3 |
| 6 | Move to `prefer`, later `primary` | each after its 14-day window | I.2 steps 4 and 5 |
| 7 | Merge the implement PR | never by an agent | K |
| 8 | Closed-vocabulary mode (SLM may only emit known taxonomy tags) versus today's open vocabulary | keep open vocabulary; revisit if `novel_tag_rate` rises | J |
| 9 | Any llama.cpp release pin added to the approved tool pins | pin the release used for export and serve | G.1 |

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
