# Mobile Feature-Parity Audit

Goal: every web route reaches desktop parity on mobile (393×852), including every in-page interaction. Seamless mobile UX.

Severity: **P0** = broken/unusable on mobile · **P1** = present but degraded/awkward · **P2** = polish.

## Route inventory (27 page routes)
- [ ] `/` (redirect)
- [ ] `/dashboard`
- [ ] `/[ticker]` (cockpit — chain parity DONE be2174d8)
- [ ] `/portfolio`
- [ ] `/orders`
- [ ] `/journal`
- [ ] `/performance`
- [ ] `/scanner`
- [ ] `/discover`
- [ ] `/flow-analysis` + `/flow-analysis/[ticker]`
- [ ] `/cta`
- [ ] `/regime` + `/regime/{cri,gex,grg,vcg,llm,backtest}`
- [ ] `/internals`
- [ ] `/alerts`
- [ ] `/profile`
- [ ] `/admin`
- [ ] `/kit`
- [ ] `/workflow`
- [ ] `/sign-in`, `/sign-up`

## Findings
(populated by audit)

## Consolidated gap matrix (from 5 parallel audits, 2026-06-27)

### P0 — broken / unusable on mobile
| # | Route | Interaction | Gap | Solution |
|---|---|---|---|---|
| 1 | /regime/cri | Correlation Risk Premium spread zoom | `RegimeRelationshipView` hardcoded ~760px width overflows 393px | Responsive width + touch-sized brush handles |
| 2 | /regime/vcg | VCG history table (9 col) | overflows / unreadable | scroll-wrap or mobile card variant |
| 3 | /regime/gex | GEX level table (8 col) | overflows / unreadable | scroll-wrap or mobile card variant |
| 4 | /[ticker] p-deck | Position legs table (8 col, 9px, clipped BOOK/TRADE btns) | unreadable + buttons clipped | mobile legs card layout |

### P1 — degraded / awkward / unreachable
| # | Route | Interaction | Gap | Solution |
|---|---|---|---|---|
| 5 | /orders | Cash Flows table | no mobile variant, 4-col cramped | MobileCashFlowsList card variant |
| 6 | /regime/{vcg,gex,grg,llm,backtest} | multi-col stat grids | don't collapse ≤640 (no mobile detection) | media-query grid collapse |
| 7 | /alerts | whole route | NOT in mobile nav (unreachable) | add to More drawer |
| 8 | /profile | whole route | NOT in mobile nav (avatar tap doesn't nav) | add to More drawer + wire avatar |
| 9 | /[ticker] s-deck | SeasonalityTab heatmap | width unverified, may clip | verify + responsive |
| 10 | /[ticker] r-deck | RatingsTab grid | reflow unverified | verify + responsive |
| 11 | /workflow | React Flow canvas | touch drag unverified; unreachable | add to nav; verify touch |

### P2 — polish (discoverability / hover-only)
- CTA row hover, scanner mode tab, performance metric-card hover → add `:active`/coarse-pointer feedback
- CTA flag tooltips rely on `title` (weak on touch) → tap/expand
- Flow sparklines tiny on mobile + hover tooltip lost
- Mobile sort = subset of desktop keys (orders/journal/cta/discover)
- Book montage 9px text legibility
- ModifyOrderModal combo 2-col at 393px (VERIFY — media query at 640 may already cover)

## Verification log
(screenshots → confirmed/dismissed)

## VERIFIED OUTCOMES (2026-06-27) — per route → interaction → solution

Key lesson: the app is already heavily mobile-optimized (mobile variant components + `@media (max-width:640px)` collapses + responsive viewBox SVGs). Most audit P0/P1s were FALSE POSITIVES once checked against actual CSS. Fixed only confirmed-real gaps; did not churn working code.

### Fixed
| Route | Interaction | Gap (confirmed) | Solution shipped |
|---|---|---|---|
| /alerts | reachability | not in mobile nav | added to MobileMoreDrawer (Bell icon) |
| /workflow | reachability | not in mobile nav | added to MobileMoreDrawer (Workflow icon) |
| /[ticker] p-deck | Position legs table | 8 cols squish, BOOK/TRADE btn text clipped | `.pos-legs-table-wrap` horizontal scroll on mobile (all columns readable), scoped to PositionTab so the Seasonality detail table is untouched |
| /performance | metric card tap | hover-only cue, no touch press feedback | `.metric-card:active` press state |
| /scanner | mode-tab switch | hover-only, no press feedback | `.scanner-mode-tab:active` press state |

### Verified already-handled (false positives — no change, avoided regressions)
| Route | Claimed gap | Reality |
|---|---|---|
| /profile | unreachable | reachable via app-bar avatar (→ /profile), already wired |
| /regime/cri | RegimeRelationshipView 760px overflow | `.regime-relationship-chart { width:100% }` viewBox SVG scales; `.regime-relationship-grid` collapses to 1fr ≤640 |
| /regime/vcg, /regime/gex | dense tables overflow | both wrapped in `overflow-x:auto` (`.table-wrap` / `.gex-history-table-wrap`) — scroll, not broken |
| /regime/{vcg,gex,grg} | stat grids don't collapse | use shared `.metrics-grid` + `.grg-metric-grid` which collapse at ≤640 |
| /orders cash-flows | no mobile variant | uses `.table-wrap` (overflow-x:auto) — scrollable, acceptable |
| /[ticker] s-deck | seasonality heatmap clip | `.seasonality-grid` collapses to 1fr ≤640 |
| /[ticker] r-deck | ratings grid reflow | `.ratings-summary-grid` collapses to 1fr ≤640 |
| /dashboard | 50/50 grid, lightbox kbd nav | stacks to column; lightbox has touch-swipe handlers |
| /portfolio, /journal, /orders open/executed | tables | full mobile card variants (MobilePositionList/MobileJournalList/MobileOrderList/MobileExecutedList) |
| /scanner, /discover, /flow-analysis, /cta | tables | mobile card variants (SignalCard/FlowMobileCards/MobileCtaSection) |
| /[ticker] order/book/glyph decks | order entry, click-to-fill, deck nav | mobile-adapted (MobileOrderTicket, MobileQuoteRow, horizontal glyph rail, o-deck) |

### Deferred P2 (genuine, low-impact polish — not blocking parity)
- RRV brush-handle hit area (~4px scaled) small for touch; whole-window drag still works
- Flow sparklines small on mobile + desktop hover-tooltip not reproduced on touch
- Mobile sort chips = subset of desktop sort keys (orders/journal/cta/discover)
- L2 book montage 9px text legibility at ~186px columns
- /workflow React Flow canvas: touch drag-to-canvas unverified (niche; desktop-first tool)
