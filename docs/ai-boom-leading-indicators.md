# AI Boom Leading Indicators (WIP)

As of: 27 Aug 2026 PT
Author: AI Boom Watch
Audience: Joe McCann / Radon

Working quant memo for the high-frequency AI-boom tapes. Reconstructed from
locked facts: the operator-box source file
(`/workspace/ai-boom-brief/AI-boom-leading-indicators.md`) was not visible
from this checkout, so the turn rules, cash conversion prints, and failure
modes that live in the box file are pending and will be added on this PR.
No numbers below are invented; every print comes from the locked facts.

## Source ranking

Top sources, in order:

1. OpenRouter
2. Vercel
3. EIA-930
4. GPU ledger + AA
5. Portkey scrape

## Token tape

Three hosts. Do not splice them into one series.

| Host | Series | Notes |
|---|---|---|
| OpenRouter | rankings-daily, LEVELS | |
| Vercel | leaderboard-export, SHARE | Share only, from 2025-10-01 |
| Portkey | `/rankings/daily`, LEVELS scrape | No public API. 27 Aug print: 380.3M req / 4.4T tok / $3.8M spend |

## GPU tape

- `gpurentalprices.com/api/latest.json`, daily.
- 54 snapshots, 2026-07-05 to 2026-08-27. 293 offers, 34 providers.
- H100 firm at $1.99 (Voltage Park).
- Full history is licensed.
- GitHub: `adriannutiu/gpu-rental-prices`.

## App tape

- `Socialpranker/token-history`. 11 files only: 2026-06-10 to 2026-06-18 and
  2026-08-27/28.
- Hermes Agent: 1.65T tokens / 17.1M requests on 28 Aug.
- Do not interpolate the hole between the two windows.
- Do not add this tape to rankings-daily.

## Price tape

- `jvrck/openrouterlist` `prices.json`, from 2024-09-21.
- 984 models as of 28 Aug.

## Watch only

- AnyRouter public JSON is too small: 6.85B tok / 85k req over 30 days, 87%
  cache. Watch only.

## Scouting

- X search on 27 Aug found no new public meter.

## Pending from the operator box file

- Turn rules
- Cash conversion prints
- Failure modes
