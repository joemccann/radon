# AUDIT — Impeccable + frontend-design

**Target:** Instrument-module grammar consistency across scanners, dashboard snapshot cards, and shell  
**Mode:** Operate (operator workstation)  
**Date:** 2026-08-06  
**Method:** Read-only critique (skills: impeccable, frontend-design). Evidence from source + skill-stack baselines in `docs/design-shots/skill-stack/baseline/`.  
**Brand law:** `docs/brand-identity.md`, `brand/radon-brand-system.md` (matte panels, 4px max radius, hairline borders, device-label headers, mono telemetry, no soft shadows / glass / gradients as decoration).

---

## Thesis

Radon already has a real instrument grammar in code:

| Primitive | Role | Primary surface |
|---|---|---|
| `.panel-edge-trace` | Calibrated left gauge channel | Scanners, snapshot cards, `InstrumentPanel` |
| `.panel-eyebrow` | Mono device-label / module ID | Same |
| `.panel-title` | Sentence-case instrument name | Same |
| `.panel-meta-rail` | Engine / basis / confidence telemetry | Scanners (strong), hero `InstrumentPanel` (strong), snapshot cards (absent) |
| `ScannerInstrumentShell` | Rack-mount module for Flow family | `/scanner` modes |
| `InstrumentPanel` | Hero metric cell with meter + meta | Hero cards / regime slots |
| `.snapshot-card` | Actionable dashboard cells | `/dashboard` left column |

The system is **half-authored**: scanners and GARCH rails feel instrument-true; portfolio page, newsfeed, and mobile dashboard still read as **generic dark SaaS**. Grammar is present as CSS atoms but applied unevenly, so the product feels stitched from two eras rather than one rack of modules.

---

## What feels instrument-true

### 1. Scanner family (`ScannerInstrumentShell`)

- Device eyebrow (`THETA / 03`, `GARCH / 06`, `LEAP / 05`, `STRENGTH / 04`) + sentence-case title is correct device-label grammar.
- Full-height edge gauge (ticks + tone wash) reads as a mountable channel, not a SaaS accent bar.
- GARCH calibration rail (`ENGINE · UNIVERSE · LAST.SAMPLE · ACTIONABLE`) is the clearest “this is a measurement instrument” moment in the app — mono keys, dotted keys, factual values.
- Dense mono tables, capsule status badges, hairline section borders, matte `#0f1519` panels stay on-brand.

**Evidence:** `baseline-desktop-scanner-theta.png`, `baseline-desktop-scanner-garch.png`, `05-garch-calibration-rail.png`.

### 2. Dashboard snapshot cards (desktop structure)

- Portfolio / Orders / Opportunities use `panel-edge-trace` + `panel-eyebrow` + `panel-title`.
- Metric cells use mono labels over large values; P&L tone maps to signal/fault without retail neon.
- Split Working / Today’s fills list feels like a blotter module, not a widget card.

**Evidence:** `baseline-desktop-dashboard.png`.

### 3. Shell chrome (partial)

- Canvas / panel / line hierarchy holds: sidebar, top strip, footer health bar all stay matte and hairline.
- Footer service strip (`IB GATEWAY · RELAY · FLEX TOKEN · SERVICES n/n NOMINAL`) already speaks instrument telemetry language.
- Futures strip + sample age in the header are correct “always-on telemetry” instincts.

---

## What feels generic (category-interchangeable)

### A. Double device labels on the dashboard

Desktop dashboard stacks:

1. Section chrome: `PORTFOLIO` + count `01` + chevron  
2. Card chrome: `PORTFOLIO / 01` · `Account` + edge gauge  

Same for Orders (`WORKING & FILLED` / `03` then `ORDERS / 03 Working & Filled`) and Opportunities. That is **two competing module IDs for one rack slot**. Instrument grammar wants one mount label, not a collapsible folder wrapped around a second instrument.

**Reads as:** “dashboard accordion product” overlaid on “instrument modules,” not a single rack.

### B. Live Market Feed is pre-instrument

`DashboardNewsFeed` still uses:

- Lucide `Radio` icon + uppercase `.section-title` (“LIVE MARKET ANALYSIS”)
- SaaS “UPDATED … · REFRESH” action cluster
- No `panel-eyebrow`, no edge gauge, no meta rail for source / cadence / capture basis

This is the largest visual grammar break on the primary operator surface: left column is instrument-adjacent; right rail is 2024 fintech content card.

**Evidence:** desktop + mobile dashboard baselines.

### C. Edge gauges often decorate rather than measure

On snapshot cards the gauge sits at rest (`--edge-level` unset): ticks + faint wash only. Brand intent for the channel is a **calibrated meter** (level + cap marker). Resting gutters on every card make the motif ornamental; scanners rarely meter either.

Contrast: `InstrumentPanel` already wires `level` → `--edge-level`. Snapshot / scanner shells do not.

