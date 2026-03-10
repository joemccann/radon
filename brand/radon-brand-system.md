# Radon Brand Identity — Technical Specification Document
**Version:** 1.0  
**Product:** Radon  
**System Type:** Institutional market structure reconstruction instrument  
**Brand Thesis:** *Reconstructing market structure from noisy signals.*

---

## 1) Visual Evolution — From Dashboard to Instrument

Radon should not read as a retail trading app, a hype-heavy scanner, or a generic fintech dashboard. It should feel like a calibrated instrument used to inspect, decompose, and reconstruct hidden structure from incomplete market data.

### Naming migration
The prior "Convex Scavenger" naming should be retired in favor of a cleaner instrument hierarchy.

| Legacy | New Radon Hierarchy | Role |
|---|---|---|
| Convex Scavenger | **Radon Terminal** | Primary operating environment |
| Scanner | **Radon Flow** | Flow reconstruction and event isolation |
| Alerts | **Radon Signals** | Signal detection, ranking, and state changes |
| Exposure View | **Radon Exposure** | Risk and positioning surfaces |
| Vol View | **Radon Surface** | Volatility surface and dislocation analysis |
| Regime View | **Radon Structure** | Cross-asset structure and state reconstruction |
| Watchlists | **Radon Sets** | Saved universes and instrument groups |

### Container refactor: cards → instrument panels
Current UI cards should evolve into **modular instrument panels**.

#### Panel rules
- Use **hairline borders**, not soft shadows.
- Favor **matte panel surfaces** with internal grid discipline.
- Introduce **panel headers** that read like device labels.
- Use **panel metadata rails** for sampling rate, engine source, confidence, or time basis.
- Corners should be **tight**. Avoid consumer-rounding.
- Modules should feel **mountable**, as if they could slide into a rack.

| Component | Current Dashboard Treatment | Radon Treatment |
|---|---|---|
| Card container | Floating rounded rectangle | Fixed instrument panel |
| Header | Casual title | Upper-left panel ID + module name |
| Footer | Optional action row | Calibration / source / last sample metadata strip |
| Accent | Generic colored border | Thin signal trace or calibrated edge marker |
| Grouping | Loose | Panel families aligned on strict grid |
| Empty state | Blank | Measurement standby state |

### Instrument motifs
Use these sparingly in background layers, loading states, and drill-down transitions.

| Motif | Description | Best Use |
|---|---|---|
| **Spectral decomposition lines** | Parallel emission-like lines that imply decomposition into components | Loading, factor/signal decomposition |
| **Circular scanning arcs** | Radar / detector rings suggesting sampling and sweep passes | Discovery states, scan progress, candidate isolation |
| **Projection geometry** | Angled projection lines inspired by Radon transforms and tomographic sampling | Hero backgrounds, engine overlays, regime reconstruction views |

#### Motif guidance
- Keep opacity low; motifs are structural, not decorative.
- Motifs should never compromise data readability.
- Motion should be slow, analytical, and confidence-increasing—not flashy.

---

## 2) Typography & Hierarchy

### Recommended stack
- **Primary UI:** Inter
- **Numeric / dense tabular:** IBM Plex Mono
- **Display / brand moments:** Söhne

### Typographic philosophy
Radon typography should communicate:
- exactness
- calibration
- legibility under density
- numerical trust

### System settings

| Use | Font | Size | Weight | Tracking | Leading |
|---|---|---:|---:|---:|---:|
| Product wordmark | Söhne / fallback Inter | 22–28 px | 600 | +1% | 1.1 |
| View title | Inter | 18 px | 600 | +1% | 1.2 |
| Panel title | Inter | 14 px | 600 | +2% | 1.2 |
| Section label | Inter | 12 px | 600 | +4% | 1.2 |
| Metric value | Inter | 24–32 px | 500/600 | 0% | 1.05 |
| Dense numeric table | IBM Plex Mono | 12–13 px | 500 | +1% | 1.35 |
| Status/meta | IBM Plex Mono | 11–12 px | 400/500 | +3% | 1.35 |
| Annotation | Inter | 12 px | 400 | +1% | 1.4 |

### Density adjustments
For a high-density quant UI:
- Slightly increase tracking on labels and metadata so the interface feels measured rather than cramped.
- Keep metric leading tight.
- Keep table leading slightly open so the matrix can breathe.
- Avoid excessive weight contrast; use **structure**, not loud typography, to create hierarchy.

### Status & meta-data style
Secondary information should read like instrument telemetry.

**Example fields**
- NOTIONAL EXPOSURE
- NET CASH
- SURFACE FIT
- CONFIDENCE
- SOURCE DELAY
- LAST SAMPLE

| Attribute | Style |
|---|---|
| Font | IBM Plex Mono |
| Case | Uppercase or title case depending on module |
| Size | 11–12 px |
| Weight | 500 |
| Tracking | +3% |
| Color | Muted slate / graphite |
| Behavior | Stable; avoid blinking unless there is a fault state |

---

## 3) The Radon Spectrum — Color Logic

