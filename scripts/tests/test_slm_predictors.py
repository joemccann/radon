"""T-503: real predictor entry points receive identical corpus prompts, with fake inference."""
import json
from types import SimpleNamespace
import pytest
from newsfeed.slm import predict_ladder, predict_slm


@pytest.mark.parametrize("override", [None, "override taxonomy"])
def test_prompt_parity_and_row_identity(tmp_path, monkeypatch, override):
    monkeypatch.delenv("RADON_LADDER_ALLOW_PREPAID", raising=False)
    rows = [{"id": str(i), "messages": [
        {"role": "system", "content": f"taxonomy {i}"},
        {"role": "user", "content": f"post {i}"},
    ]} for i in range(2)]
    gold = tmp_path / "gold.jsonl"
    gold.write_text("".join(json.dumps(row) + "\n" for row in rows))
    extra = []
    if override is not None:
        system_file = tmp_path / "system.txt"
        system_file.write_text(override)
        extra = ["--system-file", str(system_file)]
    a_calls, c_calls = [], []
    def ladder(user, *, system, **kwargs):
        a_calls.append((system, user))
        return SimpleNamespace(data={"tags": ["GAMMA", "SPX", "VOL"]}, provider="fake")
    def slm(url, user, *, system, timeout):
        c_calls.append((system, user))
        return {"tags": ["GAMMA", "SPX", "VOL"]}, 0.1
    monkeypatch.setattr(predict_ladder, "complete_text_json", ladder)
    monkeypatch.setattr(predict_slm, "predict_one", slm)
    for module, arm in [(predict_ladder, "A"), (predict_slm, "C")]:
        out = tmp_path / f"{arm}.jsonl"
        assert module.main(["--gold", str(gold), "--out", str(out), *extra]) == 0
        records = [json.loads(line) for line in out.read_text().splitlines()]
        assert [row["id"] for row in records] == ["0", "1"]
        assert all(row["arm"] == arm and row["pred"]["tags"] == ["GAMMA", "SPX", "VOL"] for row in records)
    expected = [(override or f"taxonomy {i}", f"post {i}") for i in range(2)]
    assert a_calls == c_calls == expected


def test_ladder_prepaid_refusal_never_invokes_inference(tmp_path, monkeypatch):
    monkeypatch.setenv("RADON_LADDER_ALLOW_PREPAID", "1")
    def forbidden(*args, **kwargs):
        pytest.fail("prepaid arm A must refuse before inference")
    monkeypatch.setattr(predict_ladder, "complete_text_json", forbidden)
    out = tmp_path / "out.jsonl"
    assert predict_ladder.main(["--gold", str(tmp_path / "missing"), "--out", str(out)]) == 2
    assert not out.exists()


@pytest.mark.parametrize("module,error,status", [(predict_ladder, RuntimeError, "unparseable"), (predict_slm, TimeoutError, "unavailable")])
def test_predictor_error_records_preserve_identity(tmp_path, monkeypatch, module, error, status):
    monkeypatch.delenv("RADON_LADDER_ALLOW_PREPAID", raising=False)
    def fail(*args, **kwargs):
        raise error("fake")
    monkeypatch.setattr(module, "complete_text_json" if module is predict_ladder else "predict_one", fail)
    gold, out = tmp_path / "gold.jsonl", tmp_path / "out.jsonl"
    gold.write_text(json.dumps({"id": "broken", "user": "fake post"}) + "\n")
    assert module.main(["--gold", str(gold), "--out", str(out)]) == 0
    row = json.loads(out.read_text())
    assert (row["id"], row["slm_status"], row["error"], row["pred"]) == ("broken", status, error.__name__, {"tags": []})
