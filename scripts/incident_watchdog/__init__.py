"""Incident watchdog — endpoint/deploy prober that emits incident JSON.

Complements scripts/watchdog (service_health-row driven, Pushover alert-only)
by owning exactly the gaps it does not cover: probing live HTTP endpoints,
judging response BODIES (not status codes), comparing deploy markers against
Git HEAD, and persisting structured incident artifacts to data/incidents/
that the /incident slash command and docs/incident-runbook.md consume.

Stdlib-only by design (same isolation rationale as scripts/health_service):
it must stay probe-able when the trading stack itself is the thing on fire.
"""
