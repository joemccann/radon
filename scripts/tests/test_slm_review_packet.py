from __future__ import annotations

from newsfeed.slm.build_review_packet import build_packet


def test_review_packet_is_blinded_month_stratified_and_meets_image_minimum() -> None:
    gold = []
    metadata = []
    predictions = {arm: [] for arm in ("A", "B", "C")}
    for index in range(200):
        post_id = str(index)
        month = "2026-08" if index < 100 else "2026-09"
        gold.append({
            "id": post_id,
            "timestamp": f"{month}-10T00:00:00+00:00",
            "tags": ["RARE", "B", "C"],
            "messages": [{"role": "user", "content": f"Title: title {index}\nBody: body {index}"}],
        })
        metadata.append({"id": post_id, "images": ["https://images.example.invalid/post.png"] if index < 50 else []})
        for arm in predictions:
            predictions[arm].append({"id": post_id, "pred": {"tags": ["RARE", "B", "C"]}})

    packet, key = build_packet(
        run_id="synthetic-run",
        gold_rows=gold,
        pred_rows=predictions,
        metadata_rows=metadata,
        taxonomy=["RARE", "B", "C"],
        rare=["RARE"],
    )

    assert packet["schema"] == "radon.slm-review.v1"
    assert len(packet["items"]) == 200
    assert packet["stratification"]["image_posts"] >= 40
    assert set(packet["stratification"]["month_counts"]) == {"2026-08", "2026-09"}
    assert "gold" not in packet["items"][0]
    assert set(packet["items"][0]["candidates"]) == {"Candidate 1", "Candidate 2", "Candidate 3"}
    assert set(key["aliasToArm"][packet["items"][0]["id"]]) == {"Candidate 1", "Candidate 2", "Candidate 3"}
    assert set(key["aliasToArm"][packet["items"][0]["id"]].values()) == {"A", "B", "C"}
    assert packet["items"][0]["id"] not in {row["id"] for row in gold}
    assert key["reviewIdToPostId"][packet["items"][0]["id"]] in {row["id"] for row in gold}
    assert set(key["reviewIdToPostId"]) == set(key["aliasToArm"])
