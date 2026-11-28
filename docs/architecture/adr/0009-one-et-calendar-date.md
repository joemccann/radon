# 0009. One ET calendar date

## Status

Accepted. 2026-11-28.

## Context

Earnings dates, catalysts, and informed flow each defined `_et_today(now)`
as the America/New_York calendar day of an instant, with a UTC fallback.
`is_trading_day_et` answers a different question: whether that day is a
session.

## Decision

`market_calendar.et_calendar_date` owns the conversion. The three jobs
bind it as `_et_today`.

`scripts/tests/test_et_calendar_date_single_source.py` rejects a new
`def _et_today(now: datetime) -> date`.

## Consequences

A change to which instant counts as which ET day happens in the calendar
module. Session membership is still `is_trading_day_et`.
