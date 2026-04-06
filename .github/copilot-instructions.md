# Radon — Copilot Instructions

## Design Context

### Users

Radon serves **active options traders** who manage their own capital. They are technically sophisticated operators — not retail users seeking simplification, and not institutional desk traders buried in Bloomberg. They want to see the data, understand the methodology, and make decisions with full context. The job: surface convex opportunities from dark pool flow, vol surfaces, and cross-asset positioning, then size and execute with discipline.

### Brand Personality

**Precise. Calm. Scientific.**

The interface should evoke **calm authority** — the quiet confidence of a well-calibrated instrument that simply works. No anxiety, no hype, no urgency theater. The operator trusts the system because every metric, threshold, and signal state is inspectable.

### Aesthetic Direction

**Visual tone:** Instrument-grade terminal. Panels are modules in a measurement system, not cards in a dashboard. Hairline borders, matte surfaces, no shadows, no glassmorphism.

**Anti-references:**
- SaaS dashboards: generic card grids, rounded corners everywhere, pastel gradients, corporate feel
- Retail trading apps: gamified, confetti, hiding information behind simplification
- Crypto/fintwit: neon colors, aggressive animations, hype-driven copy

**Theme:** Dark mode primary. Light mode supported as adaptation, not equal citizen.

### Design Principles

1. **Instrument, not dashboard.** Every panel is a calibrated module. Visual weight comes from data hierarchy, not decoration.
2. **Signal clarity over P&L.** Colors communicate data quality, regime state, and structural conditions — not profit/loss.
3. **Mono for machines, sans for humans.** Numbers/telemetry use IBM Plex Mono. Product voice uses Inter. Sohne for display headlines only.
4. **Calm authority, not anxious urgency.** Slow, analytical motion. No bounce, no shake, no pulse except for live-feed indicators.
5. **Operator control is non-negotiable.** Every metric shows its source. Every threshold is documented. No black boxes.

### Color Tokens

| Token | Dark | Light | Meaning |
|-------|------|-------|---------|
| accent | #05AD98 | #048A7A | Core signal, primary action |
| signal-strong | #0FCFB5 | #059C89 | High-confidence signal |
| warn | #F5A623 | #F5A623 | Incomplete confidence |
| negative | #E85D6C | #E85D6C | Operational fault |
| extreme | #8B5CF6 | #8B5CF6 | Rare regime event |

### Spacing Rules

- Border radius: 4px panels, 999px pills. Never rounded-lg/xl.
- Panel padding: 16-20px. Component gap: 12-16px.
- All colors via tokens — no raw hex in components.
- WCAG AA contrast ratios required.
