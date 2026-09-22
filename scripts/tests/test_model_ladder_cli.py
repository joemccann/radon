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


class TestSlmModes:
    PAYLOAD = {
        "system": "Pick EXACTLY 3 tags",
        "instruction": "Title: Hated puts\nBody: Put call ratio",
        "accept": "tags",
        "post_id": "sample-00",
    }

    def _run(self, payload, *, complete, env=None, write_shadow=None):
        stdout = io.StringIO()
        code = main(
            stdin=io.StringIO(json.dumps(payload)),
            stdout=stdout,
            complete=complete,
            env=env or {},
            write_shadow=write_shadow or (lambda *_a, **_k: True),
        )
        return code, json.loads(stdout.getvalue())

    def _ok(self, provider="anthropic"):
        return LadderResult(
            data={"tags": ["PUTS", "OPTIONS", "POSITIONING"]},
            text='{"tags":["PUTS","OPTIONS","POSITIONING"]}',
            provider=provider,
            model="m",
            attempted=(f"{provider}:ok",),
        )

    def test_off_and_unset_match_today(self):
        captured = []

        def complete(*args, **kwargs):
            captured.append(kwargs)
            return self._ok()

        self._run(self.PAYLOAD, complete=complete, env={})
        self._run(self.PAYLOAD, complete=complete, env={"RADON_SLM_TAGGER_MODE": "off"})
        assert "providers" not in captured[0]
        assert "providers" not in captured[1]
        assert captured[0]["accept"].__name__ == "accept_tags_payload"

    def test_distill_never_gets_the_rung(self):
        captured = []

        def complete(*args, **kwargs):
            captured.append(kwargs)
            return LadderResult(data={"summary": "x"}, text="{}", provider="anthropic", model="m")

        self._run(
            {**self.PAYLOAD, "accept": "distill", "instruction": "summarise"},
            complete=complete,
            env={"RADON_SLM_TAGGER_MODE": "primary"},
        )
        assert "providers" not in captured[0]

    def test_prefer_order(self):
        from newsfeed.slm.modes import SLM_PREFER_ORDER

        captured = []

        def complete(*args, **kwargs):
            captured.append(kwargs)
            return self._ok("slm-tagger")

        self._run(self.PAYLOAD, complete=complete, env={"RADON_SLM_TAGGER_MODE": "prefer"})
        assert tuple(captured[0]["providers"]) == SLM_PREFER_ORDER

    def test_primary_order(self):
        from newsfeed.slm.modes import SLM_PRIMARY_ORDER

        captured = []

        def complete(*args, **kwargs):
            captured.append(kwargs)
            return self._ok("slm-tagger")

        self._run(self.PAYLOAD, complete=complete, env={"RADON_SLM_TAGGER_MODE": "primary"})
        assert tuple(captured[0]["providers"]) == SLM_PRIMARY_ORDER

    def test_shadow_writes_row_and_returns_ladder(self):
        shadows = []

        def complete(*args, **kwargs):
            if kwargs.get("providers") == ["slm-tagger"]:
                return LadderResult(
                    data={"tags": ["GAMMA", "SPX", "VOL"]},
                    text='{"tags":["GAMMA","SPX","VOL"]}',
                    provider="slm-tagger",
                    model="radon-slm-tagger",
                    attempted=("slm-tagger:ok",),
                )
            return self._ok()

        code, body = self._run(
            self.PAYLOAD,
            complete=complete,
            env={"RADON_SLM_TAGGER_MODE": "shadow"},
            write_shadow=lambda row: shadows.append(row),
        )
        assert code == 0
        assert body["provider"] == "anthropic"
        assert shadows[0]["slm_status"] == "ok"
        assert shadows[0]["mode"] == "shadow"

    def test_shadow_timeout_and_unavailable(self):
        shadows = []

        def complete(*args, **kwargs):
            if kwargs.get("providers") == ["slm-tagger"]:
                return LadderResult(
                    data=None,
                    text="",
                    provider="anthropic",
                    model="",
                    attempted=("slm-tagger:unavailable",),
                )
            return self._ok()

        self._run(
            self.PAYLOAD,
            complete=complete,
            env={"RADON_SLM_TAGGER_MODE": "shadow"},
            write_shadow=lambda row: shadows.append(row),
        )
        assert shadows[0]["slm_status"] == "unavailable"

    def test_one_in_twenty_sample_is_deterministic(self):
        from newsfeed.slm.shadow import should_sample_ladder

        hits = [should_sample_ladder(f"post-{i}") for i in range(400)]
        assert 0 < sum(hits) < 400
        assert should_sample_ladder("post-1") == should_sample_ladder("post-1")
        shadows = []

        def complete(*args, **kwargs):
            return self._ok("slm-tagger")

        sampled_id = next(f"post-{i}" for i in range(400) if should_sample_ladder(f"post-{i}"))
        self._run(
            {**self.PAYLOAD, "post_id": sampled_id},
            complete=complete,
            env={"RADON_SLM_TAGGER_MODE": "prefer"},
            write_shadow=lambda row: shadows.append(row),
        )
        assert shadows[-1]["ladder_sampled"] == 1
