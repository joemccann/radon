# Flow terminal: no-slop review of the TZU reference

Scope: the SPX 0DTE desk screenshot that @BuildwithPublic posted on 2026-10-12
(`docs/design/flow-terminal/before-tzu.jpg`, 1919x989) scored as the "before",
and the Radon direction plate (`docs/design/flow-terminal/index.html`, rendered
to `after-light.png` and `after-dark.png` at 2x) scored as the "after". The plate
is static with synthetic values copied from the screenshot; nothing here is
measured market data. Build plan: `tasks/flow-terminal-plan.md`.

## Scores

0 = none, 3 = severe. Evidence is pixel position in the before image.

| Tell | Before | Evidence (before) | After | What changed |
|---|---:|---|---:|---|
| Equal-weight tile grid | 3 | Row C: eight identical KPI boxes (y 118-186), same type scale; DAY NET +$0.00 weighs the same as NET DEALER γ +$1.53B | 0 | One finding sentence owns the regime; every other figure is a ledger line under it, same mono size |
| Dead tiles | 3 | DAY NET `+$0.00 · 0/6 trades · — win · cap 0%`; PAPER POSITION `flat`; PAPER TODAY `+0.00 · 0W 0L`: three full slots for "nothing happened" | 0 | No P&L surface until a trade exists; caps are one muted telemetry line under the arm button |
| Badge confetti | 2 | 11 pill groups across four panels: SPX/SPY, Tick..1h, γ/Ch/Va/Buy, Bull/Bear, Any/2+/3+/4+, OI+tape/Tape only, 1.5x/2x/3x, On/Off, BLK badges per row, SPX·PAPER, Sound on | 1 | Two toolbar segments on the instrument, three in the engine; mode stated once in the nav |
| Repeated status | 3 | `paper` appears 7 times (top badge, DAY NET label, EXECUTION title, RISK row, strip header, two cards); `hunting` 3 times; Net γ / Charm / 0γ / EM printed in the KPI row AND the ladder footer; `history 84 strikes · 0 bar prints` twice | 0 | Each fact has one owner: mode in the nav, regime in the finding, levels in the ledger, state in the engine sentence |
| Icon + title + help + pill | 1 | Headers are title plus pills; no icons | 0 | Title, then a mono provenance line |
| Semantic color drift | 2 | Green means calls, positive P&L, window open and LONG γ at once; puts are orange on the chart but red in the tape; 7835C bid-hit print is green | 0 | Bubble fill = call (solid) or put (hollow), bubble tone = edge clarity, ladder teal = long γ, magenta = short γ, red reserved for the signed net-premium line |
| CAPS/mono everywhere | 3 | Every label, the rules panel body and the model paragraph are uppercase tracked mono | 0 | Mono for figures and telemetry only; the model text and engine state are sentence-case Inter |
| Machine formatting | 1 | `2026-10-12`, `7,825.00` axis tags, `$1.00-1.50`; strikes already read `7835C` | 0 | `12 Oct 0DTE`, strike axis without decimals, `5h 44m to settle` |
| Generic copy | 1 | `PRINT RULES`, `RISK`, `DEALER MODEL estimate`; `hunting for a print` is the one line with a voice | 0 | The engine says what it is doing: "Flat. Watching call prints at edge 3 or better. None since the open." |
| Gradients, glow, shadows | 2 | Ladder bars glow; bubbles carry white halos; chart pane is navy inside a black page | 0 | Flat bars at 75% opacity on the shared axis; hairlines; one canvas |
| Truncation | 2 | Tape EDGE column clipped at the panel edge; bottom-right cards cut; two scrollbars | 0 | Tape fits a 384px rail at 12px mono with all seven columns; nothing scrolls inside a panel in the plate |
| Split scales (design.md failure pattern) | 3 | Dealer ladder spans 7900 to 7710 while the chart spans 7800 to 7825: the two y axes cannot be read against each other, which is the whole point of a dealer map | 0 | Ladder bars sit on the chart's own price axis; out-of-range walls and the flip are pinned to the plot edge with their distance |

Before average 2.2 (fails). After average 0.1.

## Three directions considered

1. Reconstruction ledger: dealer ladder as the hero, chart demoted, everything else a ledger rail.
2. Tape first: print tape fills the left column, chart on the right, engine as one status line, no levels panel.
3. Decision strip (chosen): one sourced finding sentence, chart and ladder fused on one price axis, tape and engine in the existing 384px instrument rail, model and scorecard behind a disclosure.

Direction 3 was chosen because the fused axis fixes the only structural defect
in the reference (split scales) and because the rail reuses the chain-first
instrument workspace that already exists (`web/DESIGN_MEMORY.md`, B / Chain
first), so the terminal reads as a Radon instrument rather than a new app.

## Harsh critic pass

- The finding sentence is the point of view. If the model cannot produce it
  (no chain, no tape) the page must say what is missing in the brand error
  pattern, never render an empty sentence.
- The scorecard is the honesty surface the reference promised and never showed.
  It ships in the first UI milestone, not later.
- Risk: the ledger line can grow into a tile row by another name. Hard cap of
  six items; anything else goes behind the Model disclosure.
- Not verified here: mobile. The plan reflows to one column with the ladder
  collapsed to the three levels and the engine as a bottom sheet; it must be
  captured at 393x852 before the UI milestone closes.

## Plates

- `docs/design/flow-terminal/before-tzu.jpg`: the reference.
- `docs/design/flow-terminal/after-light.png`, `after-dark.png`: direction 3 at
  1440x1000, 2x, Clear tokens from `web/app/globals.css`.
