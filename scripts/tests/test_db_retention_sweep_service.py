from __future__ import annotations

import db_retention_sweep
from db import retention


def test_partial_policy_failure_is_nonzero_and_unhealthy(monkeypatch, capsys):
    health = []
    monkeypatch.setattr(
        retention,
        "run_retention_sweep_http",
        lambda: ({"kept": 2, "failed": 0}, ["failed"]),
    )
    monkeypatch.setattr(
        db_retention_sweep,
        "write_service_health",
        lambda state, detail, started_at: health.append((state, detail)),
    )

    assert db_retention_sweep.main() == 1
    assert health[0][0] == "error"
    assert '"ok": false' in capsys.readouterr().err.lower()
