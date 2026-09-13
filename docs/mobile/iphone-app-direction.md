# iPhone app direction

Foundation only. Native iOS is **not shipping**. This page locks the design/build
loop and the Radon constraints any future iPhone surface must inherit. It does
not start an App Store submission, an Xcode project, or a rewrite of the web UI.

**Status:** direction. **Owner:** this file. **Scaffold:** [`apps/ios/README.md`](../../apps/ios/README.md).

## Source

The design loop is adapted from Breeje Anadkat's AI app-design workflow:

- https://x.com/breejeanadkat/status/2098728437133476089

Joe saved that thread as skill `mobile-app-ai-design-loop`. Credit stays on this
page. The Radon mapping below is ours: Chat today, native iOS later, trading
safety unchanged.

## Why this exists

Radon already has a mobile web shell (PWA, 393x852, `body[data-mobile="true"]`)
and product Chat. An iPhone app is a later native client of the same operator
workstation, not a second product. The web shell stays the live mobile surface
until a Figma lock and a human go-ahead say otherwise.

## Surfaces this loop applies to

| Surface | Role now | Role later |
|---------|----------|------------|
| Product Chat (`ChatPanel`, `web/app/api/assistant/route.ts`) | Live AI draft + propose/confirm | Same contract on iOS |
| Mobile web shell (`web/components/mobile/`) | Live phone UI | Reference and constraint source, not a throwaway |
| Native iOS (`apps/ios/`) | README stub only | Interactive MVP after Figma taste lock |

## Design loop

Map the source phases onto Radon. Do not skip a phase because Chat or Claude
Code can emit a screen.

| Phase | Source move | Radon move |
|-------|-------------|------------|
| 1. References | Collect visual references first. Do not start from a prompt. | Dump brand plates, the live mobile order sheet, dark-pool tables, Chat composer, and IB status chip. Brand tokens: [`docs/brand-identity.md`](../brand-identity.md). Public agent-design loop: [`docs/design-evals.md`](../design-evals.md). |
| 2. AI draft | First model output is a draft. | Treat Chat or any generator output as a sketch. Never ship it as final UI. |
| 3. Specific critique | Name what is wrong: layout, UX, color, interaction. | Critique against the constraints below. Vague "make it nicer" is not a pass. |
| 4. Figma taste lock | Taste lives in Figma. AI gets close; Figma makes it ours. | Lock frames before implementation. No vibecode-as-final-UI. |
| 5. Interactive MVP | Only after lock, build real flows. | Build the interactive MVP from the locked frames. Chat may propose; it does not skip Figma. |

## Principles

- **No vibecode-as-final-UI.** Generated structure and components are a draft.
- **Figma lock before implementation.** Phase 5 does not start without locked frames.
- **First AI output is a draft.** Taste and specific critique are the product step.
- **Trading safety: propose, then human-yes.** Chat already states this: destructive
  actions are never executed automatically; the model proposes and the operator
  confirms (`web/app/api/assistant/route.ts`). Native iOS inherits the same rule.
  The rule in one line: never auto-trade from AI. No silent place, modify, or cancel.
- **Same math, same gates, same broker.** iOS does not grow a second order path.

## Mobile-first Radon constraints

These are the constraints that matter on a phone. A draft that misses one fails
critique, including a native draft.

### Order sheet

One sheet scroll. Legs, size, quote, limit, TIF, risk grid, and payoff live in
the body scroller. The pinned footer is a compact thumb-zone: status, teaser
(pay / max loss / max gain), Clear / Review. Nothing that can grow is pinned.
Reference: [`docs/design-shots/mobile-order-size/README.md`](../design-shots/mobile-order-size/README.md).

Every order surface routes through `OrderRiskGate`. Transmit stays closed until
coverage resolves and, when max loss is unbounded, the operator ticks the ack
that names where the position turns loss-making. Chat confirm is the same gate,
not a back door. iOS must call the same place/modify/cancel APIs with the same
payload shape.

### Dark pool surfaces

Flow signal or nothing. Phone density is not a license to drop venue, notional,
VWAP distance, print count, or % of ADV. A block that moved price is not edge.
HISTORY and flow tables must remain readable at ~390px (wrap, sticky DATE) rather
than clipping the evidence.

### Four-gate language

Evaluations and tickets speak `signal -> structure -> Kelly math -> decision`.
Gates stay sequential. A failing gate is a stop; name the gate. Do not
rationalize.

1. Convexity: gain at least 2x loss. Defined-risk only.
2. Edge: a specific dark-pool / OTC print that has not moved price.
3. Risk: fractional Kelly. Hard cap 2.5% bankroll per position.
4. Naked-short gate: **disabled 2026-04-30**. Logic stays in `_*Impl`. Do not
   re-enable from an iOS sketch. See `docs/naked-short-reenable.md`.

Copy on the phone uses this vocabulary. Do not invent a softer mobile dialect.

### IB connection state

The chip is live broker state, not chrome. Read the same
`IBStatusContext.displayStatus` states the web bar uses: `connected`,
`awaiting_2fa`, `unhealthy`, `unreachable`, `ib_offline`, `relay_offline`.
Amber for `awaiting_2fa`. Offline is "showing last known data", never a silent
stale book. A missing or unauthenticated gateway is not a reason to skip IB and
is not a reason to invent a quote.

## Non-goals

This foundation does **not**:

- not rewriting the web UI, and not restyling it as a side effect
- claim native iOS is shipping, in TestFlight, or in review
- start an App Store submission, signing, or release pipeline
- stand up a full Xcode project, CocoaPods/SPM app target, or CI App Store job
- change trading gates, Kelly caps, or the propose-then-human-yes Chat contract
- train or fine-tune models
- merge without Joe

## What comes after this PR

Joe reviews. If the loop is right, the next work is references + Figma, not a
repo-wide iOS target. Keep this file as the owner when that work lands.
