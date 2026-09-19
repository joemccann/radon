# radon-slm-tagger v1

Radon SLM tagger: internal specialist, not a SotA replacement for open research.

## Base

- `Qwen/Qwen2.5-1.5B-Instruct` (Apache 2.0)
- Train (default): LLaMA-Factory QLoRA/LoRA (`scripts/newsfeed/slm/train.sh`, `configs/llamafactory-qwen25-1p5b-qlora-v1.yaml`). Train only; not runtime.
- Train (optional Mini): `SLM_TRAINER=mlx` + `configs/qwen25-1p5b-qlora-v1.yaml`
- Serve: llama.cpp `b6504` GGUF Q4_K_M via `radon-slm-tagger.service` on 127.0.0.1:8331

## Dataset

- Sources: `["turso.posts"]` only (HR-8)
- Operator extract (credentialed host):

      python3.13 scripts/slm/export_dataset.py --out data/slm

- Time split (HR-4): test = last 60d, valid = 30d before test, train = older
- Taxonomy is live Turso `tag_taxonomy` / `data/tag_taxonomy.json`, not weights (HR-5)
- Corpus is Market Ear subscription content. Never commit `data/slm/`.

## Prompt contract

The live tagger system prompt (`tagger_system_prompt` in `scripts/newsfeed/slm/contract.py`, byte-identical to `buildSystemPrompt` in `tagger.js`) plus the `buildUserPrompt` user turn; the rung forwards the caller's prompt verbatim. Loss masked to the assistant JSON (`train_on_prompt: false` in LLaMA-Factory; `mask_prompt: true` in the mlx-lm alternative).

## Eval

Fixture-limited bakeoff only in this implement PR. Live A/B/C numbers require the Mini (train + arm A subscription eval) and the Hetzner serve host (latency). Until that table exists the rung stays `off`.

Train / valid loss belong on this card after the Mini run. They are not acceptance gates (HR-6).

## Known failure modes

- Out-of-taxonomy tags abstain and fall through the ladder (`slm-tagger:abstain:out_of_taxonomy`)
- Empty or wrong-count lists abstain (`abstain:empty`, `abstain:count`)
- Unparseable output is never forwarded to Node
- Self-distillation is blocked: rows with `label_source=slm` are dropped at extract
