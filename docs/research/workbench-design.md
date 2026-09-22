# Research workbench design

## Overview

The `/research-workbench` surface applies Radon's selected [Clear direction](../../web/DESIGN_MEMORY.md) to source inspection and operator-reviewed research. It keeps the existing white/evergreen identity and quiet dark counterpart. [Financial workbench](financial-workbench.md) owns product scope and calculation conventions; this document records the implemented presentation and interaction hierarchy.

The opening heading, “Evidence to decision,” sits above source and cited-fact counts, an explicit unsaved-evidence label, and Import evidence / Save evidence actions. Source intake precedes research outputs. Outputs keep their passages inspectable before model analysis, artifact download, or ticket handoff.

## Colors

Use the existing theme tokens in [globals.css](../../web/app/globals.css) and [clear.css](../../web/app/clear.css). Primary actions, selected views, links, input carets, and focus outlines use `--signal-core`; primary actions pair it with `--text-on-accent`. Reading text uses `--text-primary`, supporting labels use `--text-muted`, and errors use `--negative`.

Panels and controls use `--bg-panel`; quotations, notices, and the trade-review area use `--bg-subtle`. Quiet dividers use `--line-grid`. These are inherited roles, not a workbench-specific palette or theme store.

## Typography

Inherit the existing Inter UI hierarchy. Reading text is 14px with a 1.6 line height; metadata and field labels are 12px. Section headings are 22px, subheadings 16px, and empty-state headings 28px. Comparable financial values use tabular numerals, with 22px fact values and 24px valuation results. The document inspector uses the inherited sans-serif font, preserved whitespace, and wrapping text for readable source inspection.

## Layout

The workbench has a centered 1500px maximum width. Desktop places a 260–310px evidence column beside flexible research content, separated by a quiet vertical rule and 36px gap. At 1000px and below, the evidence column becomes 220–260px and the gap becomes 24px; header actions move beneath the heading.

At 640px and below, evidence precedes research in one column. The source list scrolls within a 260px height, field pairs and valuation inputs become single-column, section headers stack, and view buttons wrap. Financial tables retain column structure inside horizontal overflow containers. The document inspector appears beneath the active research view, with its source text in a scrollable region capped at 480px.

## Elevation & Depth

Flat surfaces, thin rules, and pale supporting backgrounds establish hierarchy. Facts form separated reading rows rather than a grid of floating cards. Quotations and the trade checklist receive tonal emphasis. This surface adds no decorative imagery, gradients, or shadows.

## Shapes

Controls and quotations use the existing `--radius`; the trade-review panel uses `--radius-lg`. Preserve Clear's restrained rounded forms instead of introducing a local corner scale.

## Components

- **Evidence intake:** Native disclosures expose labeled text, file, date, URL, and source-type inputs. Feed import opens on demand. Source titles open the document inspector, where an additional disclosure permits exact-passage fact annotation.
- **Eight research views:** Brief, Fundamentals, Calendar, Deals, Signals, AI infrastructure, Labs & exports, and Controls are native buttons in a named navigation region. `aria-pressed`, evergreen text, and an underline identify the selected view. They use normal button keyboard interaction, not a custom ARIA tablist.
- **Citations:** Native `details` / `summary` disclosures show source title and publication date, then the exact quotation, character offsets, source kind, document inspection action, and original-source link when supplied. Citation alignment does not establish publisher authenticity.
- **State and saving:** Save evidence downloads sources and facts as JSON. It excludes the analysis date, valuation assumptions, peer inputs, investor-note draft, and imported reliability report. Labs and Controls remain mounted after their first visit, preserving those drafts when switching views within the page. Reloading clears the workspace; this is not durable draft storage.
- **Privacy and model handoff:** Source intake explains that text remains on the page until exported. “Draft with assistant” explicitly hands research to the configured assistant; the workbench does not silently run a model or imply premium-data entitlement. Evidence and artifact downloads can contain source passages and require operator review before sharing.
- **Valuation and controls:** Native numeric fields label units and decimal-rate conventions. Results appear only with complete valid inputs; assumption limits remain in a disclosure. Governance and reliability use sortable tables. Reliability distinguishes imported offline contracts from live probes and displays measurement limits beside the metrics.
- **Feedback and unavailable states:** Errors use alerts; import/export progress and notices use status text. Feed and audit failures expose retry actions. Missing reconciliation, calendar evidence, comparable periods, AI-spend disclosure, and reliability measurements stay explicitly unavailable. Empty states name the evidence needed. Export and ticket actions are disabled until their prerequisites are met.
- **Interaction targets:** Buttons, inputs, selects, and disclosure summaries have a 44px minimum height; action and view buttons also have a 44px minimum width. Checkbox labels provide a 44px-high click target around the smaller native checkbox. Interactive elements receive an evergreen 2px focus outline with a 3px offset. Text links remain inline links.

## Do's and Don'ts

- Preserve exact passages, dates, signed values, units, and explicit missing-data language beside the output they qualify.
- Keep source inspection and assumptions close to the action that uses them.
- Keep checklist completion separate from contract selection, sizing, coverage, risk checks, and explicit order confirmation in the existing ticket.
- Do not describe evidence export as a complete workspace backup or retained tab drafts as saved storage.
- Do not present extraction candidates, model output, imported reports, or citation matches as verified financial truth.
- Do not add a competing global design system; the implemented reference is [ResearchWorkbench.module.css](../../web/components/research/ResearchWorkbench.module.css) within Clear's existing theme.
