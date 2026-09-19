#!/usr/bin/env python3.13
"""I.3 post-deploy monitor. Exit 3 on threshold breach. No-op when mode is off or shadow."""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

_SCRIPTS = Path(__file__).resolve().parents[2]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from newsfeed.slm.contract import HONESTY_LABEL  # noqa: E402
from newsfeed.slm.modes import SLM_MODE_OFF, SLM_MODE_SHADOW, normalise_mode  # noqa: E402

SERVICE_NAME = "slm-tagger-monitor"

INVALID_STATUSES = frozenset({"unparseable", "abstain:count", "abstain:empty"})
AVAIL_STATUSES = frozenset({"unavailable", "timeout"})


def js_divergence(p: Mapping[str, float], q: Mapping[str, float]) -> float:
    keys = sorted(set(p) | set(q))
    if not keys:
        return 0.0

    def _vec(src: Mapping[str, float]) -> list[float]:
        vals = [float(src.get(k, 0.0)) for k in keys]
        total = sum(vals)
        if total <= 0:
            return [1.0 / len(keys)] * len(keys)
        return [v / total for v in vals]

    pv, qv = _vec(p), _vec(q)
    mid = [(a + b) / 2.0 for a, b in zip(pv, qv)]

    def _kl(a: list[float], b: list[float]) -> float:
        acc = 0.0
        for x, y in zip(a, b):
            if x > 0 and y > 0:
                acc += x * math.log(x / y)
        return acc

    return 0.5 * _kl(pv, mid) + 0.5 * _kl(qv, mid)


def _share(rows: Sequence[Mapping[str, Any]], pred) -> float:
    if not rows:
        return 0.0
    return sum(1 for row in rows if pred(row)) / len(rows)


def _p95(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[max(0, idx)]


def _as_tag_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if isinstance(value, dict):
        value = value.get("tags")
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def evaluate_signals(
    rows_7d: Sequence[Mapping[str, Any]],
    rows_28d: Sequence[Mapping[str, Any]],
    *,
    train_distribution: Mapping[str, float] | None = None,
    shadow_jaccard_mean: float | None = None,
) -> dict[str, Any]:
    invalid = _share(rows_7d, lambda r: str(r.get("slm_status") or "") in INVALID_STATUSES)
    oot_rows = [r for r in rows_7d if r.get("slm_status") == "abstain:out_of_taxonomy"]
    oot = len(oot_rows) / len(rows_7d) if rows_7d else 0.0
    unknown: Counter[str] = Counter()
    for row in oot_rows:
        unknown.update(_as_tag_list(row.get("tags_slm_raw")))
    unknown_hot = {tag: n for tag, n in unknown.items() if n >= 10}

    slm_freq: Counter[str] = Counter()
    for row in rows_28d:
        slm_freq.update(_as_tag_list(row.get("tags_slm")))
    js = js_divergence(dict(slm_freq), dict(train_distribution or {}))
    train_zero = 0
    if train_distribution:
        train_zero = sum(1 for tag in train_distribution if slm_freq.get(tag, 0) == 0)

    sampled = [r for r in rows_28d if r.get("ladder_sampled") or r.get("jaccard") is not None]
    jaccards = [float(r["jaccard"]) for r in sampled if r.get("jaccard") is not None]
    jaccard_mean = sum(jaccards) / len(jaccards) if jaccards else None
    agree_drop = None
    if jaccard_mean is not None and shadow_jaccard_mean is not None:
        agree_drop = shadow_jaccard_mean - jaccard_mean

    avail = _share(rows_7d, lambda r: str(r.get("slm_status") or "") in AVAIL_STATUSES)
    latencies = [float(r["slm_latency_ms"]) for r in rows_7d if r.get("slm_latency_ms") is not None]
    p95 = _p95(latencies)

    breaches: list[str] = []
    if invalid > 0.01:
        breaches.append("invalid")
    if oot > 0.03 or unknown_hot:
        breaches.append("vocabulary_drift")
    if js > 0.10:
        breaches.append("label_shift")
    if agree_drop is not None and agree_drop > 0.05:
        breaches.append("agreement")
    if avail > 0.05:
        breaches.append("availability")
    if p95 is not None and p95 > 10_000:
        breaches.append("latency")

    return {
        "honesty": HONESTY_LABEL,
        "n_7d": len(rows_7d),
        "n_28d": len(rows_28d),
        "invalid_or_empty": invalid,
        "vocabulary_drift": oot,
        "unknown_tags": dict(unknown),
        "unknown_hot": unknown_hot,
        "js_divergence": js,
        "train_tags_zero_pred": train_zero,
        "jaccard_28d": jaccard_mean,
        "agreement_drop": agree_drop,
        "availability": avail,
        "latency_p95_ms": p95,
        "breaches": breaches,
    }


def write_heartbeat(state: str, error: dict | None = None) -> None:
    from db.hrana_http import write_service_health_http

    now = datetime.now(timezone.utc).isoformat()
    write_service_health_http(
        SERVICE_NAME,
        state,
        started_at=now,
        finished_at=now,
        error=error,
    )


def _load_shadow_rows(days: int) -> list[dict[str, Any]]:
    from db.hrana_http import hrana_query

    raw = hrana_query(
        f"SELECT * FROM slm_tagger_shadow WHERE observed_at > datetime('now','-{int(days)} days')",
        (),
    )
    out: list[dict[str, Any]] = []
    for row in raw:
        out.append(row if isinstance(row, dict) else {"raw": row})
    return out


def main(
    argv: list[str] | None = None,
    *,
    rows_7d: Sequence[Mapping[str, Any]] | None = None,
    rows_28d: Sequence[Mapping[str, Any]] | None = None,
    heartbeat: bool = True,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="data/slm/tagger/v1/manifest.json")
    parser.add_argument("--shadow-jaccard-mean", type=float, default=None)
    args = parser.parse_args(argv)
    mode = normalise_mode(os.environ.get("RADON_SLM_TAGGER_MODE", "off"))
    if mode in {SLM_MODE_OFF, SLM_MODE_SHADOW}:
        payload = {"honesty": HONESTY_LABEL, "mode": mode, "noop": True}
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        if heartbeat:
            try:
                write_heartbeat("ok", error={"class": "noop", "mode": mode})
            except Exception:
                pass
        return 0

    train_dist: dict[str, float] = {}
    manifest_path = Path(args.manifest)
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        train_dist = {
            str(k): float(v) for k, v in (manifest.get("label_distribution") or {}).items()
        }

    try:
        left = list(rows_7d) if rows_7d is not None else _load_shadow_rows(7)
        right = list(rows_28d) if rows_28d is not None else _load_shadow_rows(28)
    except Exception as exc:  # noqa: BLE001
        payload = {"honesty": HONESTY_LABEL, "error": type(exc).__name__, "breaches": ["unavailable"]}
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        if heartbeat:
            try:
                write_heartbeat("error", error={"class": type(exc).__name__})
            except Exception:
                pass
        return 3

    signals = evaluate_signals(
        left,
        right,
        train_distribution=train_dist,
        shadow_jaccard_mean=args.shadow_jaccard_mean,
    )
    signals["mode"] = mode
    json.dump(signals, sys.stdout, indent=2, sort_keys=True, default=str)
    sys.stdout.write("\n")
    breached = bool(signals["breaches"])
    if heartbeat:
        try:
            write_heartbeat(
                "error" if breached else "ok",
                error={"class": "monitor_breach", "breaches": signals["breaches"]} if breached else None,
            )
        except Exception:
            pass
    return 3 if breached else 0


if __name__ == "__main__":
    raise SystemExit(main())