Traditional green/red maps too closely to retail P&L and binary gain/loss semantics. Radon should use color to communicate **clarity, dislocation, certainty, state, and severity**.

### Core brand palette

| Token | Hex | Role |
|---|---|---|
| `bg.canvas` | `#060E09` | Primary background |
| `bg.panel` | `#0B1A12` | Instrument panels |
| `bg.panelRaised` | `#112A1C` | Hover / focus panel |
| `line.grid` | `#1A3527` | Grid and borders |
| `text.primary` | `#E5F0EA` | Primary text |
| `text.secondary` | `#94AE9E` | Secondary text |
| `text.muted` | `#637A6C` | Meta / supporting text |
| `signal.core` | `#3CB868` | Core Radon discovery layer |
| `signal.strong` | `#5FD882` | High-confidence signal |
| `signal.deep` | `#2A8B4F` | Deep data / selected states |
| `warn` | `#F5A623` | Quality / caution |
| `fault` | `#E85D6C` | Feed fault / integrity problem |
| `violet.extreme` | `#8B5CF6` | Extreme dislocation / rare state |
| `magenta.dislocation` | `#D946A8` | Structural dislocation |
| `neutral` | `#B8CABC` | Neutral comparative states |

### Signal semantics
Use **clarity scale**, not profit/loss scale.

| State | Meaning | Color |
|---|---|---|
| Baseline | No notable structure isolated | neutral |
| Emerging | Weak but non-random candidate | signal.deep |
| Clear | Strong structural candidate | signal.core |
| Strong | High-confidence reconstruction | signal.strong |
| Dislocated | Market structure notably out of line | magenta.dislocation |
| Extreme | Rare regime / high-convexity event | violet.extreme |

### Volatility dislocation logic
For surface and regime views:
- **Green family** = recovered or clarified structure, signal clarity
- **Magenta/Violet family** = tension, dislocation, or instability
- **Amber** = incomplete confidence / data quality concern
- **Red/Pink fault** = operational issue, not market P&L

### Recommended flagship accent
**Primary Radon signal accent:** `#3CB868`

Reason:
- institutional, authoritative green — reads as infrastructure, not retail
- distinct from consumer fintech neons and sci-fi cyan
- clean on dark backgrounds, adapts well to light mode
- reads as “structure found / signal isolated” rather than simplistic profit/loss

---

## 4) Open Source Design System — Contributor Kit

Contributors need strict rules so the system remains coherent.

### Atomic design principles

| Principle | Rule |
|---|---|
| Instrument first | Every component should feel like part of a measurement system |
| Data over decoration | No ornamental UI unless it communicates state, structure, or calibration |
| Panel discipline | All modules live inside defined panel shells |
| Mono for machine, sans for product | Numbers and telemetry use mono; narrative UI uses sans |
| Color is semantic | Accent color must indicate state, clarity, or quality—not “make it pretty” |
| Motion is analytical | Motion should explain transitions, scan progress, or decomposition |
| Grid before style | Layout correctness is more important than embellishment |
| Density with restraint | High information density is good; clutter is not |
| Dark mode is primary | Light mode is supported, but the system is designed dark-first |
| Every module has a source | Surfaces should expose source, confidence, and recency when possible |

### Radon grid system
A strict grid is required so community-built modules still feel native.

#### Layout foundation
- Base unit: **8 px**
- Sub-unit for table spacing / micro-alignment: **4 px**
- Grid columns:
  - 12 columns on laptop
  - 16 columns on desktop
  - 24 columns on ultra-wide
- Standard panel spans: **4 / 8 / 12 / 16 / full**
- Horizontal gutter: **16 px**
- Vertical rhythm between panel rows: **16 px**
- Section gap: **32 px**

| Grid Rule | Specification |
|---|---|
| Base spacing | 8 px |
| Micro spacing | 4 px |
| Panel padding | 16 px |
| Compact panel padding | 12 px |
| Header row height | 32 px |
| Metadata rail height | 20–24 px |
| Dense table row | 28 px |
| Standard table row | 32 px |

### Component acceptance rules for contributors
A contributed component should not be accepted unless it:
1. uses approved tokens
2. snaps to the Radon grid
3. exposes state clearly
4. supports dark mode first
5. keeps numerical alignment intact
6. avoids stylistic drift like glassmorphism, heavy gradients, or soft consumer shadows

---

## 5) Visualizing the Engines

When drilling into a signal, the user should see not just output but **which engine produced the interpretation**.

### Engine representation framework

| Engine | Meaning | Visual Grammar | UI Behavior |
|---|---|---|---|
| **Spectral** | Decomposition into components / frequencies / latent bands | spectral bars, energy bands, line stacks | animate through decomposition passes |
| **Eigen** | Principal directions, correlation geometry, dominant factors | matrix field, vector axes, eigenplanes | reveal dominant components and rank |
| **Markov** | Regime transitions and state probabilities | node graph, transition arcs, state lattice | show prior → current → likely next state |
| **Laplace** | Curvature, convexity, local instability | contour surfaces, curvature maps, field lines | highlight local pressure and payoff curvature |

