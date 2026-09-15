"""CLI bridge for the shared text-JSON ladder (newsfeed tagger stdin/stdout)."""
from __future__ import annotations

import io
import json
from clients.model_ladder import LadderResult, ModelLadderExhausted
from clients.model_ladder_cli import main


def _run(payload: dict, *, complete) -> tuple[int, dict]:
    stdout = io.StringIO()
    code = main(
        stdin=io.StringIO(json.dumps(payload)),
        stdout=stdout,
        complete=complete,
    )
    return code, json.loads(stdout.getvalue())


class TestModelLadderCli:
    def test_tags_contract_returns_stdout_json(self):
        captured = {}

        def complete(instruction, **kwargs):
            captured.update(kwargs)
            captured["instruction"] = instruction
            return LadderResult(
                data={"tags": ["PUTS", "OPTIONS", "POSITIONING"]},
                text='{"tags":["PUTS","OPTIONS","POSITIONING"]}',
                provider="anthropic",
                model="claude-sonnet-4-6",
            )

        code, body = _run(
            {
                "system": "Pick EXACTLY 3 tags",
                "instruction": "Title: Hated puts\nBody: Put call ratio",
                "accept": "tags",
            },
            complete=complete,
        )
        assert code == 0
        assert body == {
            "ok": True,
            "data": {"tags": ["PUTS", "OPTIONS", "POSITIONING"]},
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
        }
        assert captured["accept"].__name__ == "accept_tags_payload"
        assert captured["require_end_turn"] is False

    def test_exhausted_soft_fails(self):
        def complete(*_args, **_kwargs):
            raise ModelLadderExhausted("no keyed provider")

        code, body = _run({"instruction": "x", "accept": "tags"}, complete=complete)
        assert code == 0
        assert body == {"ok": False, "error": "exhausted"}

    def test_invalid_stdin_soft_fails_without_complete(self):
        stdout = io.StringIO()
        code = main(stdin=io.StringIO("not-json"), stdout=stdout, complete=lambda *_a, **_k: None)
        assert code == 0
        assert json.loads(stdout.getvalue()) == {"ok": False, "error": "invalid_request"}
