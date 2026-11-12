"""A transient Turso 502 on the history write must not fail the oneshot.

Production topology: SHY/HYG/VIX fetches succeed, a new session is ready
to upsert, and libsql raises a bare ValueError for HTTP 502. The rehydrate
path already treats that blip as non-fatal. The history write must keep
the error row, must not retry the uncertain insert, and must exit 0.
A SQLite statement error stays fatal.
"""
import pytest

import fetch_credit_vix as mod

UPSTREAM_502 = (
    "Hrana: `api error: `status=502 Bad Gateway, "
    'body={"error":"connect to upstream failed"}`'
)
STATEMENT = (
    'Hrana: `stream error: `Error { message: "SQLite error: '
    'no such table: credit_vix_history" }`'
)
CACHED = {
    "date": "2026-09-28",
    "shy_close": 80.0,
    "hyg_close": 75.0,
    "vix_close": 18.0,
    "spread": 5.0,
}


def _wire(monkeypatch, tmp_path, upsert_error):
    calls = []
    monkeypatch.setattr(mod, "CREDIT_VIX_JSON", tmp_path / "credit_vix.json")
    monkeypatch.setattr(mod, "load_cached_series", lambda **_kw: [dict(CACHED)])

    def upsert(rows, recorded_at=None):
        calls.append(("rows", len(rows), recorded_at))
        raise upsert_error

    monkeypatch.setattr(mod.writer, "upsert_credit_vix_rows", upsert)
    monkeypatch.setattr(
        mod.writer,
        "upsert_scan_snapshot",
        lambda *_a, **_k: calls.append(("snapshot",)),
    )
    monkeypatch.setattr(
        mod.writer,
        "record_service_health",
        lambda service, state, **kwargs: calls.append(
            ("health", service, state, kwargs.get("error"))
        ),
    )
    monkeypatch.setattr(
        mod,
        "fetch_equity_closes",
        lambda *_a, **_k: (
            {"SHY": {"2026-09-30": 81.0}, "HYG": {"2026-09-30": 76.0}},
            "ib",
            {"SHY": "ib", "HYG": "ib"},
        ),
    )
    monkeypatch.setattr(
        mod, "fetch_vix_closes", lambda **_k: ({"2026-09-30": 19.0}, "ib")
    )
    return calls


def test_hrana_502_on_history_upsert_exits_zero(monkeypatch, tmp_path, capsys):
    calls = _wire(monkeypatch, tmp_path, ValueError(UPSTREAM_502))

    assert mod.main([]) == 0

    err = capsys.readouterr().err
    assert "cycle transport non-fatal: ValueError" in err
    assert "connect to upstream failed" not in err
    assert [item for item in calls if item[0] == "rows"] == [
        ("rows", 1, calls[0][2])
    ]
    health = [item for item in calls if item[0] == "health"]
    assert health[-1][1:] == (
        "credit-vix",
        "error",
        health[-1][3],
    )
    assert health[-1][3]["class"] == "cycle_failed"
    assert "status=502" in health[-1][3]["message"]
    row_calls = [item for item in calls if item[0] == "rows"]
    assert len(row_calls) == 1
    assert row_calls[0][1] == 1
    assert not any(item[0] == "snapshot" for item in calls)
    assert not (tmp_path / "credit_vix.json").exists()


def test_sqlite_statement_error_on_upsert_still_fails(monkeypatch, tmp_path):
    calls = _wire(monkeypatch, tmp_path, ValueError(STATEMENT))

    with pytest.raises(ValueError, match="no such table"):
        mod.main([])

    assert len([item for item in calls if item[0] == "rows"]) == 1
    assert not any(item[0] == "snapshot" for item in calls)