### Spectral drill-down
Use:
- stacked bands
- frequency-like traces
- decomposition passes
- energy concentration markers

**Behavior:** Show the raw signal entering from the left, then resolved layers with confidence scores.

### Eigen drill-down
Use:
- orthogonal vector lines
- matrix heatmaps
- principal factor ordering
- dimension reduction cues

**Behavior:** Emphasize which dimensions explain most of the structure.

### Markov drill-down
Use:
- linked state nodes
- transition percentages
- current-state emphasis
- next-state probability lanes

**Behavior:** Show the signal as part of a path, not an isolated event.

### Laplace drill-down
Use:
- curvature contours
- gradient fields
- convexity ridges
- local extrema markers

**Behavior:** Helps the user understand where payoff geometry bends, compresses, or destabilizes.

---

## 6) UI Component Specification

### Panel shell

| Property | Spec |
|---|---|
| Background | `bg.panel` |
| Border | 1 px `line.grid` |
| Corner radius | 4 px max |
| Shadow | None |
| Padding | 16 px |
| Header | 32 px fixed height |
| Footer / metadata rail | 20–24 px |
| Hover | background shift to `bg.panelRaised`, not lift |

### Signal badge

| Property | Spec |
|---|---|
| Shape | Capsule or tight rounded rect |
| Height | 20 px |
| Font | IBM Plex Mono 11 px 500 |
| Colors | state-driven; default uses `signal.core` on dark muted background |
| Usage | confidence, regime, dislocation, engine tags |

### Table arrays

| Property | Spec |
|---|---|
| Header font | Inter 11–12 px 600 |
| Cell font | IBM Plex Mono 12–13 px 500 |
| Row height | 28–32 px |
| Row hover | subtle fill only |
| Dividers | 1 px grid lines |
| Alignment | decimals right-aligned; labels left-aligned |
| Sorting | explicit directional icons, no playful motion |

### Risk/exposure cards → exposure modules

| Current | Radon Revision |
|---|---|
| Generic KPI card | Exposure module with header rail |
| Plain label + value | Label + unit + time basis + confidence |
| Large color fill | Thin spectral indicator or edge trace |

### Loading states

| State | Treatment |
|---|---|
| Data fetching | slow scan line or spectral pulse |
| Reconstructing | projection geometry sweep |
| Waiting for feed | standby pulse with muted telemetry |
| Fault | static panel with explicit reason and recovery hint |

---

## 7) Brand Voice

Radon’s voice should be:
- precise
- calm
- scientific
- unsensational
- informative under stress

### What Radon should not sound like
- hypey
- trader-bro
- breathless
- retail-alert-y
- vague

### Alert language examples

| Bad | Radon |
|---|---|
| Massive trade alert! | **Structural event detected.** |
| Huge gamma squeeze incoming | **Convexity concentration elevated.** |
| This ticker is exploding | **Volatility state shifted beyond baseline range.** |
| Something went wrong | **Data reconstruction failed. Source feed incomplete.** |

### Error messages
Use this pattern:

**[System] + [Failure] + [Cause if known] + [Recovery guidance if useful]**

Examples:
- **Flow module unavailable. Upstream feed timed out.**
- **Signal reconstruction incomplete. Required options surface missing.**
- **Exposure view delayed. Last valid sample: 09:41:12 ET.**
- **Markov transition model unavailable. Regime history window too short.**

### Technical alerts
Examples:
- **Signal clarity degraded. Liquidity insufficient for stable reconstruction.**
- **Cross-asset divergence detected. Confidence: 0.82.**
- **Eigen decomposition complete. Principal factor isolated.**
- **Surface dislocation elevated relative to local baseline.**

### Tone rules
- Prefer nouns and verbs over adjectives.
- Avoid emotional punctuation.
- Never use emojis.
- Avoid “huge,” “massive,” “crazy,” “exploding,” “insane.”
- Confidence and source are better than hype.

---

## 8) Signature Identity Summary

Radon becomes recognizable through the combination of:
1. spectral cyan signal logic
2. graphite instrument panels
3. mono telemetry styling
4. projection/scanning motifs
5. disciplined grid structure
6. scientific, non-hyped language

### Core line
**Radon — Reconstructing market structure from noisy signals.**

### Alternate lines
- **Signal from noise.**
- **Market structure, reconstructed.**
- **A scientific instrument for convex discovery.**

---

## 9) Implementation Notes

### Immediate next moves
1. Rename product architecture to Radon hierarchy.
2. Replace rounded “app cards” with panel shells.
3. Move all risk metrics into mono-telemetry styles.
4. Introduce signal clarity color semantics.
5. Add one motif family per area:
   - scan arcs in discovery
   - spectral lines in decomposition
   - projection geometry in structure/regime
6. Standardize component contributions against the Radon grid.

### Success criteria
The redesign is successful if:
- the interface feels more like a calibrated instrument than a web dashboard
- users can distinguish operational fault vs market dislocation at a glance
- modules built by contributors still feel native
- the visual system supports density without feeling messy
- the product looks credible in front of serious PMs, vol traders, and quant researchers
