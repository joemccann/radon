# Kelly sizing and order admission

This owner serves trading operators choosing risk controls and maintainers
integrating evaluation or order placement. Source and tests define the exact
parameters; deployed preferences require operator verification.

## Implemented controls

[Kelly sizing](../../scripts/kelly.py) uses half Kelly by default, permits a
stricter quarter-Kelly setting, rejects full Kelly, and applies the same
position cap to scalar sizing with bankroll and batch sizing. Estimated
probabilities, probability haircuts, ticket risk, portfolio capacity and
drawdown checks are implemented there. Kelly allocates a supplied edge; it
does not establish a trade signal.

[Evaluation M6](../../scripts/evaluate.py) calls `kelly_ticket` after the
upstream gates and supplied structure pass. Missing structure leaves M5/M6
pending; a failed ticket cannot produce a TRADE decision. Passing evaluation
is not proof that every order placer enforces the same controls.

Order admission has two independent controls:

- [The Kelly order guard](../../scripts/kelly_guard.py) is enabled by
  `RADON_KELLY_ENFORCE_ORDERS`. When enabled, its default mode is warning;
  `RADON_KELLY_ENFORCE_MODE=block` makes a violation refuse the order in
  `ib_place_order.py`. Its cached bankroll and closing flags are not the
  fresh-snapshot verification used by the all-placer gate.
- [The all-placer bankroll gate](../../scripts/bankroll_guard.py), selected
  by `RADON_BANKROLL_CAP_ENFORCE_ALL_PATHS`, defaults off in
  [app preferences](../../scripts/app_preferences.py). When enabled it checks
  opening orders in `ib_place_order.py`, `ib_execute.py` and
  `exit_order_service.py` against fresh IB net liquidation from the latest
  portfolio snapshot. Missing, stale or invalid bankroll and unpriceable or
  over-cap opening risk are refused. A verified close-out is exempt: the
  snapshot must prove the combined contract deltas reduce held positions
  without increasing or flipping them. This exception is checked before
  snapshot freshness; a client closing flag alone does not establish it.

The all-placer gate is independent of Kelly warning/block mode. Turning it
off does not disable the separate Kelly guard, evaluation checks, trading
halt or fat-finger limits. Conversely, enabling the Kelly guard does not arm
the all-placer gate. Source defaults do not establish deployed settings.

## Operator enable and recovery

Gate changes are operator-only risk-policy decisions. Before enabling either
control, review its source-backed refusal cases with isolated order fixtures,
verify portfolio-sync freshness and the effective audited preference, and
obtain the existing risk-policy approval. Do not place a live probe to test
admission. For a refusal, identify which guard returned it; restore valid
portfolio data or correct the ticket rather than disabling a safety control.
An unavailable snapshot cannot prove a close-out. Escalate unresolved
position identity or policy questions to the trading operator. Reversal of an
authorized preference change requires restoring its recorded prior value;
this documentation does not authorize a policy change or claim deployment.

## Historical design

