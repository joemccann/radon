"""Single service owner for bounded provider renewal and native credential locks."""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

from .client import GatewayError

# Job lifecycle: queued -> running -> complete. One job runs at a time; at
# most one queued job per (mode, interactive) collects later requests, so a
# request is joined to a job that will still refresh its providers, or queued,
# never dropped.
JobStatus = Literal["queued", "running", "complete"]


@dataclass
class _Job:
    job_id: str
    mode: str
    interactive: bool
    providers: list = field(default_factory=list)
    done: set = field(default_factory=set)
    status: JobStatus = "queued"
    started: float = field(default_factory=time.monotonic)
    exit_code: int | None = None
    result: dict | None = None

    def covers(self, mode, interactive, names) -> bool:
        """Will this job still refresh every name, from now on?"""
        if self.status == "complete" or (mode, interactive) != (self.mode, self.interactive):
            return False
        pending = set(self.providers) - self.done
        return set(names) <= pending

    def public(self) -> dict:
        row = {"job_id": self.job_id, "status": self.status}
        if self.status == "complete":
            row.update(exit_code=self.exit_code, result=self.result)
        return row


class RefreshManager:
    def __init__(self, locks):
        self.locks = locks
        self.guard = threading.Lock()
        self.jobs: dict[str, _Job] = {}
        self.active: str | None = None
        self.pending: list[str] = []

    def start(self, payload, *, interactive=True):
        import subscription_tokens as tokens
        if set(payload) - {"mode", "providers", "operation"}:
            raise GatewayError("invalid_request", "Invalid credential renewal fields", 400)
        mode = payload.get("mode", payload.get("operation", "once"))
        names = payload.get("providers", list(tokens.PROVIDERS))
        if mode not in {"once", "seal", "restore", "reauth"} or not isinstance(names, list) or not names or len(names) > 4 or any(name not in tokens.PROVIDERS for name in names):
            raise GatewayError("invalid_request", "Invalid credential renewal request", 400)
        names = list(dict.fromkeys(names))
        with self.guard:
            for key in ([self.active] if self.active else []) + self.pending:
                if self.jobs[key].covers(mode, interactive, names):
                    return self.jobs[key].public()
            for key in self.pending:
                job = self.jobs[key]
                if (job.mode, job.interactive) == (mode, interactive):
                    job.providers.extend(n for n in names if n not in job.providers)
                    return job.public()
            key = uuid.uuid4().hex
            self._prune()
            self.jobs[key] = _Job(key, mode, interactive, names)
            if self.active is None:
                self.active = key
                threading.Thread(target=self._drain, args=(key,), daemon=True).start()
            else:
                self.pending.append(key)
            return self.jobs[key].public()

    def _prune(self):
        now = time.monotonic()
        live = {self.active, *self.pending}
        for key in [k for k, job in self.jobs.items() if job.status == "complete" and (now - job.started >= 3600)]:
            self.jobs.pop(key)
        complete = [k for k, job in self.jobs.items() if k not in live and job.status == "complete"]
        while len(self.jobs) >= 32 and complete:
            self.jobs.pop(complete.pop(0))

    def _drain(self, key):
        while key is not None:
            self._run(self.jobs[key])
            with self.guard:
                key = self.pending.pop(0) if self.pending else None
                self.active = key

    def _run(self, job):
        import subscription_tokens as tokens
        results = []
        codes = []
        states = []
        skipped = False
        with self.guard:
            # Once running, providers are frozen: start() only extends a
            # queued job and joins this one for providers it has not reached.
            job.status = "running"
            names = list(job.providers)
        try:
            for name in names:
                # Only this provider pauses; other rungs remain available.
                with self.locks[name]:
                    runtime = tokens.Runtime(heartbeat=lambda *a, **k: None, **({} if job.interactive else {"login": lambda *a, **k: False}))
                    report = tokens.run(job.mode, [name], runtime)
                with self.guard:
                    job.done.add(name)
                skipped = skipped or bool(report.get("skipped"))
                codes.append(report.get("exit_code", 78))
                rows = [row for row in report.get("providers", []) if isinstance(row, dict)]
                states.extend(row.get("state") for row in rows)
                results.extend({"provider": row.get("provider"), "state": row.get("state")} for row in rows)
            code = 78 if 78 in codes else 1 if any(codes) else 0
            fleet = tokens.fleet_heartbeat_state(names, states, skipped=skipped)
            if code and fleet is None and not skipped:
                fleet = "error"
            if fleet is not None:
                try:
                    tokens.record_heartbeat(tokens.SERVICE_NAME, fleet,
                                            error=None if fleet == "ok" else {"message": "Model credentials require attention"})
                except Exception:
                    code = code or 1
        except Exception:
            code = 78
        with self.guard:
            job.status = "complete"
            job.exit_code = code
            # A lock-skipped pass refreshed nothing; the lock holder reports.
            job.result = {"providers": results, **({"skipped": True} if skipped else {})}

    def status(self, payload):
        if set(payload) != {"job_id"} or not isinstance(payload.get("job_id"), str):
            raise GatewayError("invalid_request", "Invalid renewal job", 400)
        with self.guard:
            job = self.jobs.get(payload["job_id"])
            if job is None:
                raise GatewayError("not_found", "Renewal job is unavailable", 404)
            return job.public()

    def request_refresh(self, name):
        # An expired access token schedules bounded renewal; inference does not
        # wait for a potentially interactive native probe while holding a slot.
        self.start({"mode": "once", "providers": [name]}, interactive=False)
