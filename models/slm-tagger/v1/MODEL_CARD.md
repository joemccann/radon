# radon-slm-tagger v1

Radon SLM tagger: internal specialist, not a SotA replacement for open research.

## Base

- `Qwen/Qwen2.5-1.5B-Instruct` (Apache 2.0)
- Train: mlx-lm QLoRA on the Mac Mini (`scripts/newsfeed/slm/train.sh`)
- Serve: llama.cpp `b6504` GGUF Q4_K_M via `radon-slm-tagger.service` on 127.0.0.1:8331

## Dataset

- Sources: `["turso.posts"]` only (HR-8)
- Operator extract (credentialed host):

      python3.13 scripts/newsfeed/slm/extract_cli.py --out data/slm/tagger/v1

- Time split (HR-4): test = last 60d, valid = 30d before test, train = older
- Taxonomy is live Turso `tag_taxonomy` / `data/tag_taxonomy.json`, not weights (HR-5)
- Corpus is Market Ear subscription content. Never commit `data/slm/`.

## Prompt contract

`SLM_SYSTEM` in `scripts/newsfeed/slm/contract.py`. Loss masked to the assistant JSON (`mask_prompt: true`).

## Eval

Fixture-limited bakeoff only in this implement PR. Live A/B/C numbers require the Mini (train + arm A subscription eval) and the Hetzner serve host (latency). Until that table exists the rung stays `off`.

Train / valid loss belong on this card after the Mini run. They are not acceptance gates (HR-6).

## Known failure modes

- Out-of-taxonomy tags abstain and fall through the ladder (`slm-tagger:abstain:out_of_taxonomy`)
- Empty or wrong-count lists abstain (`abstain:empty`, `abstain:count`)
- Unparseable output is never forwarded to Node
- Self-distillation is blocked: rows with `label_source=slm` are dropped at extract