The remaining sections preserve the original Fortune's Formula rationale and
implementation acceptance design from [PR #552](https://github.com/joemccann/radon/pull/552).
They are historical, not a pending implementation plan or an operator cutover
procedure. The implemented controls above and their linked sources supersede
any proposed defaults, signatures, rollout steps or unchecked tasks below.

## B. Formulas, parameter names, defaults

All constants live in `scripts/kelly.py`. One canonical module; no second
calculator. TypeBox mirrors the bounds, never re-derives the math.

### B.1 Core formula

```
q       = 1 - p_effective
f_full  = (b * p_effective - q) / b        # identical to p_effective - q/b
f_used  = f_full * fraction                # fraction <= KELLY_MAX_FRACTION
edge    = f_full > 0
```

`b` is the payoff ratio of the defined-risk structure: `max_gain / max_loss`
per contract, both positive. `b <= 0` short-circuits to zero size (existing
behavior).

### B.2 Growth rate (geometric framing)

```
g(f) = p_effective * ln(1 + f * b) + q * ln(1 - f)       for 0 <= f < 1
growth_rate_full = g(f_full)   when edge else 0.0
growth_rate_used = g(f_used)   when edge else 0.0
```

`f_full < 1` whenever `p < 1`, so `ln(1 - f)` is defined. When `p == 1`,
`f_full == 1` and `g` is undefined; report `growth_rate_full = null` and keep
sizing on the cap (existing `test_prob_win_one_exact` behavior). Half Kelly
yields 75% of full-Kelly growth with half the variance; quarter Kelly yields
~44%. These numbers go in the output so the tradeoff is visible on every call,
not argued from memory.

### B.3 Parameters and defaults

| Name (Python) | Env / config | Default | Hard band | Notes |
|---|---|---|---|---|
| `fraction` | `RADON_KELLY_FRACTION` | **`0.5` (half Kelly, Joe-signed 2026-09-19; code default today is `0.25`, changes in implement PR 1)** | `[0.05, 0.5]` | Env outside the band is clamped INTO it and flagged `fraction_source: "env_clamped"`. Explicit call argument outside the band raises `ValueError`. `0.25` (quarter Kelly) is the documented stricter optional setting: `RADON_KELLY_FRACTION=0.25`. |
| `KELLY_MAX_FRACTION` | constant | `0.5` | n/a | Full Kelly ban. Not tunable. |
| `KELLY_MAX_PCT` | constant | `0.025` | n/a | The 2.5% per-position cap. Not tunable upward, not env-readable. Replaces the two `0.025` literals. |
| `p_haircut` | `RADON_KELLY_P_HAIRCUT` | `0.0` in implement PR | `[0.0, 0.25]` | Recommended `0.05` after Joe approves. `p_effective = max(0.0, p - p_haircut)`. |
| `p_source` | call arg | `"estimated"` | `{"estimated", "measured"}` | `"estimated"` forces `fraction <= RADON_KELLY_FRACTION`. `"measured"` still obeys `KELLY_MAX_FRACTION`. Nothing in Radon produces a measured `p` today; the value exists so the rule is explicit, not so it is used. |
| `KELLY_RESTRUCTURE_PCT` | constant | `0.20` | n/a | `f_full > 0.20` sets `restructure: true` (the `.pi/SYSTEM.md` rule). Recommendation strings (`STRONG` / `MARGINAL` / `WEAK` / `DO NOT BET`) do not change; tests pin them. |
| `max_deployed_pct` | `RADON_KELLY_MAX_DEPLOYED_PCT` | `0.20` | `[0.05, 0.50]` | Sum of worst-case losses across open defined-risk positions plus the proposed ticket, as a share of bankroll. |
| `drawdown_halt_pct` | `RADON_KELLY_DRAWDOWN_HALT_PCT` | `0.15` | `[0.05, 0.50]` | Peak-to-trough NAV drawdown at or beyond this returns size `0` with `reason: "DRAWDOWN_HALT"`. NAV history source: `ib_sync.py:_append_nav_snapshot`. |
| `RADON_KELLY_ENFORCE_ORDERS` | env | `0` (off) | `{0, 1}` | Phase 3 order-path guard is **active** when `1`. Mode is warn, not block. See §C.4. |
| `RADON_KELLY_ENFORCE_MODE` | env | `warn` | `{warn, block}` | Order-path only. Default `warn`: log + attach `kelly_warning`, still call IB. `block` restores hard refuse. Invalid values fall back to `warn`. |

Resolution order for env values: process env only, read at call time via a
small `kelly_config()` helper. Registering `RADON_KELLY_FRACTION` in
`app_preferences.REGISTRY` (`group="Risk"`, `risk_gated=True`, `default=0.5`,
`hard_max=0.5`) is optional; it is the only way a later `0.5 -> 0.25`
tightening (or any other change) gets an audit row, and the registry's
"a stored value can never widen a cap" rule means the DB can only ever tighten
below `0.5`. Registry rule 6 applies: prove `kelly.py` reads the inherited
overlay before adding the key.

### B.4 Ruin / path-to-zero statement

With defined-risk structures, the worst case per ticket is bounded by the
premium or spread width paid. Under this spec:

1. Per ticket: `max_loss_total <= KELLY_MAX_PCT * bankroll` (2.5%).
2. Aggregate: `sum(open max_loss) + proposed max_loss <= max_deployed_pct * bankroll` (20%).
3. Therefore the bankroll cannot go to zero from position losses alone: the
   worst simultaneous outcome of every open position is a `max_deployed_pct`
   drawdown, after which (3) `drawdown_halt_pct` stops new sizing until an
   operator resets.

What this does NOT cover, and the spec should not pretend it does: undefined
risk structures (Gate 4 is disabled; a naked short has no `max_loss`, so
`kelly_ticket()` must refuse it with `reason: "UNDEFINED_RISK"` rather than
size it), assignment and pin risk on spreads, broker margin calls, and fees.

### B.5 Output contract (`kelly()` and `kelly_ticket()`)

Existing keys keep their names and rounding. New keys are additive so the
TypeBox output stays backward compatible.

```
full_kelly_pct, fractional_kelly_pct, fraction_used, edge_exists, recommendation   # unchanged
p_input, p_haircut, p_effective, p_source
fraction_source          # "default" | "explicit" | "env" | "env_clamped"
growth_rate_full, growth_rate_used     # null when p == 1
restructure              # bool, f_full > KELLY_RESTRUCTURE_PCT
# only when bankroll is given:
dollar_size, max_per_position, use_size, capped        # capped = dollar_size > max_per_position
# only from kelly_ticket():
contracts, total_cost, max_loss_total, position_pct, reason
```

`position_pct` is `max_loss_total / bankroll * 100`, rounded to 2 places,
matching the key `format_report` and `test_evaluate.py` already expect
(`position_pct`, `contracts`, `total_cost`).

---

## C. Enforcement surfaces

### C.1 `scripts/kelly.py` API

```python
KELLY_MAX_FRACTION = 0.5
KELLY_MAX_PCT = 0.025
KELLY_RESTRUCTURE_PCT = 0.20

def kelly_config() -> dict          # reads RADON_KELLY_* from os.environ, clamps into bands

def kelly(prob_win, odds, fraction=None, *, bankroll=None,
          p_haircut=None, p_source="estimated") -> dict
    # fraction None -> kelly_config()["fraction"]; > KELLY_MAX_FRACTION -> ValueError
    # p_source == "estimated" and fraction > config fraction -> ValueError
    # bankroll given -> dollar_size, max_per_position (KELLY_MAX_PCT), use_size, capped

def kelly_size_batch(prob_wins, odds, bankroll, fraction=None, max_pct=KELLY_MAX_PCT)
    # validates fraction like kelly(); out-of-domain prob_wins -> 0 size
    # max_pct stays a raw-math parameter so the mutation-kill tests that pass 1.0 survive;
    # a contract test asserts no production caller passes max_pct

def kelly_ticket(*, prob_win, max_gain, max_loss, bankroll, fraction=None,
                 open_max_losses=(), nav_peak=None, nav_now=None, **kw) -> dict
    # odds = max_gain / max_loss; max_loss <= 0 -> reason "UNDEFINED_RISK", contracts 0
    # applies kelly() with bankroll, then contracts = floor(use_size / max_loss)
    # portfolio_capacity() and drawdown guard; any refusal -> contracts 0 + reason

def portfolio_capacity(bankroll, open_max_losses, proposed_max_loss, max_deployed_pct=None) -> dict
    # {"ok": bool, "deployed_pct", "remaining", "reason"}
```

CLI: `--fraction` upper bound becomes `KELLY_MAX_FRACTION`; `--bankroll`
branch calls `kelly(bankroll=...)` instead of recomputing with a literal.
New flags `--p-haircut`, `--p-source`. Output is `kelly()`'s dict, verbatim.

### C.2 TypeBox schema and wrappers

- `lib/tools/schemas/kelly.ts`: `fraction.maximum: 0.5`; add optional
  `p_haircut` (`[0, 0.25]`) and `p_source` (`Type.Union` of two literals) to
  `KellyInput`; add the §B.5 keys as `Type.Optional` on `KellyOutput`
  (`growth_rate_*` as `Type.Union([Type.Number(), Type.Null()])`).
- `lib/tools/wrappers/kelly.ts`: `RangeError` bound for `fraction` becomes
  `> 0.5`; pass the two new flags through.
- `lib/tools/pi-tools.ts:kelly_calc`: import `KellyInput` instead of the inline
  copy so the bound cannot drift a third time.
- `web/lib/assistant/tools.ts` and `scripts/mcp_hosted/server.py` describe the
  tool in prose only; no schema change, but their descriptions must not say
  "full Kelly".

### C.3 `scripts/evaluate.py` M6 gate

Preconditions: M4 `passed` (already enforced by early return). M5 must supply
`{"structure_type", "max_gain", "max_loss", "prob_win"}` per contract. Today M5
is a placeholder, so:

- `evaluate_ticker(..., structure: Optional[dict] = None)`. When `structure` is
  `None`, M5 and M6 stay `passed=False`, decision `PENDING`, exit `2`. This is
  the fail-closed default and is exactly today's behavior.
- When `structure` is supplied (CLI `--structure '<json>'` or the FastAPI
  `/evaluate` caller once M5 is real), M6 calls `kelly_ticket()` with
  `bankroll` and the open positions' worst-case losses from
  `data/portfolio.json` (or the DB-first equivalent; the reader already exists
  in `ib_sync.py`).
- M6 `passed=True` iff `edge_exists and contracts >= 1 and reason is None`.
  Otherwise `passed=False`, `decision="NO_TRADE"`, `failing_gate="RISK"`, and
  `data.reason` set (`"NO_EDGE"`, `"CAP_BELOW_ONE_CONTRACT"`,
  `"UNDEFINED_RISK"`, `"CAPACITY"`, `"DRAWDOWN_HALT"`). `restructure: true`
  also fails M6 with `reason="RESTRUCTURE"`; the `.pi/SYSTEM.md` rule becomes a
  gate, not a note.
- `format_report` M6 block trigger changes from `data.get("total_cost")` to
  `m6.passed or data.get("reason")` so a refused ticket prints why.
- `decision="TRADE"` requires `M5.passed and M6.passed`. No other path sets
  `TRADE`.

### C.4 Order path (phase 3, default off; warn-only when armed)

`order_limits.py` stays fat-finger only; its docstring is correct and must not
be widened. A separate guard, `scripts/kelly_guard.py:check_kelly_ticket(params,
bankroll)`, runs in `ib_place_order.place_order` immediately after
`check_order_limits` and only when `RADON_KELLY_ENFORCE_ORDERS=1`.

**Warn, not block.** `RADON_KELLY_ENFORCE_ORDERS=1` means the guard is active.
Default mode is `warn` (`RADON_KELLY_ENFORCE_MODE` unset or `warn`): violations
log a WARNING (code + message + loss/bankroll/pct when relevant) and attach
`kelly_warning` on the place_order success JSON. The order still proceeds to
IB. Fat-finger `order_limits` still refuse. `RADON_KELLY_ENFORCE_MODE=block`
restores hard refuse (`status: "error"` before IB). Evaluate M6 fail-closed
for sizing/eval is unchanged.

- Applies to opening orders only. Closing, rolling and reducing orders lower
  risk and are exempt; the classifier reuses the coverage logic behind
  `RADON_MAX_COMBO_LOSS_DOLLARS` (worst-case loss already computed for combos).
- Bankroll source in a fresh interpreter: `NetLiquidation` from the connected
  `IBClient` account summary, falling back to `data/portfolio.json`
  `net_liquidation`. If neither is available the guard **warns** with
  `code: "KELLY_BANKROLL_UNKNOWN"` and continues; an unknown bankroll is not a
  silent pass.
- Warning shape: `{"code": "KELLY_CAP_EXCEEDED", "message": "<max_loss> is
  <pct>% of bankroll; cap is 2.5%", "loss", "bankroll", "pct"}`. place_order
  copies it onto `kelly_warning`. Preserve upstream detail; never collapse to
  500.
- Stock orders: skip (no defined `max_loss`), log at INFO. Naked short options:
  warn with `KELLY_UNDEFINED_RISK` only when the guard is on; Gate 4 remains
  disabled and this guard does not re-enable it.
- `web/lib/orderRisk.ts` gets a display-only `bankrollPct` so the ticket shows
  the number the server will check. Display, never enforcement (chokepoint
  rule in `web/CLAUDE.md`).

`RADON_KELLY_ENFORCE_ORDERS` stays unset on every `radon-*` unit until Joe
signs (§F). Once `=1`, prod warns and still places unless mode is `block`.

---

## D. Test plan (red first, then green)

Files: extend `scripts/tests/test_kelly_extended.py`,
`test_kelly_domain_guards.py`, `test_kelly_vectorized.py`, `test_evaluate.py`;
add `scripts/tests/test_kelly_ticket.py`, `scripts/tests/test_kelly_guard.py`;
extend `lib/tools/__tests__/schemas.test.ts`, `kelly.test.ts`,
`input-domain-guards.test.ts`.

| ID | Red assertion | Surface |
|---|---|---|
| D1 | `kelly(0.6, 2.0, fraction=1.0)` raises `ValueError`; `fraction=0.51` raises; `fraction=0.5, p_source="measured"` succeeds. CLI `--fraction 1.0` exits 2 with argparse error. `Value.Check(KellyInput, {fraction: 0.51})` is `false`; `{fraction: 0.5}` is `true`. Wrapper throws `RangeError` at `0.51`. | full-Kelly ban at every boundary |
| D2 | `kelly(0.6, 2.0)["fraction_used"] == 0.5` and `fraction_source == "default"` with no env set (new default). With `RADON_KELLY_FRACTION=0.25` (monkeypatched, the stricter option): `kelly(0.6, 2.0)["fraction_used"] == 0.25`, `fraction_source == "env"`, and `kelly(0.6, 2.0, fraction=0.5)` with default `p_source="estimated"` raises `ValueError` mentioning `estimated`; `p_source="measured"` still succeeds at `0.5`. `RADON_KELLY_FRACTION=0.9` resolves to `0.5` with `fraction_source == "env_clamped"`. Existing `test_custom_fraction_exact_double` (`0.5` is 2x `0.25`, both explicit) stays green unchanged. | half-Kelly default; estimated-p forces configured fraction |
| D3 | `kelly(0.9, 5.0, bankroll=100_000)["use_size"] == 2500.0` and `capped is True`; equals `kelly_size_batch([0.9],[5.0],100_000)[0]`. Property test over 200 random `(p, b, bankroll)` triples: scalar `use_size == batch[0]` to 1e-9. CLI output `use_size <= max_per_position` for every case. `kelly(0.51, 1.02, bankroll=100_000)["capped"] is False`. | cap always applied; scalar/batch parity |
| D4 | `rg`-based contract test: no file under `scripts/` or `lib/` outside `tests/` passes `max_pct=` to `kelly_size_batch`. The literal `0.025` appears in `scripts/kelly.py` exactly once (the constant). | one cap constant |
| D5 | `kelly(0.3, 1.0, bankroll=100_000)`: `use_size == 0.0`, `contracts` absent, `recommendation == "DO NOT BET"`. `kelly_ticket(prob_win=0.3, max_gain=100, max_loss=100, bankroll=100_000)["contracts"] == 0`, `reason == "NO_EDGE"`. Batch: `prob_wins=[1.5, -0.1, nan]` all size `0`; `fraction=2.0` raises. | no edge gives zero; batch domain |
| D6 | `kelly_ticket(max_loss=0)` and `max_loss=-1` return `reason "UNDEFINED_RISK"`, `contracts 0`. `kelly_ticket(..., bankroll=100_000, max_loss=3000)` with an edge that wants 1 contract returns `contracts 0`, `reason "CAP_BELOW_ONE_CONTRACT"` (3000 > 2500). `portfolio_capacity(100_000, [9_000]*2, 2_000)` is `ok` (`20_000 <= 20_000`); `portfolio_capacity(100_000, [9_000]*2, 2_001)` is not. `kelly_ticket(..., nav_peak=100_000, nav_now=85_000)` returns `reason "DRAWDOWN_HALT"`; `nav_now=85_001` sizes normally. Sequence test: 40 consecutive capped max-loss tickets against a fixed bankroll are refused by capacity at the 9th (`8 * 2.5% = 20%`). | ruin / path-to-zero |
| D7 | `kelly(0.6, 2.0, p_haircut=0.05)["p_effective"] == 0.55` and `full_kelly_pct == round((0.55 - 0.45/2)*100, 2) == 32.5`; `p_haircut=0` reproduces today's `40.0`. `p_haircut=0.3` raises (band). Env `RADON_KELLY_P_HAIRCUT=0.05` applies when the arg is `None`. | fat-tail haircut |
| D8 | `growth_rate_used > 0` whenever `edge_exists`; `growth_rate_full >= growth_rate_used`; `kelly(1.0, 3.0)["growth_rate_full"] is None`; `kelly(0.6, 2.0, fraction=0.5, p_source="measured")["growth_rate_used"] == pytest.approx(0.75 * g_full, rel=0.05)`. | geometric framing |
| D9 | `kelly(0.7, 4.0)["restructure"] is True` (`f_full = 0.625`); `kelly(0.55, 1.5)["restructure"] is True` (`f_full = 0.25`); `kelly(0.52, 1.5)["restructure"] is False` (`f_full = 0.20`, boundary is strict). Recommendation strings unchanged for all existing pinned cases. | restructure flag |
| D10 | `evaluate_ticker("AAPL", bankroll=100_000)` with mocked M1-M4 PASS and no structure: `M5.passed is False`, `M6.passed is False`, `decision == "PENDING"`, exit code `2` (existing test stays green). With `structure={"max_gain": 300, "max_loss": 100, "prob_win": 0.4}`: `M6.passed is True`, `M6.data["contracts"] == 25`, `position_pct == 2.5`, `decision == "TRADE"`. With `prob_win=0.2`: `decision == "NO_TRADE"`, `failing_gate == "RISK"`, `reason == "NO_EDGE"`. With `max_loss=0`: `reason == "UNDEFINED_RISK"`. With `prob_win=0.9, max_gain=1000, max_loss=100` (`f_full = 0.89`): `reason == "RESTRUCTURE"`. `format_report` prints `KELLY SIZING` and the reason for every refusal. | evaluate fails closed without structure + kelly |
| D11 | `check_kelly_ticket` with env flag unset returns `None` for a 10x oversized ticket and emits no WARNING (guard is off). With `RADON_KELLY_ENFORCE_ORDERS=1` (mode default `warn`): opening combo whose worst-case loss is `3%` of bankroll returns `KELLY_CAP_EXCEEDED` with loss/bankroll/pct and logs WARNING; the same ticket flagged closing passes with no warn; unknown bankroll warns `KELLY_BANKROLL_UNKNOWN`; stock order passes. Wire test: `place_order` does **not** return a Kelly error, attaches `kelly_warning`, and the mocked IB `place_order` is called. `RADON_KELLY_ENFORCE_MODE=block` still refuses before IB. | order-path guard, warn-only at the wire |
| D12 | `lib/tools/__tests__/kelly.test.ts` live subprocess: omitted `fraction` returns `fraction_used 0.5`; `fraction: 0.51` returns `ok: false`; with `RADON_KELLY_FRACTION=0.25` in the child env, `fraction: 0.5` returns `ok: false` with the `estimated` message. `schemas.test.ts`: output with the new optional keys validates; output missing them still validates; the static fixtures' `fraction_used: 0.25` become `0.5`. `site/lib/pages/fractional-kelly-position-sizing.test.ts` and `agent-prompts.test.ts`: every "quarter" becomes "half" and the worked example reads `0.2667 / 2 = 13.3%; the 2.5% cap binds`. | TS/Python/site contract |

Run order: `python3.13 scripts/run_pytest_affected.py --files scripts/kelly.py scripts/evaluate.py -- -q`, then `cd web && npx vitest run lib/tools`, never concurrently on the laptop. Full suites before each commit.

---

## E. Cutover

1. **This PR (docs).** Spec, index row, owners rule, cross-links. No code.
2. **Implement PR 1: library + schema parity** (carries must-ship items 1 and
   2 and the Joe-signed `0.5` default). `kelly.py` constants,
   `kelly_config()`, `kelly(bankroll=...)`, growth, haircut (`0.0`), restructure
   flag, batch validation, full-Kelly ban, `p_source`. TypeBox, wrapper,
   `pi-tools` import. **Code default `fraction` moves `0.25 -> 0.5`** (the
   `kelly()` signature default, `kelly_size_batch()` default, CLI `--fraction`
   default, TypeBox description, `pi-tools` description). Tests D1-D5, D7-D9,
   D12. Behavior change, stated plainly: any caller that omits `fraction` now
   gets twice the pre-cap `fractional_kelly_pct` and `dollar_size`; `use_size`
   is unchanged wherever the 2.5% cap already bound (it binds for every
   `f_full >= 0.05`). Calls that pass `fraction` explicitly are numerically
   identical except for added keys. `fraction > 0.5` is refused. Site copy
   (`site/lib/pages/fractional-kelly-position-sizing.ts`, `agent-prompts.ts`)
   and the marketing FAQ move from "quarter" to "half" in the same PR so the
   public page never disagrees with the CLI. Confirm with a before/after run
   of the three existing Python test files and the site tests.
3. **Implement PR 2: `kelly_ticket`, `portfolio_capacity`, M6** (must-ship
   item 3). Tests D6, D10.
   `evaluate.py --structure` flag. Decision `TRADE` becomes reachable for the
   first time; document in `docs/evaluation.md` that M5 still needs a real
   structure source.
4. **Implement PR 3: order-path guard, flag off.** Tests D11. Deploys with
   `RADON_KELLY_ENFORCE_ORDERS` unset. No production behavior change.
5. **Joe decisions (any order, each its own tiny PR or drop-in change):**
   - Fraction: **signed `0.5` (2026-09-19)**; lands as the code default in PR 1,
     so no env drop-in is needed. Tightening back to quarter Kelly is
     `RADON_KELLY_FRACTION=0.25` in `radon-.service.d/common.conf` and the
     laptop launchd env, or the audited preference row once registered.
   - Haircut: leave `0.0` or set `RADON_KELLY_P_HAIRCUT=0.05`.
   - Order guard: set `RADON_KELLY_ENFORCE_ORDERS=1` on `radon-api` only after
     one week of M6 output matching hand-sized tickets in the journal.
6. Prose sync. In PR 1: `.pi/SYSTEM.md` §3 and `docs/prompt.md` constraint 4
   change from "0.25x-0.5x" to "half Kelly (0.5) default, 0.25 optional
   stricter setting, full Kelly banned"; `README.md` Risk row and
   `docs/evaluation.md` M6 follow. After PR 2: the marketing FAQ line
   "milestone 6 enforces the cap" becomes a true statement; until then it is
   aspirational and this spec says so.

Rollback for any implement PR: revert the commit. PR 1's revert restores the
`0.25` default; env flags are additive and unset flags reproduce the numbers of
whichever code default is deployed.

---

## F. Done-when and Joe approval checklist

### Must-ship (Joe-signed 2026-09-19 via CoS, blocks implement PR merge)

- [x] **Default fraction = `0.5` (half Kelly).** `kelly()` /
      `kelly_size_batch()` / CLI defaults, `kelly_config()` fallback, TypeBox
      and `pi-tools` descriptions all read `0.5`. `0.25` documented as the
      stricter optional `RADON_KELLY_FRACTION=0.25`, not the default. Test D2.
- [x] **Ban full Kelly.** `KELLY_MAX_FRACTION = 0.5` enforced at `kelly()`,
      `kelly_size_batch()`, CLI `--fraction`, TypeBox `KellyInput`, wrapper,
      `pi-tools.ts`. Tests D1, D12.
- [x] **Unify the 2.5% cap on scalar `kelly()`.** `kelly(bankroll=...)` returns
      `use_size` capped by `KELLY_MAX_PCT`; CLI and TypeBox consume it verbatim;
      one `0.025` constant. Tests D3, D4.
- [x] **`evaluate.py` M6 fails closed.** No `structure` or no Kelly pass means
      `PENDING`/`NO_TRADE`, never `TRADE`; `failing_gate="RISK"` with a
      `reason`. Test D10.
- [x] **Estimate / fat-tail / ruin hooks.** `p_source` (D2), `p_haircut` (D7),
      `kelly_ticket` + `portfolio_capacity` + drawdown halt (D6). Haircut ships
      at `0.0`; the guards ship with defaults from §B.3.
- [x] **No production gate enabled.** `RADON_KELLY_ENFORCE_ORDERS` unset on
      every unit (`rg -n "RADON_KELLY_ENFORCE_ORDERS" cloud/ config/` returns
      nothing). Enabling waits for a separate Joe signature after the PRs land.

### Done-when: implement PRs

- [ ] Every D-row above exists as a named test, was red on `main` before the
      change, and is green on the PR head.
- [ ] `python3.13 scripts/kelly.py --prob 0.6 --odds 2 --bankroll 100000`
      on the PR head prints `fraction_used: 0.5`, `fractional_kelly_pct: 20.0`,
      `use_size: 2500.0`, `capped: true`; with `--fraction 0.25` the output
      matches `main` except for added keys.
- [ ] `rg -n "0\.025" scripts/kelly.py` returns exactly one line.
- [ ] `rg -n "maximum: 1" lib/tools/schemas/kelly.ts lib/tools/pi-tools.ts`
      returns nothing for `fraction`.
- [ ] `evaluate_ticker` cannot return `TRADE` without `M5.passed and M6.passed`
      (test D10 plus a grep that `decision = "TRADE"` appears once).
- [ ] `docs/owners.json` rule `kelly-sizing` made this doc change alongside the
      code (the docs contract test enforces it).
- [ ] CI green on the exact head; `radon PR green` Pushover sent.

### Joe approval checklist

- [x] **Fraction: half Kelly (`0.5`) is the default.** Signed 2026-09-19 via
      CoS. `0.25` remains a documented stricter optional setting.
- [ ] Haircut: leave `0.0` or adopt `0.05` on `p`?
- [ ] Merge order and timing for implement PRs 1-3 (each is independently
      revertible; PR 2 is the one that changes evaluate's exit code from `2`
      to `0` on a fully specified ticket).
- [ ] **Enable `RADON_KELLY_ENFORCE_ORDERS=1` in production** (after the PRs
      land; not in the implement PR), or retain its current setting after separately reviewing the
      all-placer bankroll preference described above.
- [ ] Register `RADON_KELLY_FRACTION` in `app_preferences` (audited, UI) vs
      env-only.

Implement default is `0.5`. `0.25` is the opt-in stricter setting.
`1.0` is refused. Production `RADON_KELLY_ENFORCE_ORDERS` still waits Joe.
