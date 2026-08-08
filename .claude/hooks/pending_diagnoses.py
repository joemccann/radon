"""SessionStart hook: surface incident diagnoses that still need attention.

A diagnosis needs attention when <id>.diagnosis.md exists in the responder
mirror and its incident JSON is still status=open (or missing). Prints hook
JSON with a user-visible systemMessage plus model context; prints nothing
when there is nothing to address.
"""

import json
from pathlib import Path

MIRROR = Path(__file__).resolve().parents[2] / "data" / "incidents_remote"


def incident_status(incident_id: str) -> str:
    path = MIRROR / f"incident-{incident_id}.json"
    try:
        return json.loads(path.read_text()).get("status", "missing")
    except (OSError, ValueError):
        return "missing"


def pending_diagnoses() -> list[dict]:
    pending = []
    for diagnosis in sorted(MIRROR.glob("*.diagnosis.md")):
        incident_id = diagnosis.name.removesuffix(".diagnosis.md")
        status = incident_status(incident_id)
        if status != "resolved":
            pending.append({"id": incident_id, "status": status,
                            "path": str(diagnosis)})
    return pending


def main() -> None:
    pending = pending_diagnoses()
    if not pending:
        return
    ids = ", ".join(p["id"] for p in pending)
    paths = "\n".join(f"- {p['path']} (incident status: {p['status']})"
                      for p in pending)
    print(json.dumps({
        "systemMessage": (
            f"⚠️ {len(pending)} incident diagnosis(es) need attention: {ids}"
        ),
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": (
                "Unaddressed incident diagnoses from the VPS incident "
                "responder (incident still open):\n" + paths +
                "\nOffer to review and address them with the user."
            ),
        },
    }))


if __name__ == "__main__":
    main()
