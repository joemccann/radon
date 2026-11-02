"""DeepSec operator-only re-verify: prompt contract, Next filter, seeded close.

The nightly loop used to rebuild Next from every carried operator-only
finding in last-audited.json. Nothing re-checked evidence, so a fixed
item (DS-2026-09-20-03, PR #689) stayed in the operator list. These
tests fail on that prompt/helper/ledger shape and pin the replacement.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PROMPT = REPO / ".claude" / "runner-prompts" / "security-deepsec.md"
LEDGER = REPO / "docs" / "security-deepsec-closed.json"
HELPER = REPO / "scripts" / "nightly_deepsec_queue.py"
SEEDED_ID = "DS-2026-09-20-03"
SEEDED_PR = 689
SEEDED_COMMIT = "e5c4e627"
SEEDED_PATH = "cloud/scripts/setup-vps.sh"
SEEDED_LINES = "39-47"


def _prompt() -> str:
    return PROMPT.read_text(encoding="utf-8")


class TestPromptRequiresReverify:
    def test_prompt_requires_the_reverify_step_and_three_states(self):
        text = _prompt()
        assert "re-verify" in text
        for state in ("`open`", "`closed`", "`unverifiable`"):
            assert state in text, state
        assert "closed tonight" in text
        assert "scripts/nightly_deepsec_queue.py" in text
        assert "docs/security-deepsec-closed.json" in text
        assert "NOT listed in Next" in text or "not listed in Next" in text

    def test_prompt_seeds_the_fixed_provisioning_finding_as_closed(self):
        text = _prompt()
        assert SEEDED_ID in text
        assert str(SEEDED_PR) in text
        assert SEEDED_COMMIT in text
        assert SEEDED_PATH in text or "setup-vps.sh" in text
        assert SEEDED_LINES in text or "L39-47" in text


class TestClosedItemIsNotEmittedInNext:
    def test_helper_exists(self):
        assert HELPER.is_file()

    def test_closed_item_is_not_emitted_in_next(self, tmp_path):
        import nightly_deepsec_queue as q

        last = tmp_path / "last-audited.json"
        last.write_text(
            json.dumps(
                {
                    "last_audited_sha": "deadbeef",
                    "open_queue": [
                        {
                            "id": SEEDED_ID,
                            "disposition": "operator-only",
                            "action": "Re-provision from a root-owned clone.",
                        },
                        {
                            "id": "DS-2026-10-01-01",
                            "disposition": "operator-only",
                            "action": "Rotate the leftover host key.",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        result = q.reverify(
            last_audited_path=last,
            verdicts=[
                {
                    "id": "DS-2026-10-01-01",
                    "state": "open",
                    "evidence": "host still has the leftover key",
                    "action": "Rotate the leftover host key.",
                }
            ],
            write=False,
        )
        next_text = q.next_text(result["items"])
        assert SEEDED_ID not in next_text
        assert "DS-2026-10-01-01" in next_text
        assert "Rotate the leftover host key." in next_text

    def test_unverifiable_stays_in_next_but_is_labeled(self, tmp_path):
        import nightly_deepsec_queue as q

        last = tmp_path / "last-audited.json"
        last.write_text(
            json.dumps(
                {
                    "last_audited_sha": "deadbeef",
                    "open_queue": [
                        {
                            "id": "DS-2026-10-01-02",
                            "disposition": "operator-only",
                            "action": "Confirm the leftover grant is gone.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        result = q.reverify(last_audited_path=last, verdicts=[], write=False)
        item = next(i for i in result["items"] if i["id"] == "DS-2026-10-01-02")
        assert item["state"] == "unverifiable"
        assert item.get("missing")
        next_text = q.next_text(result["items"])
        assert "DS-2026-10-01-02" in next_text
        assert "unverifiable" in next_text

    def test_closed_queue_is_durable_and_omitted_from_later_next(self, tmp_path):
        import nightly_deepsec_queue as q

        last = tmp_path / "last-audited.json"
        last.write_text(
            json.dumps(
                {
                    "last_audited_sha": "deadbeef",
                    "open_queue": [
                        {
                            "id": SEEDED_ID,
                            "disposition": "operator-only",
                            "action": "Re-provision from a root-owned clone.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        first = q.reverify(last_audited_path=last, verdicts=[], write=True)
        assert any(i["id"] == SEEDED_ID for i in first["closed_tonight"])
        stored = json.loads(last.read_text(encoding="utf-8"))
        assert any(i["id"] == SEEDED_ID for i in stored.get("closed_queue", []))
        assert not any(i.get("id") == SEEDED_ID for i in stored.get("open_queue", []))
        second = q.reverify(last_audited_path=last, verdicts=[], write=True)
        assert all(i["id"] != SEEDED_ID for i in second["closed_tonight"])
        assert SEEDED_ID not in q.next_text(second["items"])

    def test_reopen_requires_new_evidence(self, tmp_path):
        import nightly_deepsec_queue as q

        last = tmp_path / "last-audited.json"
        last.write_text(
            json.dumps(
                {
                    "last_audited_sha": "deadbeef",
                    "open_queue": [
                        {
                            "id": SEEDED_ID,
                            "disposition": "operator-only",
                            "action": "Re-provision from a root-owned clone.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        blocked = q.reverify(
            last_audited_path=last,
            verdicts=[{"id": SEEDED_ID, "state": "open", "evidence": "still looks open"}],
            write=False,
        )
        seeded = next(i for i in blocked["items"] if i["id"] == SEEDED_ID)
        assert seeded["state"] == "closed"
        reopened = q.reverify(
            last_audited_path=last,
            verdicts=[
                {
                    "id": SEEDED_ID,
                    "state": "open",
                    "new_evidence": "origin/main reverted setup-vps.sh L39-47",
                    "action": "Re-apply the root-owned provision store.",
                }
            ],
            write=False,
        )
        seeded = next(i for i in reopened["items"] if i["id"] == SEEDED_ID)
        assert seeded["state"] == "open"
        assert SEEDED_ID in q.next_text(reopened["items"])


class TestSeededFindingIsClosed:
    def test_ledger_closes_ds_2026_09_20_03_with_public_evidence(self):
        data = json.loads(LEDGER.read_text(encoding="utf-8"))
        closed = {row["id"]: row for row in data["closed"]}
        assert SEEDED_ID in closed
        row = closed[SEEDED_ID]
        assert row["pr"] == SEEDED_PR
        assert row["commit"].startswith(SEEDED_COMMIT)
        assert SEEDED_PATH in row["path"]
        assert row["lines"] == SEEDED_LINES
        blob = json.dumps(data)
        assert "token" not in blob.lower()
        assert "secret" not in blob.lower()

    def test_cli_reverify_writes_closed_queue_and_next(self, tmp_path):
        last = tmp_path / "last-audited.json"
        last.write_text(
            json.dumps(
                {
                    "last_audited_sha": "deadbeef",
                    "open_queue": [
                        {
                            "id": SEEDED_ID,
                            "disposition": "operator-only",
                            "action": "Re-provision from a root-owned clone.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        proc = subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "reverify",
                "--last-audited",
                str(last),
                "--write",
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(REPO),
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert SEEDED_ID not in payload["next"]
        assert any(i["id"] == SEEDED_ID for i in payload["closed_tonight"])
        stored = json.loads(last.read_text(encoding="utf-8"))
        assert any(i["id"] == SEEDED_ID for i in stored["closed_queue"])
        seeded = next(i for i in stored["closed_queue"] if i["id"] == SEEDED_ID)
        assert str(SEEDED_PR) in json.dumps(seeded)
        assert SEEDED_COMMIT in json.dumps(seeded)
        assert "closed_at" in seeded


class TestOperatorVerifiedClosures:
    """Operator-verified closures carried as Next every night until ledgered."""

    def test_ledger_closes_operator_verified_ids(self):
        import nightly_deepsec_queue as q

        data = json.loads(LEDGER.read_text(encoding="utf-8"))
        closed = {row["id"]: row for row in data["closed"]}
        expected = {
            "DS-2026-10-04-01": (915, "1f21f367"),
            "DS-2026-09-29-03": (848, "0c946ebd"),
            "DS-2026-09-23-02": (None, None),
        }
        for item_id, (pr, commit) in expected.items():
            assert item_id in closed, item_id
            row = closed[item_id]
            assert row["closed_at"] == "2026-10-08"
            assert row["evidence"]
            if pr is not None:
                assert row["pr"] == pr
                assert row["commit"].startswith(commit)
        assert set(q.load_ledger(LEDGER)) >= set(expected)


ACCEPTED = REPO / "docs" / "security-accepted-risks.json"
TODAY = "2026-10-31"


def _queue(tmp_path, items):
    last = tmp_path / "last-audited.json"
    last.write_text(
        json.dumps(
            {
                "last_audited_sha": "deadbeef",
                "open_queue": [
                    {"disposition": "operator-only", **item} for item in items
                ],
            }
        ),
        encoding="utf-8",
    )
    return last


def _accepted(tmp_path, rows):
    path = tmp_path / "accepted.json"
    path.write_text(json.dumps({"version": 1, "accepted": rows}), encoding="utf-8")
    return path


def _row(item_id, accepted_at="2026-10-15", expires_at="2026-12-15", **extra):
    return {
        "id": item_id,
        "reason": "Host-local only; compensating control documented.",
        "accepted_at": accepted_at,
        "expires_at": expires_at,
        "accepted_by": "operator",
        **extra,
    }


def _without(row, key):
    return {k: v for k, v in row.items() if k != key}


def _open_verdict(item_id, **extra):
    return {"id": item_id, "state": "open", "evidence": "still present on main", **extra}


def _run_cli(args):
    return subprocess.run(
        [sys.executable, str(HELPER), "reverify", *args],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPO),
    )


class TestAcceptedRiskLedger:
    def test_committed_ledger_starts_empty_and_valid(self):
        import nightly_deepsec_queue as q

        data = json.loads(ACCEPTED.read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert data["accepted"] == []
        assert q.load_accepted(ACCEPTED) == {}

    def test_unexpired_acceptance_is_excluded_from_next_but_stays_open_queued(self, tmp_path):
        import nightly_deepsec_queue as q

        item_id = "DS-2099-01-01-01"
        last = _queue(tmp_path, [{"id": item_id, "action": "Rotate the host key."}])
        result = q.reverify(
            last_audited_path=last,
            verdicts=[_open_verdict(item_id)],
            accepted_ledger_path=_accepted(tmp_path, [_row(item_id)]),
            today=q.parse_date(TODAY),
            write=True,
        )
        item = next(i for i in result["items"] if i["id"] == item_id)
        assert item["state"] == "accepted"
        assert item_id not in result["next"]
        assert result["accepted"] == [
            {
                "id": item_id,
                "expires_at": "2026-12-15",
                "accepted_by": "operator",
                "reason": "Host-local only; compensating control documented.",
            }
        ]
        assert item_id not in result["closed_ids"]
        stored = json.loads(last.read_text(encoding="utf-8"))
        assert any(i.get("id") == item_id for i in stored["open_queue"])
        assert not any(i.get("id") == item_id for i in stored["closed_queue"])

    def test_acceptance_expiring_today_still_holds(self, tmp_path):
        import nightly_deepsec_queue as q

        item_id = "DS-2026-10-14-01"
        last = _queue(tmp_path, [{"id": item_id, "action": "Re-key the backup bucket."}])
        result = q.reverify(
            last_audited_path=last,
            verdicts=[_open_verdict(item_id)],
            accepted_ledger_path=_accepted(
                tmp_path, [_row(item_id, accepted_at="2026-08-31", expires_at=TODAY)]
            ),
            today=q.parse_date(TODAY),
        )
        assert next(i for i in result["items"] if i["id"] == item_id)["state"] == "accepted"
        assert result["expired_acceptances"] == []

    def test_expired_acceptance_reverts_and_leads_next(self, tmp_path):
        import nightly_deepsec_queue as q

        expired = "DS-2026-10-14-01"
        other = "DS-2026-10-01-01"
        last = _queue(
            tmp_path,
            [
                {"id": other, "action": "Rotate the leftover host key."},
                {"id": expired, "action": "Re-key the backup bucket."},
            ],
        )
        result = q.reverify(
            last_audited_path=last,
            verdicts=[_open_verdict(other), _open_verdict(expired)],
            accepted_ledger_path=_accepted(
                tmp_path, [_row(expired, accepted_at="2026-08-01", expires_at="2026-10-30")]
            ),
            today=q.parse_date(TODAY),
        )
        item = next(i for i in result["items"] if i["id"] == expired)
        assert item["state"] == "open"
        assert result["expired_acceptances"] == [{"id": expired, "expires_at": "2026-10-30"}]
        assert result["accepted"] == []
        lines = result["next"].splitlines()
        assert lines[0].startswith(f"- **{expired}**")
        assert "(acceptance expired 2026-10-30: re-decide)" in lines[0]
        assert other in lines[1]

    def test_closed_beats_acceptance(self, tmp_path):
        import nightly_deepsec_queue as q

        last = _queue(tmp_path, [{"id": SEEDED_ID, "action": "Re-provision."}])
        result = q.reverify(
            last_audited_path=last,
            verdicts=[],
            accepted_ledger_path=_accepted(tmp_path, [_row(SEEDED_ID)]),
            today=q.parse_date(TODAY),
        )
        assert next(i for i in result["items"] if i["id"] == SEEDED_ID)["state"] == "closed"
        assert result["accepted"] == []

    @pytest.mark.parametrize(
        "row",
        [
            _row("DS-26-10-28-02"),
            _row("DS-2099-01-01-01", reason=""),
            _row("DS-2099-01-01-01", reason="   "),
            _without(_row("DS-2099-01-01-01"), "reason"),
            _without(_row("DS-2099-01-01-01"), "accepted_by"),
            _row("DS-2099-01-01-01", accepted_by=""),
            _row("DS-2099-01-01-01", accepted_at="10/15/2026"),
            _row("DS-2099-01-01-01", expires_at="20261215"),
            _row("DS-2099-01-01-01", expires_at="2026-10-15"),
            _row("DS-2099-01-01-01", expires_at="2026-10-01"),
            _row("DS-2099-01-01-01", accepted_at="2026-10-01", expires_at="2026-12-31"),
            _without(_row("DS-2099-01-01-01"), "expires_at"),
            "DS-2099-01-01-01",
        ],
    )
    def test_malformed_row_fails_closed(self, tmp_path, row):
        import nightly_deepsec_queue as q

        with pytest.raises(ValueError):
            q.load_accepted(_accepted(tmp_path, [row]))

    def test_ninety_day_window_is_inclusive(self, tmp_path):
        import nightly_deepsec_queue as q

        rows = q.load_accepted(
            _accepted(
                tmp_path,
                [_row("DS-2099-01-01-01", accepted_at="2026-10-01", expires_at="2026-12-30")],
            )
        )
        assert set(rows) == {"DS-2099-01-01-01"}

    def test_duplicate_ids_and_bad_shape_fail_closed(self, tmp_path):
        import nightly_deepsec_queue as q

        with pytest.raises(ValueError):
            q.load_accepted(
                _accepted(tmp_path, [_row("DS-2099-01-01-01"), _row("DS-2099-01-01-01")])
            )
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"version": 1, "accepted": {}}), encoding="utf-8")
        with pytest.raises(ValueError):
            q.load_accepted(bad)
        bad.write_text(json.dumps({"version": 2, "accepted": []}), encoding="utf-8")
        with pytest.raises(ValueError):
            q.load_accepted(bad)

    def test_cli_accepts_ledger_and_today(self, tmp_path):
        item_id = "DS-2099-01-01-01"
        last = _queue(tmp_path, [{"id": item_id, "action": "Rotate the host key."}])
        verdicts = tmp_path / "verdicts.json"
        verdicts.write_text(json.dumps([_open_verdict(item_id)]), encoding="utf-8")
        ledger = _accepted(tmp_path, [_row(item_id, expires_at="2026-11-10")])
        base = [
            "--last-audited", str(last),
            "--verdicts", str(verdicts),
            "--accepted-ledger", str(ledger),
            "--json",
        ]
        held = _run_cli(base + ["--today", "2026-11-10"])
        assert held.returncode == 0, held.stderr
        payload = json.loads(held.stdout)
        assert payload["accepted"][0]["id"] == item_id
        assert payload["expired_acceptances"] == []
        assert item_id not in payload["next"]
        lapsed = _run_cli(base + ["--today", "2026-11-11"])
        assert lapsed.returncode == 0, lapsed.stderr
        payload = json.loads(lapsed.stdout)
        assert payload["expired_acceptances"] == [{"id": item_id, "expires_at": "2026-11-10"}]
        assert "acceptance expired 2026-11-10: re-decide" in payload["next"]

    def test_cli_malformed_ledger_exits_nonzero(self, tmp_path):
        last = _queue(tmp_path, [{"id": "DS-2099-01-01-01", "action": "x"}])
        ledger = _accepted(tmp_path, [_row("DS-2099-01-01-01", reason="")])
        proc = _run_cli(
            ["--last-audited", str(last), "--accepted-ledger", str(ledger), "--today", TODAY]
        )
        assert proc.returncode == 2


class TestNextCappedAtThree:
    def test_next_caps_at_three_with_counts_only_overflow(self):
        import nightly_deepsec_queue as q

        items = [
            {"id": "DS-2026-10-05-01", "state": "unverifiable", "action": "U1.", "missing": "m"},
            {"id": "DS-2026-10-04-01", "state": "open", "action": "O4."},
            {"id": "DS-2026-10-03-01", "state": "open", "action": "O3."},
            {"id": "DS-2026-10-02-01", "state": "open", "action": "O2."},
            {"id": "DS-2026-10-01-01", "state": "closed", "action": "C."},
            {"id": "DS-2026-10-06-01", "state": "accepted", "action": "A."},
        ]
        text = q.next_text(items)
        lines = text.splitlines()
        assert len(lines) == 4
        assert [line.split("**")[1] for line in lines[:3]] == [
            "DS-2026-10-02-01",
            "DS-2026-10-03-01",
            "DS-2026-10-04-01",
        ]
        assert lines[3] == "- 1 more operator-only item(s) in the private report."
        assert "DS-2026-10-05-01" not in text
        assert "DS-2026-10-06-01" not in text

    def test_ordering_expired_then_open_then_unverifiable_with_severity(self):
        import nightly_deepsec_queue as q

        items = [
            {"id": "DS-2026-10-01-01", "state": "unverifiable", "action": "U.",
             "missing": "m", "severity": "P0"},
            {"id": "DS-2026-10-02-01", "state": "open", "action": "O-P3.", "severity": "P3"},
            {"id": "DS-2026-10-03-01", "state": "open", "action": "O-P1.", "severity": "P1"},
            {"id": "DS-2026-10-09-01", "state": "unverifiable", "action": "EXP.",
             "missing": "m", "acceptance_expired": "2026-10-20"},
        ]
        lines = q.next_text(items).splitlines()
        ids = [line.split("**")[1] for line in lines[:3]]
        assert ids == ["DS-2026-10-09-01", "DS-2026-10-03-01", "DS-2026-10-02-01"]
        assert "(acceptance expired 2026-10-20: re-decide)" in lines[0]
        assert "(unverifiable)" in lines[0]
        assert lines[3] == "- 1 more operator-only item(s) in the private report."

    def test_no_overflow_line_at_or_under_three(self):
        import nightly_deepsec_queue as q

        items = [
            {"id": f"DS-2026-10-0{n}-01", "state": "open", "action": "A."} for n in (1, 2, 3)
        ]
        lines = q.next_text(items).splitlines()
        assert len(lines) == 3
        assert "more operator-only" not in "\n".join(lines)
        assert q.next_text([]) == ""

    def test_reverify_items_keep_full_list(self, tmp_path):
        import nightly_deepsec_queue as q

        ids = [f"DS-2026-10-1{n}-01" for n in range(5)]
        last = _queue(tmp_path, [{"id": i, "action": "Act."} for i in ids])
        result = q.reverify(
            last_audited_path=last,
            verdicts=[_open_verdict(i) for i in ids],
            today=q.parse_date(TODAY),
        )
        assert {i["id"] for i in result["items"]} >= set(ids)
        assert result["next"].splitlines()[-1] == (
            "- 2 more operator-only item(s) in the private report."
        )


class TestPromptStatesCapAndAcceptance:
    def test_prompt_states_cap_overflow_and_ledger_rules(self):
        text = _prompt()
        start = text.index("## Operator-only re-verify (every phase)")
        section = text[start:text.index("## Remediation mode")]
        assert "`accepted`" in section
        assert "docs/security-accepted-risks.json" in section
        assert "three" in section
        assert "more operator-only item(s) in the private report" in section
        assert "never writes" in section
        assert "re-decide" in section
        assert "—" not in section