### D. Snapshot cards lack meta rails

Brand §4: metadata rails expose sampling rate, engine source, confidence, time basis.  
Scanners ship rails. Snapshot cards stop after body content. Operator cannot tell at a glance whether Account numbers are IB stream vs cache, or how old Opportunities samples are, without hunting tab meta.

### E. Module ID namespace collision

| Surface | IDs in use |
|---|---|
| Scanner Flow | THETA / 03, STRENGTH / 04, LEAP / 05, GARCH / 06 (implied FLOW 01–02) |
| Dashboard | Portfolio / 01, Live Market / 02, Orders / 03, Opportunities / 04, … |

`03` means Theta on Flow and Orders on Dashboard. Device labels only feel real when the **rack channel is unique product-wide** (or scoped with a channel prefix: `EXP·01`, `FLOW·03`).

### F. Portfolio page is legacy section grammar

`/portfolio` uses collapsible uppercase section labels (`ACCOUNT`, `RISK`, `DEFINED RISK POSITIONS`) and metric tiles **without** edge gauges, eyebrows, or meta rails. Same account figures appear on the dashboard as instrument modules and on portfolio as generic finance dashboard blocks.

**Evidence:** `baseline-desktop-portfolio.png`.

### G. Empty states and mobile strip identity

- Snapshot empties: mono uppercase “NO FILLS TODAY.” — terse but not measurement-condition copy (`SectionEmptyState` exists and is underused here).
- Mobile dashboard: section toggles collapse to bare `01` / `02` with titles dropped; instrument identity evaporates. Metric grid has no edge channel, no device label.

**Evidence:** `baseline-mobile-dashboard.png`.

### H. Soft glow on metered edge (token drift)

`.panel-edge-trace::after` uses `box-shadow: 0 0 6px var(--edge-acc)`. Brand bans soft shadows. Cap marker should be a hard 2px edge, not a consumer glow.

### I. ChartPanel hybrid

`ChartPanel` uses family kicker (good) but still uppercase `.section-title` + optional lucide icon — closer to SaaS chart widget than instrument module. Not the focus of this audit, but same grammar debt.

### J. Control-row noise on GARCH

Header shows `1 ACTIONABLE` pill, filter bar shows `ACTIONABLE 1`, rail shows `ACTIONABLE 1`, and two adjacent SCAN controls (ticker search + global). Density without a single primary control hierarchy.

---

## Design specificity verdict

| Axis | Score (1–5) | Note |
|---|---:|---|
| Color / material (matte, tokens) | 4 | Strong on-brand surfaces |
| Type roles (sans narrative / mono telemetry) | 4 | Consistent where applied |
| Device-label + edge grammar | 3 | Present, uneven, sometimes double-labeled |
| Meta rails / measurement honesty | 2.5 | Scanners yes; dashboard/news/portfolio no |
| Cross-surface module system | 2 | Two numbering schemes; portfolio/news legacy |
| Signature vs generic dark UI | 3 | GARCH rail + edge gauge are signature; news + portfolio pull toward generic |

**Overall:** authored product with a clear instrument thesis that is **not yet enforced as a system**. Generic moments are not “ugly”; they are **interchangeable** — another dark trading dashboard could ship them unchanged.

---

## Eight concrete visual upgrades (on-brand)

These stay inside brand law: tokens only, 4px max panel radius, hairline borders, matte, device labels, mono telemetry, no glass/gradients/soft shadows/emoji/em dashes.

### 1. Single mount label per dashboard slot

Drop the redundant outer `dashboard-section` title row **or** the inner `snapshot-card` eyebrow/title — not both. Prefer: outer row becomes a pure collapse affordance (chevron + optional count), card owns `panel-eyebrow` + `panel-title` + edge gauge. One device label per rack unit.

### 2. Instrument-ize Live Market Feed

Replace icon + uppercase section-title with the same shell atoms as snapshot cards: edge gauge, `FEED / 02` (or product-scoped channel ID), sentence-case title, meta rail `source · capture.basis · last.sample`. Refresh becomes a mono control in the header rail, not a SaaS “Refresh” pill with lucide spinner as the visual hero.

### 3. Meter snapshot (and scanner) edge gauges

Wire meaningful `--edge-level` values so gutters measure:

- Portfolio: open risk / bankroll or margin utilization  
- Orders: working count vs recent max / session cap  
- Opportunities: highest candidate confidence or count intensity  
- Scanners: optional scan progress or actionable density  

Resting gauges remain only when data is truly unavailable (awaiting feed).

### 4. Meta rails on every snapshot card

Add a 20–24px `panel-meta-rail` under Portfolio / Orders / Opportunities:

- Portfolio: `source ib · basis stream|cache · as.of HH:MM:SS`  
- Orders: `working n · filled.today n · session ET`  
- Opportunities: `engine <tab> · last.sample · universe`  

