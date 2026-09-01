# Design eval scenarios — design.md loop

Seed eval loop for the public agent-design surface, following the Vercel design.md methodology (guidance file + bounded public stylesheet + deterministic checks + frozen scenarios).

**Surface:**

| Piece | URL | Source |
|---|---|---|
| Guidance | `https://app.radon.run/design.md` | `web/public/design.md` |
| Stylesheet | `https://app.radon.run/radon.css` | `web/public/radon.css` |
| Rendered example | `https://app.radon.run/design-example.html` | `web/public/design-example.html` |

**Deterministic checks:** `web/tests/design-artifact-contract.test.ts` (vocabulary sync, radius cap, no shadows/gradients, token-only color, dual-theme parity, no em dashes).

## How to run a round

1. Give an agent one scenario prompt below plus its mock inputs, with `design.md` loaded. Same prompt, same data, same viewport (1280px) every round. Keep the first attempt, no rerolls.
2. Screenshot the full page in dark and light (`data-theme="light"`).
3. Score against the scenario rubric blind against the previous round's output.
4. Encode each accepted correction in the narrowest place that enforces it: judgment into `web/public/design.md` prose, repeatable mechanics into `web/public/radon.css`, mechanical failures into the contract test. Never hand-tune the generated page.

## Scenario 1 — Flow evaluation brief

**Prompt:** "Build a one-page flow evaluation brief for ticker XMPL from this data: five sessions of dark pool block prints (dates, venues, print counts, notional, VWAP distance, % of ADV), peer off-exchange share for XMPL and three peers, IV rank 22, and a proposed defined-risk call spread with its Kelly fraction. The reader decides whether to enter the trade."

**Rubric:**
- The supplied figures survive verbatim, each with source and time basis.
- Executive read in ten seconds: state, headline numbers, decision.
- The evidence table uses the full available width.
- Peer values share one scale.
- Decision presented as signal, structure, sizing math, decision; caveats present but subordinate.

## Scenario 2 — Weekly portfolio review

**Prompt:** "Build a weekly portfolio review from this data: net liq series for the week, per-position P&L table (ticker, structure, entry date, MV, P&L, week change), three exposure concentrations, and two data quality notes. The reader checks health and looks for positions needing action."

**Rubric:**
- Positions needing action are visually separable without red/green P&L color leakage (signal states only).
- Numeric columns right-aligned mono; totals reconcile with rows.
- Data quality notes rendered as `rd-note`/`rd-warn`, not buried and not alarmist.

## Scenario 3 — Incident postmortem

**Prompt:** "Build an incident postmortem page from this data: a timeline of six timestamped events (feed fault, detection, mitigation, recovery), impact metrics (minutes degraded, affected surfaces), root cause, and three follow-up actions. The reader verifies the incident is understood and closed."

**Rubric:**
- Voice follows the error pattern: system, failure, cause, recovery. No blame language, no drama.
- Fault color used for the operational fault only; recovery states use the teal family.
- Follow-ups are concrete and checkable, each with an owner surface.

## Feedback log

Append one line per accepted correction: date, scenario, correction, where it was encoded.

| Date | Scenario | Correction | Encoded in |
|---|---|---|---|
| 2026-08-31 | 1 | Baseline round: stylesheet + guidance shipped; dark/light renders verified in headless Chrome | Initial `design.md`, `radon.css`, contract test |
