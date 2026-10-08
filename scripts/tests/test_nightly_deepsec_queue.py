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
