"""Next.js DB-read deep-probe watchdog.

2026-07-02 incident: the radon-nextjs PROCESS stayed alive (TCP :3000
liveness green) while every in-process Turso read failed for hours — the
health daemon, the four watchdog buckets, and systemd all reported healthy
because none of them exercises a Turso read THROUGH the Next.js process.
This probe does: GET /api/service-health performs a real DB read and, when
that read fails, returns HTTP 200 with a synthetic `turso-db` error row
(web/app/api/service-health/route.ts catch path) — so the judge inspects
the BODY, not the status code.

Restart policy (mirrors scripts/ib_watchdog.py's ladder):
  - K=3 consecutive wedge cycles (60s timer -> ~3 min to action).
  - A Python-side canary read distinguishes a Node-local wedge from a
    genuine Turso outage: if THIS host's Python also cannot read Turso, a
    Next.js restart fixes nothing — log and stand down.
  - 10 min cooldown between restarts so a persistent upstream failure
    cannot flap the frontend.
  - radon-nextjs has no PartOf/BindsTo — a solo restart does not cascade.

Writes a `nextjs-db-read` service_health row (error on restart, ok on
recovery) so the existing watchdog/footer surfaces see the incident.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

PROBE_URL = "http://127.0.0.1:3000/api/service-health"
PROBE_TIMEOUT_S = 10
STATE_PATH = "/home/radon/radon-cloud/state/nextjs-db-watchdog.json"
CONSECUTIVE_WEDGES_TO_RESTART = 3
RESTART_COOLDOWN_S = 600
VENV_PYTHON = "/home/radon/radon/.venv/bin/python"
APP_ROOT = "/home/radon/radon"
HEALTH_ROW_SERVICE = "nextjs-db-read"

sys.path.insert(0, APP_ROOT)


def log(message: str) -> None:
    print(f"[nextjs-db-watchdog] {message}", flush=True)


def probe_nextjs_db_read() -> tuple[bool, str]:
    """True when the Next.js process can read Turso end-to-end."""
    try:
        with urllib.request.urlopen(PROBE_URL, timeout=PROBE_TIMEOUT_S) as resp:
            if resp.status != 200:
                return False, f"HTTP {resp.status}"
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as err:  # timeout, refused, bad JSON — all failing
        return False, f"probe error: {err}"
    rows = payload.get("services") or []
    for row in rows:
        if row.get("service") == "turso-db" and row.get("state") == "error":
            return False, f"turso-db error row: {row.get('error_summary')}"
    return True, ""


def python_can_read_turso() -> bool:
    """Canary read from a separate process — bounds a hung libsql call."""
    canary = (
        "import sys; sys.path.insert(0, '.')\n"
        "from scripts.db.client import get_db\n"
        "get_db().execute('SELECT 1').fetchone()\n"
    )
    try:
        result = subprocess.run(
            [VENV_PYTHON, "-c", canary],
            cwd=APP_ROOT,
            timeout=8,
            capture_output=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def write_health_row(state: str, detail: str) -> None:
    try:
        from scripts.db.writer import record_service_health

        record_service_health(
            HEALTH_ROW_SERVICE,
            state,
            finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            error={"message": detail} if state == "error" else None,
        )
    except Exception as err:
        log(f"service_health write failed (non-fatal): {err}")


def load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"consecutive_wedges": 0, "last_restart_epoch": 0, "last_state": "ok"}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, STATE_PATH)


def restart_nextjs() -> None:
    subprocess.run(
        ["systemctl", "restart", "radon-nextjs.service"],
        timeout=30,
        check=True,
    )


def main() -> None:
    state = load_state()
    ok, reason = probe_nextjs_db_read()

    if ok:
        if state.get("last_state") == "error":
            log("recovered: Next.js Turso reads healthy again")
        # Heartbeat every clean cycle — transition-only writes leave the row
        # stale between incidents and it surfaces as footer noise (see
        # feedback_service_health_heartbeat: ok every cycle, or rows latch).
        write_health_row("ok", "")
        save_state({"consecutive_wedges": 0,
                    "last_restart_epoch": state.get("last_restart_epoch", 0),
                    "last_state": "ok"})
        return

    if not python_can_read_turso():
        # Upstream Turso outage — a Next.js restart fixes nothing.
        log(f"Turso unreachable from Python too ({reason}); upstream outage, standing down")
        state["consecutive_wedges"] = 0
        state["last_state"] = "error"
        save_state(state)
        return

    state["consecutive_wedges"] = state.get("consecutive_wedges", 0) + 1
    state["last_state"] = "error"
    log(f"Node-local DB wedge {state['consecutive_wedges']}/{CONSECUTIVE_WEDGES_TO_RESTART}: {reason}")

    now = time.time()
    cooled_down = now - state.get("last_restart_epoch", 0) >= RESTART_COOLDOWN_S
    if state["consecutive_wedges"] >= CONSECUTIVE_WEDGES_TO_RESTART and cooled_down:
        log("restarting radon-nextjs.service (in-process Turso wedge, Turso itself healthy)")
        write_health_row("error", f"in-process Turso wedge; restarted radon-nextjs ({reason})")
        restart_nextjs()
        state["consecutive_wedges"] = 0
        state["last_restart_epoch"] = now
    elif state["consecutive_wedges"] >= CONSECUTIVE_WEDGES_TO_RESTART:
        log("wedge persists but restart is inside the 10 min cooldown; standing down")

    save_state(state)


if __name__ == "__main__":
    main()
