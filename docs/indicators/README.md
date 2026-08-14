# Indicators

Owner specs for regime tabs and the cheap-wing scanner. Add a row here when a spec ships. Do not copy signal math into the root README.

| Slug | Route | Service | Spec |
|---|---|---|---|
| vol-cone | `/scanner?mode=vol-cone` | `vol-cone` | [vol-cone.md](vol-cone.md) |
| skew | `/regime/skew` | `skew` | [skew.md](skew.md) |
| skew2d | `/regime/skew2d` | `skew2d` | [skew2d.md](skew2d.md) |
| straddle | `/regime/straddle` | `straddle` | [straddle.md](straddle.md) |
| cor | `/regime/cor` | `cor` | [cor.md](cor.md) |
| curve | `/regime/curve` | `yield-curve` | [curve.md](curve.md) |

`/regime/vol-cone` redirects to the scanner. Pattern for a new indicator: `.claude/skills/new-indicator/SKILL.md`.

A derived indicator must tell an explained parent lag apart from a broken
parent, or a spent UW daily cap fails its unit and pages a P1 every run. See
[skew2d.md](skew2d.md) "Parent lag — stale vs embargoed".
