# Status & Decision Log

## Last Updated
2026-04-23T16:52:00-07:00

## Recent Evaluations

### AAPL — 2026-04-23 ⛔ NO_TRADE
- **Decision**: NO_TRADE
- **Failing Gate**: EDGE (Milestone 4)
- **📊 Data as of**: 2026-04-23 04:41 PM PT (LIVE)
- **Reason**: Aggregate flow strength 30.3 below threshold (>50 required). Only 2 sustained accumulation days. Recent strength weak (17.2 today). Signal appears priced in despite bullish news and strong OI changes.
- **Dark Pool**:
  - 2026-04-23: 58.6% buy / 17.2 ACCUMULATION
  - 2026-04-22: 95.0% buy / 90.0 ACCUMULATION
  - 2026-04-21: 48.4% buy / 0.0 NEUTRAL
  - 2026-04-20: 74.8% buy / 49.5 ACCUMULATION
  - 2026-04-17: 67.2% buy / 34.5 ACCUMULATION
  - AGGREGATE: 65.1% buy / 30.3 ACCUMULATION
- **OI Changes**: MASSIVE 3, LARGE 3, SIGNIFICANT 22, Total $150,011,386
- **News**: BULLISH sentiment (score 0.15), 20 headlines (5 bull, 2 bear, 13 neutral), catalysts: AI_CATALYST(3)

### TSLA — 2026-03-25 ⛔ NO_TRADE
- **Decision**: NO_TRADE
- **Failing Gate**: TICKER_VALIDATION
- **📊 Data as of**: 2026-03-25 07:31 AM PT (LIVE)
- **Reason**: M1 failed (`fetch_ticker.py` via evaluate pipeline): `API error: Too Many Requests`.
- **Status**: Evaluation stopped at Gate 1; no fresh options flow / OI / options-flow data fetched.

### AAPL — 2026-03-25 ⛔ NO_TRADE
- **Decision**: NO_TRADE
- **Failing Gate**: EDGE (Milestone 4)
- **📊 Data as of**: 2026-03-25 06:40 AM PT (LIVE)
- **Reason**: Aggregate flow strength is 18.1 (below threshold >50) with 0 sustained days and no sustained directional edge.
- **Dark Pool**:
  - 2026-03-25: 36.3% buy / 27.4 DISTRIBUTION
  - 2026-03-24: 76.4% buy / 52.8 ACCUMULATION
  - 2026-03-23: 66.4% buy / 32.7 ACCUMULATION
  - 2026-03-20: 63.8% buy / 27.6 ACCUMULATION
  - 2026-03-19: 64.1% buy / 28.3 ACCUMULATION
  - 2026-03-18: 22.3% buy / 55.5 DISTRIBUTION
  - AGGREGATE: 59.0% buy / 18.1 ACCUMULATION
- **OI Changes**: MASSIVE 0, LARGE 2, SIGNIFICANT 11, Total $54,868,496
- **News**: BEARISH sentiment (-0.25), catalysts: AI_CATALYST(8), PRODUCT_LAUNCH(2), REGULATORY(2), LEGAL(2), DEAL(1)

### TSLA — 2026-03-24 ⛔ NO_TRADE
- **Decision**: NO_TRADE
- **Failing Gate**: EDGE (Milestone 4)
- **📊 Data as of**: 2026-03-24 08:27 PM PT (LIVE)
- **Reason**: Aggregate flow is NEUTRAL (52.3% buy, 0.0 strength), with only 1 sustained day and no current directional force (recent strength 0.0). Options flow is conflicting on this tape.
- **Dark Pool**:
  - 2026-03-24: 47.3% buy / 0.0 NEUTRAL
  - 2026-03-23: 80.8% buy / 61.6 ACCUMULATION
  - 2026-03-20: 18.4% buy / 63.2 DISTRIBUTION
  - 2026-03-19: 63.2% buy / 26.4 ACCUMULATION
  - 2026-03-18: 70.5% buy / 41.0 ACCUMULATION
  - Aggregate: 52.3% buy / 0.0 NEUTRAL
- **OI Changes**: MASSIVE 4, LARGE 6, SIGNIFICANT 12, Total $145,976,037
- **News**: BEARISH sentiment (-0.15), catalysts: AI_CATALYST(4), PRODUCT_LAUNCH(2), GUIDANCE(1)

### CRWV — 2026-03-24 ⛔ NO_TRADE
- **Decision**: NO_TRADE
- **Failing Gate**: EDGE (Milestone 4)
- **📊 Data as of**: 2026-03-24 08:26 PM PT (LIVE)
- **Reason**: Aggregate flow strength 12.0 below threshold (need >50). Recent flow is mixed with only 28.2 current-day strength and no sustained directional edge.
- **Dark Pool**:
  - 2026-03-24: Distribution 35.9% buy / 28.2 strength
  - 2026-03-23: Accumulation 63.4% buy / 26.8 strength
  - 2026-03-20: Accumulation 65.9% buy / 31.7 strength
  - 2026-03-19: Neutral 52.2% buy / 0.0 strength
  - 2026-03-18: Accumulation 57.0% buy / 14.0 strength
  - Aggregate: 56.0% buy / 12.0 strength (ACCUMULATION)
- **OI Changes**: MASSIVE 0, LARGE 0, SIGNIFICANT 6, Total $23,244,062
- **News**: BEARISH sentiment (-0.25), catalysts: EARNINGS_BEAT, AI_CATALYST, REGULATORY

### IREN — 2026-03-24 ⛔ NO_TRADE
- **Decision**: NO_TRADE
- **Failing Gate**: EDGE (Milestone 4)
- **📊 Data as of**: 2026-03-24 08:24 PM PT (LIVE)
- **Reason**: Aggregate flow direction is NEUTRAL (45.1% buy, 0.0 strength), with 0 sustained days and low recent strength (15.1). Options flow did not conflict, but recent dark-pool context is not directional enough.
- **Dark Pool**:
  - 2026-03-24: DISTRIBUTION 42.4% buy / 15.1 strength
  - 2026-03-23: ACCUMULATION 55.2% buy / 10.4 strength
  - 2026-03-20: DISTRIBUTION 36.2% buy / 27.7 strength
  - 2026-03-19: ACCUMULATION 55.9% buy / 11.7 strength
  - 2026-03-18: DISTRIBUTION 41.9% buy / 16.2 strength
  - AGGREGATE: 45.1% buy / 0.0 strength (NEUTRAL)
- **OI Changes**: MASSIVE 0, LARGE 0, SIGNIFICANT 3, Total $16,839,879
- **News**: BEARISH sentiment (-0.35), material EARNINGS_MISS catalyst.
