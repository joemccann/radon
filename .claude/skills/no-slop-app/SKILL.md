---
name: no-slop-app
description: Audit and redesign a Radon screen (app.radon.run or radon.run) so it stops reading as generic AI/template output. Use when asked to de-slop, critique, or redesign a page, panel, empty state or copy; before shipping new UI; or when a screen "looks generated". Produces scored tells, before/after plates, and test-first fixes inside Radon's design system.
---

# no-slop-app (Radon)

Sources: the no-slop-app pass run on app.radon.run 2026-10-12 (72 plates, PRs #1037 to #1057), and the ban list in Leonxlnx/taste-skill (MIT), `skills/redesign-skill` and `skills/taste-skill`. Only the tells that fit a dense trading terminal were adopted. The design system wins every conflict: `web/CLAUDE.md` § Theme System, `DESIGN_MEMORY.md`, `docs/brand-identity.md`.

Clean is not the bar. A tidy panel with no point of view fails. Every screen should answer the trader's first question in its largest type.

## Process

1. **Capture before.** Real screens via chrome-cdp or Playwright against an authenticated session, dark and light. Never critique from code alone.
2. **Score.** Each tell below 0 to 3 with pixel evidence (crop, value, route).
3. **Redesign** with brand tokens only. Use only data the payload carries; never invent a figure or a finding.
4. **Critic pass.** Re-score. A screen still averaging above 1 goes back.
5. **Ship test-first.** Failing Vitest, fix, browser screenshot as evidence. Contract tests win over a plate: keep the invariant and note the skip in the PR.

## Tells (score 0 none to 3 severe)

| Tell | Looks like | Fix |
|---|---|---|
| Equal-weight tile grid | 4 to 11 identical boxed KPI cells at one type size | One hero figure, the rest as a ledger line |
| Dead or orphan cell | `$0.00`, `---`, an empty grid slot after a tile is removed | Hide it, or one muted line saying why; strip columns equal tile count (`RegimeStrip columns` / `className`) |
| Badge confetti | The same pill (`UW`, `DAILY`, `NEUTRAL`, `?`) on every tile or row | State provenance or cadence once per panel; show a reading only when it carries signal |
| Repeated status | One fact owned by title, pill, sidebar, row and footer | One owner per fact |
| Timestamp pile-up | `as of`, `updated`, `next check`, `ago` stacked on one print | One freshness line derived from the data or schedule (root `CLAUDE.md` UI Copy Rules) |
| Ornament header | Icon + title + help glyph + pill on every panel | Title only; help on hover of the term |
| Semantic color drift | Red label under a positive number, P&L green on a level | Color means direction or alarm state, nothing else |
| CAPS used as decoration | Uppercase tracked labels, captions, dropdown values | Sentence case labels; caps only where a contract test pins it |
| Machine formatting | `$180.0`, `7,800.00` strikes, `2026-12-18`, `$-9.1K`, `-5138` | Trader notation: `180P Dec 18`, `7,800`, `-$9.1K`, `-5,138` |
| Generic or hedging copy | "Signal Detail", "Overview", "at a glance", disclaimer paragraphs | Say the finding, computed from data |
| Leaked internals | `IB_AUTO_IMPORT`, `IB-SKIPPED+STOCKCHARTS` in user copy | Plain provenance once |
| Stale verdict in hero type | A cached BEARISH call shown as live | Mute it and lead with the as-of date |
| Empty state that teaches nothing | Blank canvas, two buttons doing one thing, prefilled sample | Say what is missing and the one action that fills it |
| Truncation | `DISPER...`, a clipped token field, a wrapped button label | Rename, widen, or `white-space: nowrap` on the control |

Adopted from taste-skill's redesign audit (they apply to app UI):

- **Copy:** no "Elevate your", "Seamless", "Unleash", "Next-gen", "Game-changer", "Delve", "Tapestry", "In the world of"; no "Oops"; no Lorem ipsum, John/Jane Doe, Acme. No exclamation marks in success messages. Active voice in errors ("Order rejected by IB", not "An error occurred"). Enforced by `web/tests/ui-copy-slop-contract.test.ts`.
- **Numbers:** tabular figures wherever numbers align in a column.
- **Orphans:** `text-wrap: balance` on multi-line headings, `pretty` on prose.
- **States:** visible focus ring, a skeleton shaped like the layout instead of a spinner, a designed empty state, and errors through `RequestError` / toast per `web/CLAUDE.md`.
- **Alignment:** controls in a row share a baseline; a centered empty state centers its action too.
- **Motion:** transform and opacity only, with a `prefers-reduced-motion` fallback.

## Not adopted (they conflict with Radon's system)

- Bans on Inter, monospace and hairlines. Radon sets UI in Inter, figures in IBM Plex Mono, and tables need rules.
- Required Geist/Satoshi, Phosphor icons, Framer Motion springs, grain, glass, tinted shadows and picsum imagery.
- "Double the whitespace" and "no dashboards with a sidebar". This is a dense terminal.
- Any hard-coded hex or radius. Brand tokens only; 4px max radius on panels.

## Output

- Before/after plates (before crop | proposed) rendered with headless Chrome at 2x and read back before reporting.
- One PR per page group, body per `.github/pull_request_template.md`, listing each plate as done, partial or skipped with the reason.
- Account figures in captures are real. Keep plates private; never publish them to radon.run (`site/CLAUDE.md` PII rule).
