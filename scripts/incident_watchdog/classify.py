"""Pure classification: probe findings -> incidents mapped to runbook cases.

No I/O, no clock reads — the cycle passes ``now`` so every rule is testable
with a pinned clock. ``unknown`` findings never classify (a probe that could
not observe is not evidence of an outage).
"""

from __future__ import annotations

from datetime import datetime

RUNBOOK = "docs/incident-runbook.md"


def _incident(case_id: str, severity: str, title: str, fingerprint: str,
              evidence: dict) -> dict:
    return {
        "case_id": case_id,
        "severity": severity,
        "title": title,
        "fingerprint": fingerprint,
        "evidence": evidence,
    }


def _classify_corrupt_build(findings: dict) -> dict | None:
    liveness = findings.get("nextjs_liveness", {})
    deploy = findings.get("deploy", {})
    ci = deploy.get("ci") or {}
    deploy_settled = ci.get("status") in (None, "completed")

    if liveness.get("state") == "down" and liveness.get("http_status") == 500:
        return _incident(
            "cancelled-deploy-corrupt-next-build", "P1",
            "Public route /sign-in returns 500 — app cannot render any page",
            "corrupt-next-build",
            {
                "liveness": liveness,
                "last_ci_run": ci or None,
                "green_marker": deploy.get("green_marker"),
                "head": deploy.get("head"),
            },
        )

    marker = deploy.get("green_marker")
    head = deploy.get("head")
    if marker and head and marker != head and deploy_settled:
        return _incident(
            "cancelled-deploy-corrupt-next-build", "P2",
            "Git HEAD does not match the last green-deploy marker",
            "deploy-marker-mismatch",
            {"green_marker": marker, "head": head, "last_ci_run": ci or None},
        )
    return None


def _classify_turso_wedge(findings: dict) -> dict | None:
    db = findings.get("nextjs_db", {})
    liveness = findings.get("nextjs_liveness", {})
    if db.get("state") == "up" and db.get("synthetic_turso_row") \
            and liveness.get("state") == "up":
        return _incident(
            "turso-destroy-storm", "P1",
            "Next.js Turso reads failing while the process is up",
            "turso-destroy-storm",
            {
                "warning": db.get("warning"),
                "next_diagnostic": (
                    "Run the Python Turso canary from the same host in the same "
                    "minute; canary succeeds => Node-local wedge (restart "
                    "radon-nextjs helps), canary fails => upstream Turso "
                    "(do NOT restart-flap)."
                ),
            },
        )
    return None


def _classify_stale_freshness(findings: dict) -> dict | None:
    fresh = findings.get("freshness", {})
    if fresh.get("state") == "up" and fresh.get("all_fresh") is False:
        stale = fresh.get("stale_checks") or []
        return _incident(
            "stale-market-data-freshness", "P2",
            f"Market-data freshness failing during RTH: {', '.join(stale)}",
            "stale-freshness:" + ",".join(stale),
            {"stale_checks": stale, "database_ok": fresh.get("database_ok")},
        )
    return None


def _classify_degraded_rows(findings: dict) -> dict | None:
    db = findings.get("nextjs_db", {})
    failing = db.get("failing") or []
    if db.get("state") == "up" and not db.get("synthetic_turso_row") and failing:
        names = sorted(str(row.get("service")) for row in failing)
        return _incident(
            "service-health-degraded", "P2",
            f"{len(names)} service_health row(s) degraded: {', '.join(names)}",
            "service-health-degraded:" + ",".join(names),
            {"failing": failing},
        )
    return None


def _classify_service_down(findings: dict) -> dict | None:
    if findings.get("api_lite", {}).get("state") == "down":
        return _incident(
            "service-down", "P1",
            "FastAPI /health/lite unreachable (connection refused)",
            "service-down:fastapi",
            {"health_status": findings.get("health_status")},
        )
    return None


_RULES = (
    _classify_corrupt_build,
    _classify_turso_wedge,
    _classify_stale_freshness,
    _classify_degraded_rows,
    _classify_service_down,
)


def classify(findings: dict, now: datetime) -> list[dict]:
    incidents = []
    for rule in _RULES:
        incident = rule(findings)
        if incident:
            incident["classified_at"] = now.isoformat()
            incidents.append(incident)
    return incidents