Match scanner rail key style (`lowercase.dotted` keys).

### 5. Product-scoped module IDs (kill collisions)

Adopt a channel prefix so IDs never collide across routes:

- Flow rack: `FLOW·01` … `FLOW·06` (or keep THETA/GARCH names under `FLOW·`)  
- Exposure / account rack: `EXP·01` Account, `EXP·02` Feed, `EXP·03` Orders, `EXP·04` Candidates  

Same mono 10px / 0.14em tracking; change only the namespace string.

### 6. Bring `/portfolio` Account strip onto instrument grammar

Top Account metrics row: edge channel + `EXP·01` / Account (or `PORTFOLIO / ACCOUNT`) + optional meta rail (`source ib · currency USD`). Collapsible Risk/Margin blocks can stay denser but should inherit hairline panel + mono section labels consistent with device labels, not a separate “settings accordion” look.

### 7. Measurement-condition empty states

Swap bare mono “NO FILLS TODAY.” / silent empties for `SectionEmptyState` (or rail-aligned copy) that states **measurement conditions**: session window, filter state, feed readiness — never generic “nothing here.” Keep compact variant inside snapshot cards so height stays instrument-tight.

### 8. Hard edge cap; kill soft glow + restore mobile identity

- Remove `box-shadow` from `.panel-edge-trace::after`; keep 2px solid cap marker only (matte, calibrated).  
- On mobile dashboard, keep a compact device label (eyebrow or short title) next to the collapse control so slots do not collapse to orphaned `01` / `02`. Prefer left-aligned primary readout + secondary telemetry, per brand narrow-viewport rule — not a centered empty cluster.

---

## Priority ordering (if implementing)

| Order | Upgrade | Why first |
|---:|---|---|
| P0 | 1 Single mount label | Removes immediate visual double-speak on primary surface |
| P0 | 2 Feed instrument shell | Largest left/right grammar break on dashboard |
| P1 | 4 Snapshot meta rails | Completes brand §4 for half the product |
| P1 | 3 Metered gauges | Turns motif from decoration into measurement |
| P1 | 8 Hard edge + mobile labels | Token integrity + mobile identity |
| P2 | 5 Scoped module IDs | System-wide clarity |
| P2 | 6 Portfolio page | Cross-route consistency for same account data |
| P2 | 7 Empty states | Copy / calm density polish |

---

## Out of scope (noted, not upgraded here)

- Soft glow / toast stack / issues chip chrome (operational overlays present in baselines).  
- ChartPanel / regime deep views (same grammar debt; separate pass).  
- GARCH dual SCAN control hierarchy (control design, not pure grammar).  
- Brand-system.md legacy green (`#3CB868`) vs enforced teal (`#05AD98`) doc drift — identity doc wins; do not re-palette.

---

## Evidence index

| Artifact | Path |
|---|---|
| Desktop scanner Theta | `docs/design-shots/skill-stack/baseline/baseline-desktop-scanner-theta.png` |
| Desktop scanner GARCH | `docs/design-shots/skill-stack/baseline/baseline-desktop-scanner-garch.png` |
| Desktop dashboard | `docs/design-shots/skill-stack/baseline/baseline-desktop-dashboard.png` |
| Desktop portfolio | `docs/design-shots/skill-stack/baseline/baseline-desktop-portfolio.png` |
| Mobile dashboard | `docs/design-shots/skill-stack/baseline/baseline-mobile-dashboard.png` |
| Shell primitive | `web/components/ScannerInstrumentShell.tsx` |
| Hero primitive | `web/components/instruments/InstrumentPanel.tsx` |
| Snapshot cards | `web/components/dashboard/*SnapshotCard.tsx`, `OpportunitiesCard.tsx` |
| Grammar CSS | `web/app/globals.css` (`.panel-edge-trace`, `.instrument-section`, `.snapshot-card`, `.instrument-panel`) |
| Newsfeed (generic) | `web/components/DashboardNewsFeed.tsx` |
| Baseline notes | `docs/design-shots/skill-stack/BASELINE.md` |

---

## The 8 upgrades (checklist)

- [ ] **1.** Single mount label per dashboard slot (drop double section + card eyebrows).  
- [ ] **2.** Instrument-ize Live Market Feed (edge + eyebrow + title + meta rail).  
- [ ] **3.** Meter snapshot/scanner edge gauges with real `--edge-level` signals.  
- [ ] **4.** Add `panel-meta-rail` to Portfolio / Orders / Opportunities cards.  
- [ ] **5.** Product-scoped module IDs (`FLOW·` / `EXP·`) so numbers never collide.  
- [ ] **6.** Port `/portfolio` Account strip onto instrument grammar.  
- [ ] **7.** Measurement-condition empty states via `SectionEmptyState` language.  
- [ ] **8.** Hard edge cap (no soft glow) + keep mobile device labels on collapse rows.
