# Radon — Codex Instructions

Radon is a market-structure reconstruction and operator workstation for finding, sizing, monitoring, and executing convex options trades from IB, Unusual Whales, dark-pool/OTC flow, OI changes, vol/regime signals, news, and live portfolio state.

This root file is intentionally small. Scoped `AGENTS.md` files live beside subsystem `CLAUDE.md` files and should be loaded by cwd as needed:

| Scope | Codex File | Mirrors |
|---|---|---|
| Root | `AGENTS.md` -> `.pi/AGENTS.md` | `CLAUDE.md` |
| Web | `web/AGENTS.md` | `web/CLAUDE.md` |
| Scripts | `scripts/AGENTS.md` | `scripts/CLAUDE.md` |
| FastAPI | `scripts/api/AGENTS.md` | `scripts/api/CLAUDE.md` |
| Monitor daemon | `scripts/monitor_daemon/AGENTS.md` | `scripts/monitor_daemon/CLAUDE.md` |
| Newsfeed | `scripts/newsfeed/AGENTS.md` | `scripts/newsfeed/CLAUDE.md` |
| Watchdog | `scripts/watchdog/AGENTS.md` | `scripts/watchdog/CLAUDE.md` |

When a scoped `AGENTS.md` and this root file conflict, prefer the more specific scoped file. When `AGENTS.md` and `CLAUDE.md` conflict, prefer the newer, more specific rule and update the Codex file during the work.

## Workflow

- Be concise. No preamble.
- Think before coding. State assumptions, surface tradeoffs, and ask only when ambiguity blocks safe progress.
- Keep changes surgical. Touch only what the task requires; do not refactor adjacent code opportunistically.
- Simplicity first. Do not add speculative features or abstractions without a concrete need.
- For non-trivial work, update `tasks/todo.md` first with a dependency graph using IDs like `T1`, `T2` and `depends_on: []`.
- Track checklist progress as work completes and add a review section before final response.
- After any user correction, update `tasks/lessons.md` with a rule that prevents repeating the mistake.
- Never revert user changes. Check `git status --short --branch` before edits and ignore unrelated dirty files.
- Use `rg` / `rg --files` first for search.

## Verification

- Bug fixes use red/green TDD: failing test, minimal fix, green test.
- UI bugs require Playwright E2E coverage and visual browser verification. Use `chrome-cdp` when available; otherwise use Playwright.
- Target 95% coverage on touched surfaces.
- Always run the relevant focused tests. Run full project suites before commits when changes are code-bearing; if baselines are already red, report focused green results and unrelated baseline failures separately.
- For scoped Python changes, prefer `python3.13 scripts/run_pytest_affected.py --files ... -- -q` first, then broaden when needed.
- For JS/UI changes, run focused Vitest, relevant Playwright E2E, and browser screenshot checks.

## Global Trading Rules

- Output discipline for evaluations: `signal -> structure -> Kelly math -> decision`; state probabilities and uncertainty.
- Four gates are sequential:
  1. Convexity: gain >= 2x loss; defined-risk preferred.
  2. Edge: specific data-backed dark-pool / OTC signal that has not moved price.
  3. Risk: fractional Kelly with hard cap 2.5% bankroll per position.
  4. Naked-short gate is disabled as of 2026-04-30; logic remains in `_*Impl`, re-enable only via `docs/naked-short-reenable.md`.
- If an active gate fails, stop and name the gate. Never rationalize a bad trade.
- Evaluations must call `python3.13 scripts/evaluate.py [TICKER]`; do not manually call milestone scripts during evaluation.
- Every evaluation milestone must fetch fresh data, include today's data, and print a Data Freshness line.
- M3B OI changes are required; large OI can reveal institutional trades missed by UW flow alerts.
- Portfolio source of truth is Interactive Brokers via `python3.13 scripts/ib_sync.py`. `docs/status.md` and `data/portfolio.json` are stale-prone caches/logs.

## Brand Rules

Any UI or asset work must comply with Radon brand identity:

- Reference: `docs/brand-identity.md`, `brand/radon-brand-system.md`, `brand/radon-design-tokens.json`, `brand/radon-tailwind-theme.ts`, `brand/radon-component-kit.html`.
- Accent `signal.core: #05AD98`; canvas `#0a0f14`; panel `#0f1519`; raised `#151c22`; grid `#1e293b`.
- Typography: Inter UI, IBM Plex Mono numeric tables, Sohne display only.
- Panels are instrument modules with hairline borders, matte surfaces, device-label headers, and max 4px radius. Badges may be 999px capsules.
- No glassmorphism, gradients, soft shadows, decorative elements, hype, emojis, or em dashes in user-facing copy.
- Empty states describe measurement conditions, not generic placeholders.

## Data Source Priority

1. Interactive Brokers.
2. Unusual Whales.
3. Yahoo Finance only as market-data fallback after IB and UW.
4. Web scrape / browser last resort for market data; use Exa for research/docs and interactive browser only for JS-rendered pages.

Specialized feeds like Cboe may be used where a script explicitly documents that provider as the correct source for a metric.

## Commands

- `commands`: immediately read `.pi/commands.json` and display a formatted table. No other actions.
- `evaluate [TICKER]`: run `python3.13 scripts/evaluate.py [TICKER]`.
- `scan`: run scanner then CRI scan and generate daily scan report.
- `discover`: run `python3.13 scripts/discover.py`.
- `portfolio`: run `python3.13 scripts/portfolio_report.py`.
- `sync`: run `python3.13 scripts/ib_sync.py --sync`.
- `risk-reversal [TICKER]`: run `python3.13 scripts/risk_reversal.py [TICKER]`.
- `garch-convergence [TICKERS]`: run `python3.13 scripts/garch_convergence.py ...`.
- `vcg`: call the `vcg_scan` Pi tool; do not read strategy docs.

## Repository State

- Turso libSQL embedded replicas were retired on 2026-05-20. Direct-to-cloud is the code default (replica opt-in only via `RADON_DB_USE_REPLICA=1`; `RADON_DB_NO_REPLICA=1` remains the kill switch); `data/replica.db` must not exist.
- Trades canonical store is the Turso `journal` table. `/journal` and `/orders` derive from it, with JSON fallback only for legacy rows.
- Push to `main` auto-deploys through `.github/workflows/deploy.yml`; verify with `gh run list --workflow=deploy.yml --limit 1` when deployment matters.
- Local HTML reports that embed images must inline images as base64 `data:` URIs; Chrome blocks `file://` image loading.
