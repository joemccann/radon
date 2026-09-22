# Radon selected design: A / Clear

Status: selected by the user on 2026-09-05: “A is the winner.”

The selected visual reference is [Clear](.claude-design/a.html), with [desktop](.claude-design/screenshots/a-desktop.png) and [mobile](.claude-design/screenshots/a-mobile.png) captures. The original brief waived the existing project's visual constraints for this exploration. Do not blend the other four concepts into Clear without further direction.

## Accepted visual direction

- An approachable, portfolio-first workstation for professional traders and risk managers.
- Light paper canvas, restrained evergreen actions, dark readable ink, quiet rules, and pale supporting surfaces.
- Local Inter for UI and prominent account numbers. Tabular numerals for comparable amounts; IBM Plex Mono reserved for appropriate secondary numeric detail. No new display font.
- A wide, open account chart connected directly to value and period controls. Avoid wrapping every metric in a separate card.
- Compact buying-power, margin and delta strip; readable positions below; a calm desktop rail for concentration, downside and a research handoff.
- Mobile uses one column, persistent bottom navigation, and a concentration exception visible before the position list. Reflow the hierarchy rather than compressing the desktop table.
- Rounded supporting surfaces follow Clear's reference: approximately 6–10px for controls/rail, circular avatars and small security marks. The old universal 4px limit is not the selected design.

## Reference palette

| Role | Selected value |
|---|---|
| Canvas | `#ffffff` |
| Supporting surface | `#f6f8f8` |
| Primary ink | `#172624` |
| Secondary ink | `#62716d` |
| Rule | `#e4eae7` |
| Accent / positive | `#087f53` |
| Negative | `#b5453e` |
| Selected control background | `#e9f4ed` |
| Attention | `#96651c` |
| Attention background | `#faf2e1` |

These are reference values from `a.css`, not a new competing production theme store. Integrate semantic roles into the existing theme architecture. Light is the approved appearance; preserve explicit user theme preferences and the current hydration contract while implementing theme parity.

## Interaction and legibility

- Clear primary actions and progressive disclosure: account → position/research → structure → risk review.
- Keep concentration conditions named and inspectable. Do not replace them with an opaque reassuring score.
- Text and signs supplement semantic color. Financial secondary labels are at least 12px; primary reading text is at least 14px. Major account values may use larger type with optically subordinate, readable decimals.
- Touch targets at least 44 × 44px, including coarse-pointer tablets. Visible keyboard focus, Escape dismissal, focus wrapping and return to the opener are required.
- Period changes update plotted data, fill, labels and axes together. Sorting/filtering applies to the full underlying list in production.
- Motion should explain state changes and respect reduced motion. Do not animate every quote update or add perpetual drawing loops.

## What selection does not turn into live functionality

- Every value and plotted point in the mockup is synthetic. The example semiconductor watch limit and stress loss are not measured account facts or existing policy defaults.
- The shared prototype's paper-review dialogs are interaction references, not production order components. Production must use `useOrderRisk` and `OrderRiskGate`.
- The reference's four primary destinations are a hierarchy proposal, not permission to remove orders, news, scanners, chains, performance, journal, alerts, workflow, administration, or settings.
- Keep the root-owned price socket, authentication, data freshness/provenance, signed valuation and order safety contracts.
- Static prototype timings do not establish production performance. Measure the implemented app under representative data and quote traffic.

## Evidence and implementation

The prior comparison passed 68 flow checks, 18 independent interaction checks, 36 dialog cases and 20 isolated load checks. See [reference notes](.claude-design/sources-ab.md), [interaction review](.claude-design/review-flows.md), [mobile review](.claude-design/review-mobile-type.md), and [performance limits](.claude-design/performance-review.md). These describe the prior prototype run, not new production verification.

The production work is specified in [DESIGN_PLAN.md](DESIGN_PLAN.md). The subsequent user-authorized implementation applies Clear across the real application. [Verification](DESIGN_VERIFICATION.md) and [performance measurements](DESIGN_PERFORMANCE.md) record the production-code evidence separately from the prototype. No deployment is part of this handoff.

## Instrument workspace: B / Chain first (2026-09-16)

User selected B from two density prototypes and authorized implementation. On desktop at 1024px and wider, every instrument view owns the viewport below a 48px global navigation bar. Position, News, Ratings, Seasonality, Company, 13F, Filings and Commands share a 56px toolbar and independently scrolling full-width content. Book & trade retains its quote strip, depth/tape and order ticket beside the same persistent sidebar. A 208px instrument sidebar (192px at narrower desktop widths) holds the underlying quote, instrument views and held position. Feed/freshness, futures and sync remain in a header disclosure. A 56px toolbar, 52px column header and 26px underlying anchor leave room for 40px quote rows. The existing two independently scrolling strike panes and 384px staged ticket remain intact. Widths below 1024px retain the existing cockpit/mobile layout.

Rendered fixture measurements: 14 complete strike rows at 1440×800; 12 at 1280×720; first quotes at y=156px; no document horizontal overflow. Financial quote labels and stale behavior use the shared telemetry model. CI coverage lives in `e2e/chain-first-layout.spec.ts`, `e2e/instrument-workspace-layout.spec.ts` and `tests/chain-first-sidebar.test.tsx`.
