"""Regression fixtures for source identity, exact counts and fail-closed schemas."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ai_cycle.collect import environment, windows
from scripts.ai_cycle.collectors import (
    SourceError,
    Transport,
    archive_raw,
    parse_aa,
    parse_disclosures,
    parse_eia,
    parse_gpu,
    parse_openrouter,
    parse_sec,
    parse_vercel,
    public_url,
)

HASH = "a" * 64
FETCHED = "2026-09-07T12:00:00+00:00"


def test_archive_raw_repairs_corrupt_content_addressed_file(tmp_path):
    raw = b'{"rows": ["complete"]}'
    digest = hashlib.sha256(raw).hexdigest()
    path = tmp_path / f"{digest}.json"
    path.write_bytes(raw[:8])

    assert archive_raw(tmp_path, raw) == digest
    assert path.read_bytes() == raw


def test_archive_raw_never_publishes_partial_file_when_replace_interrupts(tmp_path, monkeypatch):
    raw = b'{"rows": ["complete"]}'
    digest = hashlib.sha256(raw).hexdigest()

    def interrupted_replace(source, destination):
        raise OSError("interrupted before replacement")

    monkeypatch.setattr(os, "replace", interrupted_replace)
    with pytest.raises(OSError, match="interrupted"):
        archive_raw(tmp_path, raw)

    assert not (tmp_path / f"{digest}.json").exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_archive_raw_leaves_valid_content_addressed_file_untouched(tmp_path, monkeypatch):
    raw = b'{"rows": ["complete"]}'
    digest = hashlib.sha256(raw).hexdigest()
    path = tmp_path / f"{digest}.json"
    path.write_bytes(raw)

    monkeypatch.setattr(os, "replace", lambda *_: pytest.fail("valid artifact was replaced"))
    assert archive_raw(tmp_path, raw) == digest
    assert path.read_bytes() == raw


def test_openrouter_exact_int_and_unknown_paid_other():
    rows = parse_openrouter(
        {
            "data": [
                {"date": "2026-09-06", "model_permaslug": "other", "total_tokens": "9007199254740993"},
                {"date": "2026-09-07", "model_permaslug": "x", "total_tokens": "22"},
            ]
        },
        HASH,
        FETCHED,
        "2026-09-01",
        "2026-09-07",
    )
    assert len(rows) == 1
    assert rows[0]["value"] == 9007199254740993
    assert rows[0]["metadata"]["paid_classification"] == "unknown"
    assert rows[0]["published_at"] is None


@pytest.mark.parametrize("value", ["1.5", "-1", True, "NaN"])
def test_openrouter_rejects_invalid_tokens(value):
    with pytest.raises(SourceError):
        parse_openrouter(
            {"data": [{"date": "2026-09-06", "model_permaslug": "x", "total_tokens": value}]},
            HASH,
            FETCHED,
            "2026-09-01",
            "2026-09-06",
        )


def test_vercel_shares_must_reconcile():
    payload = {
        "dataset": "models",
        "modality": "text",
        "rows": [{"date": "2026-09-06", "metric": "tokens", "name": "Other", "share_percent": 90}],
    }
    with pytest.raises(SourceError):
        parse_vercel(payload, HASH, FETCHED, "2026-09-06", "2026-09-06")
    payload["rows"][0]["share_percent"] = 100
    row = parse_vercel(payload, HASH, FETCHED, "2026-09-06", "2026-09-06")[0]
    assert row["unit"] == "%"
    assert "not market volume" in row["metadata"]["definition"]


def test_gpu_missing_bundle_is_ineligible_not_assumed_single_gpu():
    row = parse_gpu(
        {
            "date": "2026-09-06",
            "offers": [
                {
                    "provider": "lambda",
                    "gpu": "h100-sxm",
                    "vram_gb": 80,
                    "kind": "on-demand",
                    "usd_hr": 3.99,
                    "source_url": "https://lambda.ai/pricing",
                }
            ],
        },
        HASH,
        FETCHED,
    )[0]
    assert row["metadata"]["gpu_count"] is None
    assert row["metadata"]["cohort_eligible"] is False


def test_aa_missing_expensive_member_suppresses_entire_basket():
    payload = {
        "data": [
            {
                "id": "stable-cheap-id",
                "slug": "cheap",
                "name": "Cheap frontier",
                "model_creator": {"id": "creator-id"},
                "pricing": {"price_1m_input_tokens": 1, "price_1m_output_tokens": 2},
            }
        ]
    }
    with pytest.raises(SourceError):
        parse_aa(payload, HASH, FETCHED, ["cheap", "expensive"])
    row = parse_aa(payload, HASH, FETCHED, ["cheap"])[0]
    assert row["value"] == 3
    assert row["series_id"] == "stable-cheap-id"
    assert row["metadata"]["required_members"] == ["stable-cheap-id"]
    assert row["metadata"]["model_slug"] == "cheap"
    assert row["methodology_version"] == "aa-frontier-id-v1"


def test_aa_slug_rename_preserves_series_and_cohort_identity():
    def payload(slug):
        return {
            "data": [
                {
                    "id": "stable-model-id",
                    "slug": slug,
                    "name": "Frontier model",
                    "model_creator": {"id": "creator-id"},
                    "pricing": {"price_1m_input_tokens": 2, "price_1m_output_tokens": 8},
                }
            ]
        }

    before = parse_aa(payload("old-slug"), HASH, FETCHED, ["old-slug"])[0]
    after = parse_aa(payload("new-slug"), HASH, FETCHED, ["new-slug"])[0]

    assert before["series_id"] == after["series_id"] == "stable-model-id"
    assert before["cohort_version"] == after["cohort_version"]


def test_sec_keeps_ytd_period_and_reviewed_tag_mapping():
    payload = {
        "facts": {
            "us-gaap": {
                "NetCashProvidedByUsedInOperatingActivities": {
                    "units": {
                        "USD": [
                            {
                                "start": "2026-01-01",
                                "end": "2026-06-30",
                                "val": 100,
                                "filed": "2026-08-01",
                                "form": "10-Q",
                                "accn": "123",
                                "fy": 2026,
                            }
                        ]
                    }
                }
            }
        }
    }
    row = parse_sec(payload, HASH, FETCHED, "MSFT", "2026-01-01", "2026-09-06")[0]
    assert row["period_start"] == "2026-01-01"
    assert row["metadata"]["duration"] == "ytd"
    assert row["metadata"]["verified_statement"] is True
    assert "not individually audited" in row["metadata"]["statement_check"]
    assert row["published_at"] == "2026-08-01T23:59:59Z"


def test_eia_dom_and_unit_fail_closed():
    payload = {
        "response": {
            "total": 1,
            "data": [
                {
                    "period": "2026-09-06T04",
                    "parent": "PJM",
                    "subba": "DOM",
                    "value": 123,
                    "value-units": "megawatthours",
                }
            ],
        }
    }
    row = parse_eia(payload, HASH, FETCHED)[0]
    assert row["unit"] == "MWh/hour"
    assert row["period_start"].startswith("2026-09-06T03")
    assert row["period_end"].startswith("2026-09-06T04")
    assert row["methodology_version"] == "eia-hour-ending-v2"
    assert row["metadata"]["timestamp_convention"] == "hour-ending UTC"
    payload["response"]["data"][0]["subba"] = "AEP"
    with pytest.raises(SourceError):
        parse_eia(payload, HASH, FETCHED)


def test_disclosure_cannot_import_unverified_memo_values():
    with pytest.raises(SourceError):
        parse_disclosures({"observations": [{"verified": False}]}, HASH, FETCHED)


def test_public_url_removes_secrets_and_rejects_userinfo():
    assert public_url("https://api.eia.gov/path?api_key=secret#secret") == "https://api.eia.gov/path"
    with pytest.raises(SourceError):
        public_url("https://secret@example.com/path")


def test_windows_bound_and_no_overlap():
    assert list(windows("2026-01-01", "2026-02-01")) == [("2026-01-01", "2026-01-28"), ("2026-01-29", "2026-02-01")]


def test_environment_loads_only_provider_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    path = tmp_path / ".env"
    path.write_text("EIA_API_KEY=selected\nPUSHOVER_TOKEN=never\n")
    env = environment(path)
    assert env["EIA_API_KEY"] == "selected"
    assert "PUSHOVER_TOKEN" not in env


def test_environment_prefers_profile_credential_store(tmp_path, monkeypatch):
    from scripts.secret_store import SecretStore

    monkeypatch.setenv("RADON_SECRET_STORE_PATH", str(tmp_path / "secrets.db"))
    monkeypatch.setenv("RADON_SECRET_STORE_KEY_FILE", str(tmp_path / "secret.key"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "stale-env-key")
    store = SecretStore()
    store.set_secrets(
        {
            "OPENROUTER_API_KEY": "profile-key",
            "RADON_AI_CYCLE_AA_BASKET": "openai/gpt-a,anthropic/model-b",
        },
        actor="test",
    )

    env = environment()

    assert env["OPENROUTER_API_KEY"] == "profile-key"
    assert env["RADON_AI_CYCLE_AA_BASKET"] == "openai/gpt-a,anthropic/model-b"


def test_environment_resolves_secret_store_from_package_invocation(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from scripts.ai_cycle.collect import environment; environment()",
        ],
        cwd=repo,
        env={
            **os.environ,
            "PYTHONPATH": str(repo),
            "RADON_SECRET_STORE_PATH": str(tmp_path / "secrets.db"),
            "RADON_SECRET_STORE_KEY_FILE": str(tmp_path / "secret.key"),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_transport_hashes_raw_and_sanitizes_errors(tmp_path):
    class Response:
        status_code = 200

        def iter_content(self, size):
            yield b'{"rows": []}'

        def close(self):
            pass

    class Session:
        def request(self, *args, **kwargs):
            return Response()

    t = Transport(tmp_path, session=Session(), max_requests=1)
    _, digest, _ = t.fetch("https://example.com")
    assert (tmp_path / (digest + ".json")).read_bytes() == b'{"rows": []}'
    with pytest.raises(SourceError, match="budget"):
        t.fetch("https://example.com")


def test_sec_preserves_negative_operating_cash_flow():
    payload = {
        "facts": {
            "us-gaap": {
                "NetCashProvidedByUsedInOperatingActivities": {
                    "units": {
                        "USD": [
                            {
                                "start": "2026-01-01",
                                "end": "2026-06-30",
                                "val": -100.5,
                                "filed": "2026-08-01",
                                "form": "10-Q",
                                "accn": "123",
                            }
                        ]
                    }
                }
            }
        }
    }
    assert parse_sec(payload, HASH, FETCHED, "MSFT", "2026-01-01", "2026-09-06")[0]["value"] == -100.5


def test_apps_same_host_and_daily_window():
    from scripts.ai_cycle.collectors import parse_apps

    rows = parse_apps(
        {"data": [{"app_id": 1, "app_name": "A", "rank": 1, "total_tokens": "9007199254740993", "total_requests": 12}]},
        HASH,
        FETCHED,
        "2026-09-06",
    )
    assert rows[0]["lineage_group"] == "openrouter"
    assert rows[0]["value"] == 9007199254740993
    assert rows[0]["metadata"]["truncated"] is True


def test_vast_alternative_machine_offers_are_deduplicated():
    from scripts.ai_cycle.collectors import parse_vast

    offers = [
        {
            "id": i,
            "machine_id": 10,
            "verification": "verified",
            "geolocation": "Virginia, US",
            "is_bid": False,
            "rentable": True,
            "rented": False,
            "reliability": 0.999,
            "gpu_name": "H100_SXM",
            "num_gpus": 2,
            "dph_total": 8,
        }
        for i in (1, 2)
    ]
    rows = parse_vast({"offers": offers}, HASH, FETCHED)
    assert [r["value"] for r in rows] == [2, 1, 2, 4]


def test_verify_never_writes_service_health(monkeypatch):
    from scripts.ai_cycle import collect

    monkeypatch.setattr(collect, "_main", lambda flags: 0)
    import scripts.db.hrana_http as db

    monkeypatch.setattr(db, "write_service_health_http", lambda *a, **k: pytest.fail("No production write allowed"))
    assert collect.main(["--verify"]) == 0
    assert collect.main(["--record", "--database", "/tmp/isolated.db"]) == 0


def test_production_heartbeat_on_failure(monkeypatch):
    from scripts.ai_cycle import collect

    monkeypatch.delenv("RADON_AI_CYCLE_DB_PATH", raising=False)
    monkeypatch.setattr(collect, "_main", lambda flags: 1)
    calls = []
    import scripts.db.hrana_http as db

    monkeypatch.setattr(db, "write_service_health_http", lambda *a, **k: calls.append((a, k)))
    assert collect.main(["--record"]) == 1
    assert calls[0][0] == ("ai-cycle", "error")
    assert calls[0][1]["timeout"] == 8


@pytest.mark.parametrize(
    "source",
    ["openrouter", "artificial-analysis", "eia", "vast", "sec", "portkey", "noaa", "lambda", "issuer-disclosures"],
)
def test_missing_entitlements_never_calls_provider(source, tmp_path):
    from scripts.ai_cycle.collectors import collect_source

    transport = Transport(tmp_path)
    with pytest.raises(SourceError):
        collect_source(source, transport, "2026-09-01", "2026-09-06", env={})
    assert transport.requests == 0


def test_cli_record_checkpoint_and_cache_only_store(tmp_path, monkeypatch, capsys):
    from scripts.ai_cycle import collect
    from scripts.ai_cycle.store import ObservationStore

    calls = []

    def collector(source, transport, start, end, **kwargs):
        calls.append((source, start, end))
        return parse_openrouter(
            {"data": [{"date": end, "model_permaslug": "other", "total_tokens": "42"}]}, HASH, FETCHED, start, end
        )

    monkeypatch.setattr(collect, "collect_source", collector)
    db = tmp_path / "local.db"
    checkpoint = tmp_path / "checkpoint.json"
    args = [
        "--record",
        "--database",
        str(db),
        "--sources",
        "openrouter",
        "--start",
        "2026-09-01",
        "--end",
        "2026-09-06",
        "--checkpoint",
        str(checkpoint),
        "--archive",
        str(tmp_path / "raw"),
    ]
    assert collect.main(args) == 0
    assert len(ObservationStore(db).read_observations()) == 1
    assert json.loads(capsys.readouterr().out)["observations"] == 1
    assert collect.main(args) == 0
    assert len(calls) == 1


def test_cli_transport_error_sanitized_and_failure_exit(tmp_path, monkeypatch, capsys):
    from scripts.ai_cycle import collect

    def fail(*args, **kwargs):
        raise SourceError("Publisher transport failed")

    monkeypatch.setattr(collect, "collect_source", fail)
    assert (
        collect.main(
            [
                "--verify",
                "--sources",
                "vercel",
                "--start",
                "2026-09-01",
                "--end",
                "2026-09-06",
                "--archive",
                str(tmp_path),
            ]
        )
        == 1
    )
    result = json.loads(capsys.readouterr().out)
    assert result["sources"][0]["status"] == "error"


def test_cli_schema_exception_hides_provider_detail(tmp_path, monkeypatch, capsys):
    from scripts.ai_cycle import collect

    def fail(*args, **kwargs):
        raise KeyError("api_key=private")

    monkeypatch.setattr(collect, "collect_source", fail)
    assert collect.main(["--verify", "--sources", "vercel", "--end", "2026-09-06", "--archive", str(tmp_path)]) == 1
    assert "private" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "extra",
    [
        ["--max-requests", "401"],
        ["--backfill"],
        ["--sources", "unknown"],
        ["--start", "2026-09-07"],
        ["--start", "2020-01-01"],
    ],
)
def test_cli_rejects_unbounded_or_invalid_windows(extra):
    from scripts.ai_cycle.collect import main

    with pytest.raises(SystemExit):
        main(["--verify", "--end", "2026-09-06", *extra])


def test_transport_http_rejection_never_archives_body(tmp_path):
    class Response:
        status_code = 401

        def close(self):
            pass

    class Session:
        def request(self, *a, **kw):
            return Response()

    with pytest.raises(SourceError, match="HTTP 401"):
        Transport(tmp_path, session=Session()).fetch("https://example.com")
    assert not list(tmp_path.iterdir())


def test_openrouter_corrupt_quota_ledger_fails_closed(tmp_path):
    ledger = tmp_path / "quota.json"
    ledger.write_text("broken")
    transport = Transport(tmp_path / "raw", budget_path=ledger)
    with pytest.raises(SourceError, match="quota ledger"):
        transport.fetch("https://openrouter.ai/api/v1/datasets/rankings-daily")
    assert transport.requests == 0


def test_vast_nonus_or_unverified_offer_is_rejected():
    from scripts.ai_cycle.collectors import parse_vast

    with pytest.raises(SourceError, match="fixed cohort"):
        parse_vast({"offers": [{"id": 1, "verification": "unverified"}]}, HASH, FETCHED)


def test_disclosure_guidance_keeps_estimated_class():
    item = dict(
        verified=True,
        verified_by="Reviewed",
        source_excerpt="Revenue guidance",
        definition="Consolidated midpoint, not DC revenue",
        indicator_id="H1",
        series_id="NVDA.total-guide",
        value=108,
        unit="USD",
        period_start="2026-07-27",
        period_end="2026-10-25",
        published_at="2026-08-26T23:59:59Z",
        source_url="https://investor.nvidia.com/news",
        measurement="estimated",
    )
    row = parse_disclosures({"observations": [item]}, HASH, FETCHED)[0]
    assert row["measurement"] == "estimated"
    assert row["period_end"] > FETCHED[:10]


def test_openrouter_quota_ledger_remains_valid_across_calls(tmp_path, monkeypatch):
    import scripts.ai_cycle.collectors as collectors

    monkeypatch.setattr(collectors.time, "sleep", lambda duration: None)
    ledger = tmp_path / "quota.json"
    transport = Transport(tmp_path / "raw", budget_path=ledger)
    transport._or_budget()
    transport._or_budget()
    assert json.loads(ledger.read_text())["used"] == 2


def test_eia_form930_midnight_is_previous_operating_hour():
    # Official Form EIA-930 instructions p.3: report hour-ending UTC timestamps.
    # https://www.eia.gov/survey/form/eia_930/instructions.pdf
    payload = {
        "response": {
            "total": 1,
            "data": [
                {
                    "period": "2026-09-07T00",
                    "parent": "PJM",
                    "subba": "DOM",
                    "value": "123",
                    "value-units": "megawatthours",
                }
            ],
        }
    }
    row = parse_eia(payload, HASH, "2026-09-07T00:00:00+00:00")[0]
    assert row["period_start"] == "2026-09-06T23:00:00+00:00"
    assert row["period_end"] == "2026-09-07T00:00:00+00:00"
    assert row["value"] == 123
    assert row["metadata"]["methodology_source_url"] == "https://www.eia.gov/survey/form/eia_930/instructions.pdf"


def test_eia_request_covers_completed_operating_days_with_hour_ending_bounds():
    from scripts.ai_cycle.collectors import collect_source

    calls = []

    class FakeTransport:
        def fetch(self, url, **kwargs):
            calls.append(kwargs["params"])
            return {"response": {"total": 0, "data": []}}, HASH, FETCHED

    assert collect_source("eia", FakeTransport(), "2026-09-01", "2026-09-06", env={"EIA_API_KEY": "test"}) == []
    assert calls[0]["frequency"] == "hourly"
    assert calls[0]["start"] == "2026-09-01T01"
    assert calls[0]["end"] == "2026-09-07T00"


def test_daily_collection_preserves_reviewed_disclosure_status_without_import(tmp_path, capsys):
    from scripts.ai_cycle.collect import main
    from scripts.ai_cycle.store import ObservationStore

    path = tmp_path / "reviewed.sqlite"
    store = ObservationStore(path)
    store.initialize()
    store.record_source_status(
        {
            "source_id": "issuer-disclosures",
            "status": "available",
            "reason": "Reviewed issuer release",
            "checked_at": FETCHED,
        }
    )
    before = store.read_source_statuses()
    assert main(["--record", "--database", str(path), "--sources", "issuer-disclosures", "--end", "2026-09-06"]) == 0
    assert store.read_source_statuses() == before
    result = json.loads(capsys.readouterr().out)
    assert result["sources"] == []
    assert result["requests"] == 0


def test_no_disclosure_import_does_not_invent_initial_source_check(tmp_path, capsys):
    from scripts.ai_cycle.collect import main
    from scripts.ai_cycle.store import ObservationStore

    path = tmp_path / "empty.sqlite"
    assert main(["--record", "--database", str(path), "--sources", "issuer-disclosures", "--end", "2026-09-06"]) == 0
    assert ObservationStore(path).read_source_statuses() == []
    assert json.loads(capsys.readouterr().out)["observations"] == 0


def test_explicit_malformed_disclosure_import_records_failure(tmp_path, capsys):
    from scripts.ai_cycle.collect import main
    from scripts.ai_cycle.store import ObservationStore

    path = tmp_path / "reviewed.sqlite"
    store = ObservationStore(path)
    store.initialize()
    store.record_source_status(
        {
            "source_id": "issuer-disclosures",
            "status": "available",
            "reason": "Reviewed issuer release",
            "checked_at": FETCHED,
        }
    )
    artifact = tmp_path / "malformed.json"
    artifact.write_text("invalid json")
    assert (
        main(
            [
                "--record",
                "--database",
                str(path),
                "--sources",
                "issuer-disclosures",
                "--end",
                "2026-09-06",
                "--import-disclosures",
                str(artifact),
                "--archive",
                str(tmp_path / "raw"),
            ]
        )
        == 1
    )
    status = store.read_source_statuses()[0]
    assert status["source_id"] == "issuer-disclosures"
    assert status["status"] == "error"
    assert json.loads(capsys.readouterr().out)["sources"][0]["status"] == "error"
