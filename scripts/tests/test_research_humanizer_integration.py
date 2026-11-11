"""The publisher refuses research copy without a current Humanizer certificate."""
import pytest


@pytest.mark.parametrize("mutation", ["missing", "stale"])
def test_final_publisher_rejects_missing_or_stale_humanizer_proof(tmp_path, monkeypatch, mutation):
    import test_research_publish as fixtures
    from research import publish

    value = fixtures.post.__wrapped__(tmp_path, monkeypatch)
    if mutation == "missing":
        value["source"].pop("humanizer")
    else:
        value["content"] += " Edited after review."

    monkeypatch.setattr(publish, "hrana_transaction", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("invalid certificate reached persistence")))
    with pytest.raises(ValueError, match="Humanizer"):
        publish.publish(value)
