"""REL-021b / R-024: preserve broker rejection context across the API."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("operation", ["cancel", "modify"])
@pytest.mark.parametrize("exit_code", [0, 1])
@pytest.mark.parametrize("coded", [False, True])
def test_structured_order_rejection_survives_http_response(monkeypatch, tmp_path, operation, exit_code, coded):
    from scripts.api import server
    from api import subprocess as bridge

    monkeypatch.setattr(server, "is_trusted_local_request", lambda _request: True)
    monkeypatch.setattr(server, "test_mode", False)
    monkeypatch.setattr(server, "_refuse_if_trading_halted", lambda: None)
    calls = []
    detail = {
        "status": "error", "code": "IB_ORDER_REJECTED",
        "message": "Broker rejected the request", "errorCode": 201,
        "orderId": 42, "permId": 420042, "finalStatus": "Inactive",
        "advancedOrderRejectJson": '{"error":"synthetic risk refusal"}',
    }
    if not coded:
        detail.pop("code")

    (tmp_path / "ib_order_manage.py").write_text("# Never executed: process transport is mocked.\n")
    monkeypatch.setattr(bridge, "SCRIPTS_DIR", tmp_path)
    monkeypatch.setattr(bridge, "_acquire_subprocess_slot", AsyncMock(return_value=True))
    monkeypatch.setattr(bridge, "_release_subprocess_slot", lambda: None)
    proc = SimpleNamespace(returncode=exit_code, communicate=AsyncMock(
        return_value=(json.dumps(detail).encode(), b"")))
    monkeypatch.setattr(bridge.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))

    async def fake_run(script, args, **kwargs):
        calls.append((script, args, kwargs))
        return await bridge.run_script(script, args, **kwargs)

    monkeypatch.setattr(server, "_run_ib_script_with_recovery", fake_run)
    response = TestClient(server.app).post(
        f"/orders/{operation}", json={"orderId": 42, "permId": 420042, "outsideRth": False},
    )
    assert response.status_code == 502
    expected = {"code": f"ORDER_{operation.upper()}_FAILED", **detail}
    assert response.json()["detail"] == expected
    assert calls == [("ib_order_manage.py", [operation, "--order-id", "42", "--perm-id", "420042"]
                      + (["--no-outside-rth"] if operation == "modify" else []), {"timeout": 15})]
