# Radon — Gemini Rules

## ⛔ Mandatory Rules

1. **Be concise.** No preamble, no filler.
2. **E2E browser verification for ALL UI work.** Use Playwright (`web/playwright.config.ts`). No UI change done until visually confirmed.
3. **Red/green TDD for ALL code.** Failing test → fix → green → refactor. Unit: Vitest, E2E: Playwright.
4. **95% test coverage target.** Every change includes corresponding tests.
5. **API keys** in `.env` files. Root `.env` (python-dotenv): `MENTHORQ_USER`, `MENTHORQ_PASS`, `CLERK_JWKS_URL`, `CLERK_ISSUER`, `ALLOWED_USER_IDS`. `web/.env` (Next.js): `ANTHROPIC_API_KEY`, `UW_TOKEN`, `EXA_API_KEY`, `CEREBRAS_API_KEY`, Clerk keys.
6. **Options structure reference:** `docs/options-structures.json` + `docs/options-structures.md` — 58 structures, guard decisions, P&L labels.

## Identity

**Radon** — market structure reconstruction system. Surfaces convex opportunities from dark pool/OTC flow, vol surfaces, cross-asset positioning. Detects institutional positioning, constructs convex options structures, sizes with fractional Kelly. **Flow signal or nothing.**

## ⛔ Four Gates — Mandatory, Sequential, No Exceptions

```
GATE 1 — CONVEXITY      : Potential gain ≥ 2× potential loss. Defined-risk only.
GATE 2 — EDGE           : Specific, data-backed dark pool/OTC signal that hasn't moved price yet.
GATE 3 — RISK MGMT      : Fractional Kelly sizing. Hard cap: 2.5% of bankroll per position.
GATE 4 — NO NAKED SHORTS: Never naked short stock, calls, futures, or bonds. Every short call fully covered. Violation = immediate cancel.
```

**Any gate fails → stop. No rationalization.**

## Architecture

- **Stack:** Next.js 16 (port 3000) + FastAPI (port 8321) + IB WS relay (port 8765)
- **Data flow:** Next.js → `radonFetch()` → FastAPI → Python scripts / IB Gateway
- **Auth:** Clerk JWT (FastAPI + Next.js middleware). Localhost bypass for dev.
- **Clients:** `scripts/clients/` — `IBClient`, `UWClient`, `MenthorQClient`
- **Data priority:** IB → UW → Yahoo (last resort). Never skip ahead.

## Combo / BAG Order Rules

1. **Never derive BAG `Order.action` from debit vs credit.** Keep envelope on `BUY`, preserve per-leg actions.
2. **ComboLeg.action = spread structure, NOT trade direction.** Always LONG→BUY, SHORT→SELL. Never flip.
3. **Combo bid/ask:** Always cross-fields. BUY combo: pay ASK on BUY legs, receive BID on SELL legs.
4. **Credit/debit sign convention:** Preserve sign through entire pipeline. Never `Math.abs()` without approval.

## Naked Short Protection

Guard: `web/lib/nakedShortGuard.ts`. Audit: `scripts/naked_short_audit.py`. Enforcement: UI (block) → API (403) → post-sync audit (cancel).

## Cancel/Modify

Must use subprocess with **original clientId** (range 20-49). Pool (clientId 0-5) cannot cancel/modify subprocess orders. Clear VOL fields before modify. Confirm against refreshed open orders, not stale Trade object.

## Key Files

| File | Purpose |
|------|---------|
| `data/portfolio.json` | Open positions (cache — IB is source of truth) |
| `data/trade_log.json` | Append-only trade journal |
| `scripts/api/server.py` | FastAPI — 26 endpoints |
| `scripts/ib_sync.py` | Portfolio sync with structure detection |
| `scripts/ib_place_order.py` | Order placement (client ID 26) |
| `web/lib/radonApi.ts` | `radonFetch()` — all Next.js→FastAPI calls |
| `web/lib/nakedShortGuard.ts` | Naked short protection |

## ⛔ Brand Identity — Mandatory for UI Work

Full spec: `docs/brand-identity.md`. Tokens: `brand/radon-design-tokens.json`. Tailwind: `brand/radon-tailwind-theme.ts`.

- **Typography:** Inter (UI) + IBM Plex Mono (numeric) + Söhne (display only)
- **Accent:** `#05AD98` (signal.core teal)
- **Surfaces (dark):** canvas `#0a0f14` | panel `#0f1519` | raised `#151c22`
- **4px max border-radius** on panels. All colors via tokens — no raw hex.
- Voice: precise, calm, scientific — no hype/emojis.
- Use `.gemini` folder exclusively for Gemini-related storage.

*These instructions take absolute precedence over standard workflows.*
