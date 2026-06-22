# Radon — Time-Series Forecasting Model Backlog

## Hard rule (applies to every feature below)
**Never forecast price/direction.** Forecast the structured, mean-reverting, autocorrelated
series Radon already computes (vol, flow volumes, breadth, regime indices). The edge is the
forecast *residual* (actual − expected) or the *quantile spread* (risk input), mapped onto the
Four Gates. Backtest every feature against a dumb baseline before shipping.

---

## TimesFM candidate features (Google decoder-only TS foundation model) — PRESERVE

Source: research.google/blog/a-decoder-only-foundation-model-for-time-series-forecasting
200M params · 100B time-points · input patch 32 → output patch 128 · zero-shot · quantile heads
(2.0: context ~2048 + covariates). Caveat: trained on traffic/weather/demand; price is its
adversarial worst case.

### [ ] Feature 2 — Flow-volume nowcast + residual anomaly detector  (BUILD FIRST)
- Forecast expected baseline per ticker for DPI / print volume / sweep counts / premium
  (count-like seasonal series = TimesFM's home turf).
- Edge = residual: flag tickers where today's flow exceeds the 0.9-quantile expectation.
  Replaces hand-tuned z-score thresholds.
- Ships: `expected_flow` + `flow_surprise` (actual ÷ forecast-quantile) on scan/discover +
  VCG/GEX caches; re-rank watchlist by surprise. Re-ranking of existing surfaces, not a new page.
- Serves Gate 2 (Edge — signal that hasn't moved price). Defensive: kills false positives.
- Risk: flow is spiky/event-driven; nominal quantile calibration off — recalibrate on own data.
- Confidence: moderate-to-high (mechanism sound, calibration is the work).

### [ ] Feature 1 — Regime-index forecast layer  (BUILD SECOND)
- Forecast CRI / VCG / GEX / VIX-term indices already persisted to Turso (`/regime/*`).
- Ships: forecast band (median + 0.1/0.9) extending each regime chart 5-20 sessions, using the
  established regime-chart convention (BrushMinimap + presets). Nightly systemd unit writes
  `regime_forecast` table.
- Serves Gate 2 timing. Predicts structural state, not price.
- Risk: smooths over gaps/holidays — display conditional-on-no-shock + overlay residual.
- Confidence: high (works as feature) / moderate (beats damped-trend baseline).

### [ ] Feature 3 — Probabilistic expected-move cone  (BUILD THIRD, gate behind backtest)
- Forecast the *distribution* of near-term realized vol (NOT direction).
- Quantile output → forward expected-move cone in one pass; zero-shot alternative/ensemble to
  `garch-convergence`.
- Ships: `forecast_cone` in `evaluate [TICKER]` + order-builder; OrderRiskGate consumes
  distribution → P(max gain), P(max loss), Kelly-implied size.
- Serves Gates 1 (convexity) + 3 (Kelly) — turns heuristics into distributional math.
- SHARPEST RISK: do not let it become a directional forecaster. Price cone = WIDTH only;
  center is noise, treat as zero. Intraday is OOD — daily bars only.
- Confidence: moderate (vol width) / low (price center — treat as zero).

### Cross-cutting
- [ ] Backtest each vs dumb baseline (seasonal-naive flow / AR-damped regime / GARCH vol) using
      the `quant-backtest` skill. Tie with a one-line baseline = not worth a systemd unit.

Report rendered at: tasks/timesfm-features-report.html

---

## Alternative-model scouting (Exa Agent research)
Results from the HuggingFace / GitHub / arXiv scout appended below when the workflow completes.

## SCOUT RESULTS — Exa Agent research (2026-06-20)

**Bottom line:** Replace zero-shot TimesFM with Chronos-2 as Radon's primary forecaster (Apache-2.0, native covariates, calibrated 0.01-0.99 quantiles), back it with Datadog Toto for heavy-tailed vol/Kelly and IBM TTM for cheap nightly fleet baselines -- but treat ALL of them as needing finance fine-tuning before they gate real trades.

**TimesFM verdict:** REPLACE as the primary, but keep a fine-tuned TimesFM only as an optional ensemble baseline -- do not rely on its zero-shot quantiles for live gating. TimesFM's quantile heads are explicitly uncalibrated and it has no real covariate interface, so it cannot honestly feed Gate 1 (convexity) or Gate 3 (Kelly), and two finance papers show its zero-shot is weak on low-SNR markets. Concrete plan: make Chronos-2 the default engine (covariate-conditioned flow residuals + regime bands + tail quantiles for the vol cone), add Toto (Toto-Open-Base-1.0, Apache-safe) as a heavy-tailed second opinion for the vol cone and Kelly, and use IBM TTM as the cheap CPU baseline for nightly fleet-wide expected-value flow residuals. An ensemble of Chronos-2 + Toto (median of quantiles) is the strongest defensible setup; TimesFM only earns a slot if you continual-pretrain it on financial data, which the research shows is what makes it usable at all.

**Honest warning:** EVERY model here is a general-purpose forecaster: finance is under ~1% of all their pretraining corpora (TimesFM <0.01%), and multiple finance papers (VaR arXiv:2410.11773, fine-tuning arXiv:2412.09880, Marconi arXiv:2507.07296) show zero-shot generic TSFMs do NOT consistently beat naive baselines on low-SNR, heavy-tailed, regime-switching market data -- and none was validated specifically on dark-pool/OTC flow series at all. Do not let any model's quantile output size a position (Gate 3) or define a convexity cone (Gate 1) until you have backtested its calibration on YOUR flow/vol series out-of-sample; benchmark leaderboards (GIFT-Eval) are general-domain and will mislead you here. The finance-native option (Kronos) models price bars, which Radon declares out of scope, and its license is unverified. Plan for fine-tuning or residual-only use, verify every license against the actual HF checkpoint before commercial deployment, and never ship a forecast straight into the gates without an empirical calibration check.

### Ranked candidates (best fit for Radon first)

#### 1. Amazon Chronos-2 (Amazon Science) — confidence: high
- 120M Apache-2.0 universal forecaster with native known-future + multivariate covariates and 21 quantiles incl. 0.01/0.99 tails.
- URL: https://huggingface.co/amazon/chronos-2
- vs TimesFM: Beats TimesFM on the three things Radon actually needs: (1) native covariate/known-future support (condition flow on VIX, day-type, regime) vs TimesFM's frequency-indicator-only; (2) real calibrated 21-quantile output incl. 0.01/0.99 tails vs TimesFM's uncalibrated quantile heads (research explicitly flags TimesFM quantiles as NOT calibrated); (3) multivariate so cross-asset flow panels work in one pass. Same Apache-2.0, smaller (120M vs 500M), longer 8192 context.
- Radon fit: All four surfaces: flow residual (covariate-conditioned baseline -> actual minus expected quantile = edge), regime band (CRI/VCG/GEX/VIX-term forecast bands), vol cone (0.01/0.99 quantiles feed Gate 1 convexity), Kelly (full distribution feeds Gate 3 fractional sizing).
- Probabilistic: Yes, native: 21 quantiles per step including 0.01 and 0.99 tail quantiles -- directly usable for tail-risk and Kelly.
- Covariates: Yes, native: univariate, multivariate, AND known-future covariates (calendar/VIX/day-type regressors).
- Self-host: Apache-2.0, 120M -> runs on a single modest GPU or even CPU on Hetzner; weights on HF (amazon/chronos-2); pip-installable. Easiest fully-permissive self-host of any top-tier covariate model.
- Caveats: Zero-shot SOTA claims are vendor-stated; on low-SNR flow series expect to validate calibration empirically and likely fine-tune. CRPS-rank trails Toto 2.0 on GIFT-Eval (23.5 vs 20.3) but GIFT-Eval is general, not finance, and Chronos-2 wins on covariates+license which matter more for Radon.

#### 2. Datadog Toto (Toto-Open-Base-1.0 / Toto 2.0) (Datadog AI Research) — confidence: high
- Observability-native, heavy-tailed multivariate forecaster -- current GIFT-Eval foundation-model leader; Student-T mixture (1.0) / quantile head (2.0).
- URL: https://huggingface.co/Datadog/Toto-Open-Base-1.0
- vs TimesFM: Designed for sparse, non-stationary, outlier-heavy, heavy-tailed telemetry -- the closest generic analog to dark-pool flow and vol spikes. Student-T mixture head is explicitly motivated by heavy-tailed financial-TS literature, so it handles fat tails far better than TimesFM's Gaussian-ish uncalibrated heads. Top reproducible GIFT-Eval FM (CRPS-rank 20.3 for 2.5B). Multivariate + probabilistic out of the box.
- Radon fit: Vol cone + Kelly (heavy-tailed distribution is ideal for fat-tailed realized-vol), regime band (multivariate handles CRI/VCG/GEX jointly), flow residual (built for spiky high-cardinality series like print volume/sweeps).
- Probabilistic: Yes, strong: Toto 1.0 Student-T mixture (excellent for fat tails), Toto 2.0 quantile/pinball head. Full distribution sampling.
- Covariates: Multivariate native; exogenous/covariate fine-tuning added in 2026 tooling (Toto-2). Not as clean a known-future API as Chronos-2.
- Self-host: Toto-Open-Base-1.0 (151M) is clearly Apache-2.0, single-GPU. WARNING: Toto-2.0 (up to 2.5B) license clarity is weaker per the research -- prefer Toto-Open-Base-1.0 for commercial trading unless you confirm 2.0 terms.
- Caveats: License caveat on 2.0 weights -- verify before production; pin to Toto-Open-Base-1.0 if unsure. No finance benchmark in-paper (observability transfer is a hypothesis, not proven on flow). Known-future covariate ergonomics lag Chronos-2.

#### 3. The Forecasting Company t0-alpha (The Forecasting Company) — confidence: moderate
- ~102M Apache-2.0 newcomer with native past + known-future covariates and 0.1-0.9 quantiles -- covariate-native at small scale.
- URL: https://huggingface.co/theforecastingcompany/t0-alpha
- vs TimesFM: True known-future covariate interface (vs TimesFM's none) plus native quantiles under a clean Apache-2.0 license at one-fifth the params of TimesFM-2.0. Published GIFT-Eval/fev numbers (CRPS 0.4941, MASE 0.7240) are competitive. For covariate-conditioned flow baselines it does exactly what Radon needs.
- Radon fit: Flow residual (condition DPI/print-vol on VIX/day-type) and regime band; vol cone via 0.1-0.9 quantiles (no 0.01/0.99 tails, so weaker for extreme-tail Kelly than Chronos-2/Toto).
- Probabilistic: Yes: native quantiles 0.1/0.25/0.5/0.75/0.9. No extreme tail quantiles (0.01/0.99), which limits tail-risk precision.
- Covariates: Yes, native historical AND known-future covariates -- a core design feature.
- Self-host: Apache-2.0, open weights, ~102M -> trivial single-GPU/CPU self-host on Hetzner.
- Caveats: Very new, ~2,266 monthly downloads -> unproven maintenance and small community. Quantile range stops at 0.1/0.9 so tail convexity gating needs interpolation/extrapolation. Treat as a strong covariate-native challenger, not yet a production default.

#### 4. THUML Sundial (base-128m) (THUML (Tsinghua)) — confidence: moderate
- 128M Apache-2.0 generative (TimeFlow) model producing sample paths -> arbitrary quantiles incl. custom tails; top GIFT-Eval MASE at release.
- URL: https://huggingface.co/thuml/sundial-base-128m
- vs TimesFM: Generative sample paths let you derive ANY quantile (including 0.01/0.99 tails) and run Monte-Carlo scenario fans for the expected-move cone -- more flexible than TimesFM's fixed uncalibrated heads. Apache-2.0, lightweight, strong benchmark standing. Also has the highest incidental finance-corpus share (~1.02%) among generic TSFMs per Kronos's analysis.
- Radon fit: Vol cone + Kelly via Monte-Carlo sample paths (scenario fans feed convexity directly); regime band per index. Weaker for flow residual because covariate conditioning is not described.
- Probabilistic: Yes, excellent shape: generative sample paths -> derive any statistic/quantile, ideal for scenario/Kelly Monte-Carlo.
- Covariates: No / not described -- univariate-centric. This is the main gap vs Chronos-2/Toto for conditioning flow on VIX/day-type.
- Self-host: Apache-2.0, 128M, single-GPU; HF weights (thuml/sundial-base-128m).
- Caveats: No covariate support kills the flow-residual use case (its strongest Radon need). Use it specifically for the vol cone / scenario sampling, not for conditioned flow baselines. Benchmark lead since surpassed by Timer-S1.

#### 5. IBM Granite TinyTimeMixer (TTM R2) (IBM Granite) — confidence: moderate
- ~1-5M Apache-2.0 CPU-only model with exogenous + static categorical infusion -- the cheap nightly-watchlist workhorse.
- URL: https://huggingface.co/ibm-granite/granite-timeseries-ttm-r2
- vs TimesFM: Runs the entire watchlist nightly on CPU at near-zero cost with covariate (exogenous + static categorical) support TimesFM lacks. Card claims it beats TimesFM/Moirai/Chronos/Lag-Llama in zero/few-shot at a fraction of the size. NOT better on probabilistic output, which is its disqualifier for the gates.
- Radon fit: Flow residual baseline at fleet scale (point expected-value of DPI/print-vol with exog conditioning). Does NOT serve vol cone or Kelly well because it is point-focused.
- Probabilistic: Weak / point-focused. No confirmed calibrated quantiles -> cannot directly feed Gate 1 convexity or Gate 3 Kelly. This caps it at the residual-baseline role.
- Covariates: Yes: exogenous infusion + static categorical infusion.
- Self-host: Apache-2.0, ~1-5M params, true CPU-only on Hetzner; HF (ibm-granite/granite-timeseries-ttm-r2). Best cost/latency profile in the survey.
- Caveats: Point forecasts only -- it is a baseline/expected-value engine, not a distribution engine. Use it as the cheap covariate-aware mean for flow residuals and pair with a probabilistic model (Chronos-2/Toto) for vol/Kelly.

#### 6. Kronos (shiyu-coder / academic (arXiv:2508.02739)) — confidence: low
- The only finance-NATIVE foundation model with real traction -- trained on 12B OHLCV K-lines, tokenized candlestick sampling.
- URL: https://github.com/shiyu-coder/Kronos
- vs TimesFM: Finance-native: directly addresses the core thesis that generic TSFMs (TimesFM <0.01% finance corpus) fail on low-SNR, heavy-tailed market data. Reports +93% RankIC and 9% lower vol-MAE vs the best generic TSFM (Time-MoE). On its own terms it is the right kind of model for markets. BUT it forecasts OHLCV price bars -- which Radon explicitly puts OUT OF SCOPE (near-martingale) -- not dark-pool flow series, so the domain match is to the wrong target.
- Radon fit: Vol cone only, plausibly (vol-MAE evidence). Poor fit for flow-residual/regime because it is built around price candlesticks, not flow/DPI/sweep series or Radon's computed regime indices.
- Probabilistic: Yes via autoregressive sampling of future bars (distributional sample paths).
- Covariates: Partial -- designed around OHLCV bars, not generic exogenous regressors like VIX/day-type.
- Self-host: License UNKNOWN/unverified across both research angles -- hard blocker until confirmed. Weights on HF (NeoQuasar/Kronos-mini). Self-hostable size-wise.
- Caveats: Two blockers: (1) license unverified -- do not deploy commercially until confirmed; (2) it models price bars, the one thing Radon declares out of scope. Its vol output could feed the cone, but it does not serve Radon's flow-signal-first mandate. Interesting, not a primary.

#### 7. Salesforce Moirai 2.0 / Moirai-MoE (uni2ts) (Salesforce AI Research) — confidence: moderate
- Any-variate multivariate forecaster with strong covariate support; Moirai 2.0 small (11.4M) is Apache-2.0, but classic 1.x checkpoints are research-only.
- URL: https://github.com/SalesforceAIResearch/uni2ts
- vs TimesFM: Best any-variate/multivariate covariate handling among open universal models -- natively ingests cross-asset panels + covariates, which TimesFM cannot. Quantile output in 2.0. Better than TimesFM for conditioned multivariate flow.
- Radon fit: Regime band (multivariate CRI/VCG/GEX/VIX jointly) and flow residual with covariates; vol cone via quantiles.
- Probabilistic: Yes: Moirai 1.x mixture distributions; Moirai 2.0 quantile loss (9 quantiles, 0.1-0.9, no extreme tails).
- Covariates: Yes, native any-variate -- the strongest multivariate design in the survey alongside Chronos-2.
- Self-host: MIXED LICENSE -- this is the catch. Moirai 1.x large/MoE are RESEARCH-ONLY (non-commercial) -> disqualified for a trading app. Only Moirai 2.0 small (Salesforce/moirai-2.0-R-small, Apache-2.0) is commercially safe; verify the exact checkpoint license before use.
- Caveats: License is the dealbreaker for the high-performing 1.x/MoE checkpoints (research-only). Only the Apache-2.0 Moirai-2.0 variants are usable, and those trail Chronos-2/Toto. Ranked here mainly because most of its standout checkpoints are off-limits commercially.

#### 8. Google TimesFM 2.0 / 2.5 (incumbent baseline) (Google Research) — confidence: high
- The current Radon baseline -- 500M (2.0) / 200M (2.5) decoder-only, Apache-2.0, but uncalibrated quantiles, no real covariates, weak zero-shot on markets.
- URL: https://github.com/google-research/timesfm
- vs TimesFM: N/A -- this IS TimesFM. Listed for reference. It is NOT better than the models above for Radon: research explicitly flags its quantile heads as uncalibrated, it has no general covariate interface (frequency indicator only), and two finance papers (VaR arXiv:2410.11773; fine-tuning arXiv:2412.09880) show zero-shot TimesFM is weak on low-SNR markets and needs fine-tuning.
- Radon fit: Currently used; nominally serves all four but poorly -- uncalibrated quantiles undermine Gate 1 convexity and Gate 3 Kelly, and no covariates undermines flow-residual conditioning.
- Probabilistic: Quantile heads exist but are explicitly UNCALIBRATED per research -> unreliable for sizing/convexity gates without recalibration.
- Covariates: No general covariates -- frequency indicator only. Major gap for Radon. (Note: the timesfm GitHub xreg_lib offers limited external regressor support, but it is not a first-class covariate interface like Chronos-2's.)
- Self-host: Apache-2.0 (code + checkpoints); 200M/500M single-GPU. Self-host is fine; that was never the problem.
- Caveats: Trained on traffic/weather/demand; price/markets are its worst case and finance corpus is <0.01%. Keep only as a sanity baseline or fine-tune it on financial data (proven to help) -- do not rely on zero-shot quantiles for live gating.

#### 9. Lag-Llama (ServiceNow / Morgan Stanley / Mila) — confidence: moderate
- Tiny 2.45M Apache-2.0 Student-t probabilistic model -- heavy-tailed by design but dated and benchmark-trailing.
- URL: https://github.com/time-series-foundation-models/lag-llama
- vs TimesFM: Native Student-t (heavy-tailed) per-step distribution is conceptually better suited to fat-tailed financial returns than TimesFM's uncalibrated heads, and a Morgan Stanley co-author signals finance intent. But it is univariate (no covariates), tiny, and outperformed by every model above.
- Radon fit: Single-series vol cone / probabilistic baseline only. No covariates -> no flow-residual conditioning; not for cross-asset regime.
- Probabilistic: Yes, native Student-t distribution (good fat-tail shape).
- Covariates: No -- univariate, lag-feature based.
- Self-host: Apache-2.0, 2.45M, trivially self-hostable (CPU).
- Caveats: Maintenance has slowed (low 2025-26 activity); benchmark-trailing; univariate-only. Reasonable cheap probabilistic baseline, not a primary. Best used fine-tuned.

#### 10. Nixtla TimeGPT-2.1 (Nixtla) — confidence: high
- Strong exogenous + conformal-interval forecaster -- but a CLOSED paid hosted API, not self-hostable.
- URL: https://nixtla.io/docs/forecasting/timegpt_2_family
- vs TimesFM: On features (conformal prediction intervals, exogenous + multivariate) it is attractive, but this is irrelevant for Radon: it is a closed proprietary API with NO open weights (Nixtla HF org has 0 public models). Sending dark-pool/flow signals to a third-party API and paying per-call violates Radon's self-host + edge requirements. Disqualified on architecture, not accuracy.
- Radon fit: Would fit feature-wise (covariates + intervals), but disqualified -- cannot self-host on Hetzner, and routing proprietary edge signals off-box is a non-starter.
- Probabilistic: Yes, conformal prediction intervals.
- Covariates: Yes, historical + future exogenous.
- Self-host: NO -- closed hosted API only, paid per-call, no public weights. Hard disqualifier for Radon's self-host + 'flow signal or nothing' edge-secrecy posture.
- Caveats: Ranked last among serious candidates purely because it cannot be self-hosted and leaks edge signals to a vendor. Do not use for a proprietary trading app regardless of benchmark claims.

### Recommended action items
- [ ] Adopt **Chronos-2** (amazon/chronos-2, Apache-2.0, 120M) as primary engine: covariate-conditioned flow residuals + regime bands + 0.01/0.99 tail quantiles for vol cone/Kelly.
- [ ] Add **Datadog Toto-Open-Base-1.0** (Apache-2.0, 151M) as heavy-tailed second opinion for vol cone + Kelly; ensemble = median of Chronos-2 + Toto quantiles.
- [ ] Use **IBM Granite TTM R2** (Apache-2.0, ~1-5M, CPU-only) as the cheap nightly fleet-wide flow-residual baseline (point expected-value).
- [ ] Demote TimesFM to optional ensemble baseline ONLY; do NOT use its uncalibrated zero-shot quantiles for Gate 1/Gate 3.
- [ ] BEFORE any model gates a trade: backtest quantile CALIBRATION on Radon's own flow/vol series out-of-sample. GIFT-Eval is general-domain and will mislead.
- [ ] Verify exact HF checkpoint LICENSE before commercial deploy (Moirai 1.x = research-only; Toto 2.0 unclear; Kronos unknown; TimeGPT = closed paid API, disqualified).

---

## SHIPPED — Chronos-2 setup (branch feat/chronos2-forecasting, 2026-06-20, NOT pushed)

Foundation laid via a 4-agent workflow + verify pass. Full suite: 3191 passed, 0 failed.

Files created:
- scripts/forecasting/chronos_engine.py — engine: forecast_quantiles() + QuantileForecast,
  lazy torch/chronos import, model API isolated in _raw_quantile_forecast (carries VERIFY
  warning), non-crossing enforced via np.sort, singleton pipeline cache. is_available() gate.
- scripts/forecasting/flow_history.py — flow_series_for() reader + record_daily_flow() accrual (best-effort).
- scripts/chronos_forecast.py — CLI: build_forecast_output() returns ok/insufficient_history/engine_unavailable; stdout=JSON, dual-writes forecast_snapshots.
- scripts/db/migrations/0014_ticker_flow_history.sql, 0015_forecast_snapshots.sql
- requirements-forecasting.txt — chronos-forecasting + torch, ISOLATED from core fleet.
- tests: test_chronos_engine.py, test_forecast_writers.py, test_flow_history.py, test_chronos_forecast_cli.py (26 new, all green, no torch needed).

Files edited (surgical):
- scripts/db/writer.py — upsert_ticker_flow_history / upsert_forecast_snapshot / get_ticker_flow_history.
- scripts/scanner.py — best-effort record_daily_flow() per ticker (history starts accruing now).
- scripts/api/server.py — POST /forecast/chronos route.
- web/lib/serviceHealthWindows.ts — registered chronos-forecast writer (on-demand, no IB).

OPEN ITEMS before this can gate a trade:
- [ ] Install requirements-forecasting.txt on the forecasting host (Hetzner) + VERIFY the real
      Chronos-2 predict_df API against _raw_quantile_forecast (the only unverified code path).
- [ ] Accrue >=8 sessions of ticker_flow_history per ticker before forecasts return "ok".
- [ ] Backtest quantile CALIBRATION on real flow series before wiring into Gate 1 / Gate 3.
- [ ] Build the flow_surprise residual (actual vs forecast 0.9-quantile) on scan/discover — Feature 2 proper.
- [ ] Decide where the engine runs (Hetzner CPU vs GPU) + add a nightly forecast scheduler.

---

## SHIPPED — Calibration backtest + flow_surprise (2026-06-21, commit 2c4262d, NOT pushed)

(1) DONE — calibration backtest harness:
- scripts/forecasting/backtest.py — pure scoring (pinball_loss, pit_of_actual,
  empirical_coverage, calibration_table), baseline_forecaster (flat empirical
  quantiles = the bar to beat), walk_forward (expanding window), run_backtest
  (chronos vs baseline + verdict). No torch deps.
- scripts/forecast_backtest.py — CLI (offline tool, no service-health writer).

(2) DONE — flow_surprise residual (Feature 2 proper):
- scripts/forecasting/flow_surprise.py — compute_flow_surprise (1-step-ahead PIT
  of today's actual vs forecast; EXCESS>=0.9 / DEFICIT<=0.1 / NORMAL) +
  rank_watchlist_surprise (by abs(PIT-0.5)). Degrades to baseline when engine off.
- scripts/flow_surprise.py CLI + POST /flow-surprise + flow-surprise registered.

Tests: 21 new, green without torch. Full suite 3229 passed.

STILL OPEN before this gates a trade:
- [ ] Accrue real ticker_flow_history (>=10 sessions per ticker).
- [ ] Run forecast_backtest.py on real flow series; confirm chronos BEATS baseline
      and calibration_error is small per level. If it ties baseline -> not worth it.
- [ ] UI surfacing of /flow-surprise on the dashboard (frontend, separate).
- [ ] Managed forecasting venv + nightly scheduler (separate deploy step).

---

## SHIPPED — remaining items (2026-06-21, branch feat/chronos2-forecasting, NOT pushed)

- DONE history backfill: scripts/forecasting/backfill_flow_history.py — reuses
  fetch_flow.analyze_darkpool over the 15-day darkpool cache -> ticker_flow_history.
  Run: python scripts/forecasting/backfill_flow_history.py --days 20
- DONE calibration report: scripts/forecasting/calibration_report.py +
  forecast_calibration.py CLI + migration 0016_forecast_calibration + writer.
  Runs run_backtest over the watchlist, persists per-ticker verdict + max calibration error.
- DONE nightly runner + deploy: scripts/forecasting/nightly_forecast.py (backfill ->
  flow_surprise -> calibration) + provision_venv.sh + docs/forecasting-deploy.md
  (systemd templates; units belong in radon-cloud).
- DONE UI: /api/flow-surprise route + useFlowSurprise hook + FlowSurpriseCard on the
  dashboard (brand tokens, force-dynamic/no-store). flow_surprise.py CLI now mirrors
  data/flow_surprise.json for the GET route.

Tests: 21 new Python + 8 vitest, full suite 3258 passed. Fixed an order-dependent
test-isolation bug in test_nightly_forecast.py (setattr on real modules, not sys.modules swap).

STILL PENDING (operational, not code):
- [ ] Browser E2E (chrome-cdp/Playwright) of FlowSurpriseCard — web/e2e/flow-surprise.spec.ts authored, NOT run.
- [ ] Provision venv on Hetzner + run backfill + forecast_calibration --persist on REAL data -> read the verdict.
- [ ] Wire radon-forecast-nightly.{service,timer} in radon-cloud + deploy.

---

## OPERATIONAL — live on Hetzner (2026-06-21/22, deployed to main)

ALL forecasting code deployed to main (CI green). Live on the Hetzner forecasting host:

- Provisioned /home/radon/forecasting-venv (python3.13, CPU torch 2.12.1, chronos 2.3.0,
  + full app deps). provision_venv.sh fixed (app deps + CPU torch index + py3.13 + venv-capable check).
- Backfill: 656 dark-pool cache files -> ticker_flow_history (682 rows, ~130 tickers, ~12 sessions each).
- systemd radon-forecast-nightly.{service,timer} installed + enabled (next 07:00 UTC daily,
  Persistent). Units tracked in radon-cloud/services/. Service verified: runs end-to-end,
  writes data/flow_surprise.json + persists forecast_calibration.
- Fixed a live-exposed gap: write_cache was on the CLI not the library, so the nightly skipped
  the dashboard cache. Moved into forecasting/flow_surprise.py; both paths share it.

### CALIBRATION VERDICT (flow_strength, horizon=1, ~12 sessions/ticker)  ==> NO-GO at this depth
- chronos beats baseline on only 11/29 tickers (38%) — the dumb empirical-quantile baseline wins 18/29.
- median max_calibration_error 0.4 — quantiles badly miscalibrated (nominal 0.9 != 0.9 coverage).
- Series are only 10-12 points (15-day cache ceiling); calibration runs on ~4 origins -> noisy.
- DECISION: do NOT wire Chronos-2 into Gate 1 / Gate 3. flow_surprise stays advisory; prefer the
  baseline forecaster for the residual until history is deep enough.

### Next (let it run, then re-judge)
- [ ] Let nightly accrue + re-backfill daily. Re-read forecast_calibration once ticker_flow_history
      has 60-90+ sessions/ticker. Only then reconsider Chronos for gating.
- [ ] Watch the all-DEFICIT degenerate case (a market-wide quiet day reads as 29 deficits); consider
      a market-wide-quiet guard in flow_surprise.
- [ ] Dashboard card live on app.radon.run (verified empty-state + populated render via chrome-cdp).
