# Skill-stack UI baseline (pre-change)

Captured **2026-08-06** from local Next.js at `http://localhost:3000` via Chrome CDP (`~/.claude/skills/chrome-cdp/scripts/cdp.mjs`).

Localhost Clerk bypass (non-production). Live IB relay was offline during capture (`RELAY OFFLINE`, `LIVE DATA DEGRADED`, toast: IB Gateway uplink lost). Cached / delayed market data still rendered; structure and chrome are representative of current UI.

## Viewports

| Label | CSS viewport | Capture size | Method |
|---|---|---|---|
| Desktop | 1440 × 900 | 1440 × 900 px (DPR 1 override) | `Emulation.setDeviceMetricsOverride` |
| Mobile | 390 × 844 | 780 × 1688 px (DPR 2) | `Emulation.setDeviceMetricsOverride` mobile |

## Files

All under `docs/design-shots/skill-stack/baseline/`.

### Desktop (1440×900)

| File | Route | What it shows |
|---|---|---|
| `baseline-desktop-scanner-theta.png` | `/scanner?mode=theta` | Full terminal shell: left nav, header (relay/futures strip), scanner mode tabs, **Theta Harvester** instrument table (TXN, MCHP, VRTX, …), device rail, footer health bar. |
| `baseline-desktop-scanner-garch.png` | `/scanner?mode=garch` | Same chrome; **GARCH Convergence** table (pairs, lag, divergence, gates). Sparse result set (3 pairs). |
| `baseline-desktop-dashboard.png` | `/dashboard` | Dashboard workspace: Account / Portfolio summary, Working & Filled orders, Trading Candidates counts, Live Market Feed column. |
| `baseline-desktop-portfolio.png` | `/portfolio` | Portfolio: Account metrics (NLv, day P&L, unrealized), collapsible risk/margin sections, Today’s P&L, Defined Risk Positions table. |

### Mobile (390×844)

| File | Route | What it shows |
|---|---|---|
| `baseline-mobile-scanner-theta.png` | `/scanner?mode=theta` | Mobile app bar (SCANNER), mode tabs, Theta Harvester cards (TXN/MCHP…), bottom tab bar, degraded-live banner. |
| `baseline-mobile-scanner-garch.png` | `/scanner?mode=garch` | Mobile GARCH Convergence: scan controls, ALL/ACTIONABLE/FAILED filters, pair rows. |
| `baseline-mobile-dashboard.png` | `/dashboard` | Mobile dashboard: NLV / today P&L grid, Live Market Analysis block, bottom nav. |
| `baseline-mobile-portfolio.png` | `/portfolio` | Mobile portfolio Account stack: NLV, day P&L, unrealized, dividends; bottom tab bar. |

## Capture notes

- Tool: Chrome Debug.app CDP on port 9222.
- Target tab opened to Radon Terminal; metrics override applied per viewport class before navigate + shot.
- Viewport-only captures (not full-page scroll). Content below the fold is not in these PNGs.
- Transient overlays present on most frames: red issues chip, IB Gateway toast. Do not treat those as layout regressions unless they change after intentional UI work.

## Paths (absolute)

```
/Users/joemccann/dev/apps/finance/radon/docs/design-shots/skill-stack/baseline/baseline-desktop-scanner-theta.png
/Users/joemccann/dev/apps/finance/radon/docs/design-shots/skill-stack/baseline/baseline-desktop-scanner-garch.png
/Users/joemccann/dev/apps/finance/radon/docs/design-shots/skill-stack/baseline/baseline-desktop-dashboard.png
/Users/joemccann/dev/apps/finance/radon/docs/design-shots/skill-stack/baseline/baseline-desktop-portfolio.png
/Users/joemccann/dev/apps/finance/radon/docs/design-shots/skill-stack/baseline/baseline-mobile-scanner-theta.png
/Users/joemccann/dev/apps/finance/radon/docs/design-shots/skill-stack/baseline/baseline-mobile-scanner-garch.png
/Users/joemccann/dev/apps/finance/radon/docs/design-shots/skill-stack/baseline/baseline-mobile-dashboard.png
/Users/joemccann/dev/apps/finance/radon/docs/design-shots/skill-stack/baseline/baseline-mobile-portfolio.png
```
