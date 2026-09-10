# Design Memory

## Brand Tone

- **Adjectives:** dense, mono-dominant, editorial-quant, utilitarian (Instrument Rack direction)
- **Avoid:** heavy boxed lists where slim rows work; multiple header blocks saying the same thing; equal visual weight on primary and secondary controls

## Chat / command surfaces (2026-08-29, Radon Chat modal lab)

- Winner pattern: **composer-led** — the input is the single hero element (top of panel, focus ring on open); suggestions are slim `/cmd + muted description` rows with NO section label above them; all secondary chrome (@ sources, / commands, model, esc hint) collapses into one 10px mono uppercase footer line.
- Enter sends; no ASK button. Keyboard affordance is a quiet `↵` inside the field.
- Empty-state panels size to content (~640px wide), not full viewport height.
- Rejected but liked directions for reuse elsewhere: command-palette list with active row (`--wash-signal` highlight), bottom dock with pill chips, terminal prompt with numbered accelerators.

## Layout & Spacing

- 8px base grid with 4px micro step (`--space-*`); radius capped at 4px (`--radius`), capsules only for badges.

## Typography

- Mono (`--font-mono`) for commands, meta rails, footers: 10px uppercase 0.06-0.12em tracking for chrome, 12px for command text. Sans (`--text-body` 13px) for descriptions and prose.

## Color

- Tokens only, never raw hex; translucency via `color-mix(... var(--token) X%, transparent)` so both themes track. Focus = `--border-focus` + `--wash-signal` glow. Hover surfaces = `--bg-panel-raised`.

## Interaction Patterns

- Modals: scrim `color-mix(var(--bg-canvas) ~90%, transparent)`, esc dismiss, ⌘J toggle.
- Motion: `--transition-controls` (paint-only, 150ms); blink/entrance effects gated behind `prefers-reduced-motion: no-preference`.

## Repo Conventions

- Styling in `web/app/globals.css` with CSS variables; components in `web/components/`; suggestion prompts sourced from `quickPromptsBySection` in `web/lib/data.ts`.

---

*Updated by the design-lab skill*
