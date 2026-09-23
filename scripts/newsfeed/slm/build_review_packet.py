#!/usr/bin/env python3.13
"""Build a blinded, month-stratified human packet from a pinned holdout."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import stat
import sys
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_SCRIPTS = Path(__file__).resolve().parents[2]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from newsfeed.slm.contract import normalise_tags  # noqa: E402
from newsfeed.slm.eval import gold_tags, load_jsonl
from newsfeed.slm.predict_slm import user_content


def index(rows: list[dict[str, Any]], name: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = row.get("id") or row.get("post_id")
        if value is None:
            raise ValueError(f"{name} contains a row without an ID")
        key = str(value)
        if key in result:
            raise ValueError(f"{name} contains duplicate ID {key}")
        result[key] = row
    return result


def clean_image_url(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("url") or value.get("src")
    if not isinstance(value, str):
        return None
    parts = urlsplit(value.strip())
    try:
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or (port and port != 443):
        return None
    # Signed/query parameters are not needed for review, and may embed tokens.
    return urlunsplit(("https", parts.hostname.lower(), parts.path, "", ""))


def _rank(run_id: str, post_id: str) -> str:
    return hashlib.sha256(f"{run_id}\0{post_id}".encode()).hexdigest()


def stratified_take(rows: list[dict[str, Any]], count: int, run_id: str) -> list[dict[str, Any]]:
    if count <= 0:
        return []
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["month"]].append(row)
    total = sum(map(len, groups.values()))
    count = min(count, total)
    quotas = {month: count * len(group) / total for month, group in groups.items()}
    allocation = {month: min(len(groups[month]), int(quotas[month])) for month in groups}
    left = count - sum(allocation.values())
    for month in sorted(groups, key=lambda key: (-(quotas[key] - int(quotas[key])), key)):
        if left == 0:
            break
        if allocation[month] < len(groups[month]):
            allocation[month] += 1
            left -= 1
    selected: list[dict[str, Any]] = []
    for month, group in groups.items():
        selected.extend(sorted(group, key=lambda row: _rank(run_id, row["id"]))[:allocation[month]])
    return selected


def build_packet(*, run_id: str, gold_rows: list[dict], pred_rows: dict[str, list[dict]], metadata_rows: list[dict], taxonomy: list[str], rare: list[str], sample_size: int = 200) -> tuple[dict, dict]:
    if sample_size != 200:
        raise ValueError("The review gate is pinned to exactly 200 examples")
    gold = index(gold_rows, "gold")
    metadata = index(metadata_rows, "source metadata")
    arms = {arm: index(rows, f"arm {arm}") for arm, rows in pred_rows.items()}
    if set(arms) != {"A", "B", "C"}:
        raise ValueError("Predictions for arms A, B, and C are required")
    for name, values in [("metadata", metadata), *[(arm, values) for arm, values in arms.items()]]:
        if gold.keys() != values.keys():
            raise ValueError(f"{name} IDs do not exactly match pinned holdout IDs")
    rare_set = set(rare)
    rows = []
    blinded_arms: dict[str, dict[str, str]] = {}
    review_to_post: dict[str, str] = {}
    for post_id, gold_row in gold.items():
        source = metadata[post_id]
        content = user_content(gold_row)
        title, _, body = content.partition("\nBody: ")
        if title.startswith("Title: "):
            title = title[len("Title: "):]
        timestamp = str(gold_row.get("timestamp") or source.get("timestamp") or "")
        month = timestamp[:7]
        image_raw = source.get("images") or source.get("image_urls") or []
        if isinstance(image_raw, str):
            try: image_raw = json.loads(image_raw)
            except json.JSONDecodeError: image_raw = []
        if isinstance(image_raw, dict):
            image_raw = image_raw.get("images") or image_raw.get("urls") or []
        image_urls = list(dict.fromkeys(url for url in (clean_image_url(value) for value in image_raw if isinstance(image_raw, list)) if url))[:6]
        candidate_tags = {}
        arm_order = ["A", "B", "C"]
        secrets.SystemRandom().shuffle(arm_order)
        alias_to_arm = dict(zip(("Candidate 1", "Candidate 2", "Candidate 3"), arm_order, strict=True))
        review_id = uuid.uuid4().hex
        blinded_arms[review_id] = alias_to_arm
        review_to_post[review_id] = post_id
        for alias, arm in alias_to_arm.items():
            payload = arms[arm][post_id].get("pred") or {}
            raw_tags = payload.get("tags") if isinstance(payload, dict) else []
            if not isinstance(raw_tags, list): raw_tags = []
            candidate_tags[alias] = [str(tag)[:80] for tag in raw_tags[:3] if isinstance(tag, str)]
        rare_signal = bool(set(gold_tags(gold_row)) & rare_set) or any(
            set(normalise_tags(candidate_tags[arm])) & rare_set for arm in candidate_tags
        )
        rows.append({
            "id": review_id, "month": month, "title": title.strip(), "text": body.strip(),
            "imageUrls": image_urls, "candidates": candidate_tags, "_rare": rare_signal,
        })
    if len(rows) < sample_size:
        raise ValueError(f"Need at least 200 holdout rows; found {len(rows)}")
    images = [row for row in rows if row["imageUrls"]]
    if len(images) < 40:
        raise ValueError(f"Holdout contains only {len(images)} image posts; cannot satisfy the 40-image gate")
    selected: dict[str, dict] = {}
    # Oversample rare-label opportunities first, then reserve 50 image posts.
    for row in stratified_take([item for item in rows if item["_rare"]], 60, run_id):
        selected[row["id"]] = row
    image_needed = max(0, 50 - sum(bool(item["imageUrls"]) for item in selected.values()))
    for row in stratified_take([item for item in images if item["id"] not in selected], image_needed, run_id):
        selected[row["id"]] = row
    fill = 200 - len(selected)
    for row in stratified_take([item for item in rows if item["id"] not in selected], fill, run_id):
        selected[row["id"]] = row
    if len(selected) != 200 or sum(bool(item["imageUrls"]) for item in selected.values()) < 40:
        raise ValueError("Could not satisfy the exact sample and image-post constraints")
    final = sorted(selected.values(), key=lambda row: _rank(run_id, row["id"]))
    month_counts = Counter(item["month"] for item in final)
    payload = {
        "schema": "radon.slm-review.v1", "runId": run_id,
        "taxonomy": taxonomy,
        "stratification": {
            "method": "month-stratified; rare-label opportunities reserved first; 50 image-post target",
            "month_counts": dict(sorted(month_counts.items())),
            "image_posts": sum(bool(item["imageUrls"]) for item in final),
            "rare_signal_posts": sum(item["_rare"] for item in final),
            "holdout_population": len(rows),
        },
        # Do not include gold labels or model names in the client packet.
        "items": [{key: value for key, value in item.items() if not key.startswith("_")} for item in final],
    }
    secret = {
        "schema": "radon.slm-review-key.v1",
        "runId": run_id,
        "reviewIdToPostId": {row["id"]: review_to_post[row["id"]] for row in final},
        "aliasToArm": {row["id"]: blinded_arms[row["id"]] for row in final},
    }
    return payload, secret


def write_private_json(path: Path, value: dict, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if stat.S_IMODE(path.parent.stat().st_mode) & 0o077:
        raise PermissionError(f"Private output directory must be mode 0700: {path.parent}")
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, separators=(",", ":"))
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--gold-test", required=True)
    parser.add_argument("--pred-a", required=True)
    parser.add_argument("--pred-b", required=True)
    parser.add_argument("--pred-c", required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--taxonomy", required=True)
    parser.add_argument("--rare", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--mapping-out", required=True, help="Private key file; keep away from the reviewer packet")
    args = parser.parse_args()
    packet, mapping = build_packet(
        run_id=args.run_id,
        gold_rows=load_jsonl(Path(args.gold_test)),
        pred_rows={"A": load_jsonl(Path(args.pred_a)), "B": load_jsonl(Path(args.pred_b)), "C": load_jsonl(Path(args.pred_c))},
        metadata_rows=load_jsonl(Path(args.metadata)),
        taxonomy=json.loads(Path(args.taxonomy).read_text(encoding="utf-8")),
        rare=json.loads(Path(args.rare).read_text(encoding="utf-8")),
    )
    target = Path(args.out)
    key_target = Path(args.mapping_out)
    if target.resolve() == key_target.resolve():
        raise ValueError("Review packet and blinding key must be separate files")
    if target.resolve().parent == key_target.resolve().parent:
        raise ValueError("Review packet and blinding key must be in separate directories")
    if target.exists() or key_target.exists():
        raise FileExistsError("Review outputs already exist; choose fresh paths so packet and key cannot drift")
    write_private_json(target, packet, exclusive=True)
    write_private_json(key_target, mapping, exclusive=True)
    print(json.dumps({"run_id": args.run_id, "sample_n": len(packet["items"]), **packet["stratification"], "packet_sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "out": str(target)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
