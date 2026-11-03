"""Single service owner for bounded provider renewal and native credential locks."""
from __future__ import annotations
import threading
import time
import uuid
from .client import GatewayError


class RefreshManager:
    def __init__(self, locks):
        self.locks = locks
        self.guard = threading.Lock()
        self.jobs = {}
        self.active = None

    def start(self, payload, *, interactive=True):
        import subscription_tokens as tokens
        if set(payload) - {"mode", "providers", "operation"}:
            raise GatewayError("invalid_request", "Invalid credential renewal fields", 400)
        mode = payload.get("mode", payload.get("operation", "once"))
        names = payload.get("providers", list(tokens.PROVIDERS))
        if mode not in {"once", "seal", "restore", "reauth"} or not isinstance(names, list) or not names or len(names) > 4 or any(name not in tokens.PROVIDERS for name in names):
            raise GatewayError("invalid_request", "Invalid credential renewal request", 400)
        with self.guard:
            if self.active:
                return {"job_id": self.active, "status": self.jobs[self.active]["status"]}
            key = uuid.uuid4().hex
            self.jobs = {k: v for k, v in self.jobs.items() if time.monotonic() - v["started"] < 3600}
            while len(self.jobs) >= 32:
                self.jobs.pop(next(iter(self.jobs)))
            self.jobs[key] = {"job_id": key, "status": "queued", "started": time.monotonic()}
            self.active = key
            threading.Thread(target=self._run, args=(key, mode, list(dict.fromkeys(names)), interactive), daemon=True).start()
            return {"job_id": key, "status": "queued"}

    def _run(self, key, mode, names, interactive):
        import subscription_tokens as tokens
        results = []
        codes = []
        with self.guard:
            self.jobs[key]["status"] = "running"
        try:
            for name in names:
                # Only this provider pauses; other rungs remain available.
                with self.locks[name]:
                    runtime = tokens.Runtime(heartbeat=lambda *a, **k: None, **({} if interactive else {"login": lambda *a, **k: False}))
                    report = tokens.run(mode, [name], runtime)
                codes.append(report.get("exit_code", 78))
                results.extend({"provider": row.get("provider"), "state": row.get("state")}
                               for row in report.get("providers", []) if isinstance(row, dict))
            code = 78 if 78 in codes else 1 if any(codes) else 0
            try:
                tokens.record_heartbeat(tokens.SERVICE_NAME, "ok" if code == 0 else "error",
                                        error=None if code == 0 else {"message": "Model credentials require attention"})
            except Exception:
                code = code or 1
        except Exception:
            code = 78
        with self.guard:
            self.jobs[key].update(status="complete", exit_code=code, result={"providers": results})
            self.active = None

    def status(self, payload):
        if set(payload) != {"job_id"} or not isinstance(payload.get("job_id"), str):
            raise GatewayError("invalid_request", "Invalid renewal job", 400)
        with self.guard:
            row = self.jobs.get(payload["job_id"])
            if row is None:
                raise GatewayError("not_found", "Renewal job is unavailable", 404)
            return {key: value for key, value in row.items() if key != "started"}

    def request_refresh(self, name):
        # An expired access token schedules bounded renewal; inference does not
        # wait for a potentially interactive native probe while holding a slot.
        self.start({"mode": "once", "providers": [name]}, interactive=False)
