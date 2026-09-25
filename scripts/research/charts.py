"""Chart plans for text-only research findings.

Mirrors web/lib/planCharts.ts. A series is plotted only when every number
traces to its label. Line, bar, range, and scatter are the chart types.
"""
from __future__ import annotations

import math
import re

STOP = {
    "The", "Its", "It", "But", "That", "This", "Earlier", "Credit", "Markets",
    "Rates", "An", "US", "HY", "AI", "GS", "PMI", "Fed", "September", "Virginia",
    "Jan", "Sep", "Global", "Research", "Deutsche", "Bank", "Finance", "Government",
}
ISSUER = re.compile(r"\b(finance|government|construction|supranational|technology|energy|corporate)\b", re.I)


def plan(raw: str) -> list:
    text = _normalize(raw)
    if not text:
        return []
    return [item for item in _build(text) if _verify(item, text)]


def _normalize(text: str) -> str:
    return re.sub(r"[ \t]+", " ", (text or "").replace("–", "-").replace("—", "-")).strip()


def _round(value: float) -> float:
    return round(value * 1000) / 1000


def _num(value: float):
    return int(value) if value == int(value) else value


def _sentences(text: str) -> list:
    shielded = re.sub(r"(\d)\.(\d)", lambda match: match.group(1) + "\0" + match.group(2), text)
    found = []
    for match in re.finditer(r"[^.!?]+[.!?]+|[^.!?]+$", shielded):
        raw = match.group(0)
        lead = len(raw) - len(raw.lstrip())
        start = match.start() + lead
        body = raw[lead:].strip().replace("\0", ".")
        if body:
            found.append({"start": start, "end": start + len(body), "text": body})
    return found


def _sentence_at(sentences: list, index: int):
    return next((item for item in sentences if item["start"] <= index < item["end"]), None)


def _overlaps(spans, start, end) -> bool:
    return any(start < stop and end > begin for begin, stop in spans)


def _last_name(text: str) -> str:
    found = ""
    for match in re.finditer(r"\b([A-Z][A-Za-z0-9]+)(?:['’]s)?(?:\s+([A-Z][A-Za-z0-9]*))?", text):
        first, second = match.group(1), match.group(2)
        if first in STOP:
            continue
        found = f"{first} {second}" if second and second not in STOP else first
    return found


def _slug(label: str) -> str:
    return re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", label.lower())) or "mark"


def _unique(label: str, used: set) -> str:
    base = _slug(label)
    if base not in used:
        used.add(base)
        return base
    n = 2
    while f"{base}-{n}" in used:
        n += 1
    ident = f"{base}-{n}"
    used.add(ident)
    return ident


def _step(span: float) -> float:
    if span <= 2:
        return 0.5
    if span <= 15:
        return 2
    if span <= 80:
        return 10
    pow10 = 10 ** math.floor(math.log10(span))
    return pow10 / 2 if span / pow10 <= 2 else pow10


def _frame(values: list) -> dict:
    min_v = min([*values, 0])
    max_v = max([*values, 0])
    span = max(max_v - min_v, 0.5)
    step = _step(span)
    lo = _round(math.floor((min_v - (span * 0.08 if min_v < 0 else 0)) / step) * step)
    padded = 0 if max_v == 0 else max_v + span * 0.08
    hi = _round(math.ceil(padded / step) * step)
    if hi <= max_v and max_v != 0:
        hi = _round(hi + step)
    if hi == lo:
        hi = _round(lo + step)
    ticks = []
    tick = lo
    while tick <= hi + step * 0.001:
        ticks.append(_num(_round(tick)))
        tick = _round(tick + step)
    return {"axis": [_num(lo), _num(hi)], "ticks": ticks}


def _money_bn(amount: float, suffix: str) -> float:
    return _round(amount / 1000) if suffix.lower() in {"mn", "m", "million"} else amount


def _money_unit(symbol: str) -> str:
    return "€bn" if symbol == "€" else "£bn" if symbol == "£" else "$bn"


def _cap(word: str) -> str:
    return word[:1].upper() + word[1:].lower()


def _clause_start(text: str, index: int, sentence_start: int) -> int:
    prior = text[sentence_start:index]
    cuts = [prior.rfind(","), prior.rfind(";"), prior.lower().rfind(" and ")]
    cut = max(cuts)
    return sentence_start + cut + 1 if cut >= 0 else sentence_start


def _money_label(clause: str) -> str:
    issuer = ISSUER.search(clause)
    if issuer:
        return _cap(issuer.group(1))
    kept = []
    for word in re.sub(r"[()]", " ", clause).split():
        bare = re.sub(r"[^A-Za-z]", "", word)
        if not bare:
            continue
        if re.fullmatch(r"bought|added|issued|was|were|reached|led|sits|is|grew|at|of|the|in|by|to|from", bare, re.I):
            if kept:
                break
            continue
        if bare[:1].isupper() or kept:
            kept.append(bare)
        if len(kept) == 3:
            break
    return " ".join(kept)


def _percent_label(text: str, sentences: list, index: int) -> str:
    sentence = _sentence_at(sentences, index)
    if sentence is None:
        return ""
    clause = text[_clause_start(text, index, sentence["start"]):index]
    tenor = re.search(r"(\d+)\s*-\s*year", clause, re.I)
    if tenor:
        return f"{tenor.group(1)}-year"
    position = sentences.index(sentence)
    previous = sentences[position - 1] if position else None
    pronoun = re.match(r"^(Its|It|That|This)\b", sentence["text"]) is not None
    name = _last_name(previous["text"]) if pronoun and previous else _last_name(clause) or _last_name(sentence["text"])
    if not name:
        return ""
    if re.search(r"\bHY\b", sentence["text"]):
        return f"{name} HY"
    if re.search(r"\bVirginia\b", sentence["text"]):
        return f"{name} VA"
    return name


def _dedupe(items: list) -> list:
    seen, kept = set(), []
    for item in items:
        key = (
            (item["unit"], item["role"], item["label"].lower(), item["low"], item["high"], item.get("time", ""), item.get("window", ""))
            if item.get("time") or item.get("window")
            else (item["unit"], item["role"], item["low"], item["high"])
        )
        if key in seen:
            continue
        seen.add(key)
        kept.append(item)
    return kept


def _drop_total(items: list) -> list:
    if len(items) < 3:
        return items
    for index, item in enumerate(items):
        rest = [other for other_index, other in enumerate(items) if other_index != index]
        total = sum(other["low"] for other in rest)
        if abs(total - item["low"]) <= max(0.02, abs(item["low"]) * 0.02):
            return rest
    return items


def _extract(text: str) -> list:
    sentences = _sentences(text)
    consumed, found = [], []

    def take(start, end):
        if _overlaps(consumed, start, end):
            return False
        consumed.append((start, end))
        return True

    grew = re.compile(
        r"grew from\s+~?\s*([€$£])\s*(\d+(?:\.\d+)?)\s*(bn|mn|million|billion)\s+in\s+([A-Z][a-z]+\s+\d{4})\s+to\s+~?\s*([€$£])\s*(\d+(?:\.\d+)?)\s*(bn|mn|million|billion)\s+in\s+([A-Z][a-z]+\s+\d{4})",
        re.I,
    )
    for match in grew.finditer(text):
        if not take(match.start(), match.end()):
            continue
        before = text[max(0, match.start() - 80):match.start()].lower()
        words = re.findall(r"\b(tokenized|assets|equities|credit|debt|issuance)\b", before)
        if "tokenized" not in words or "assets" not in words:
            continue
        unit = _money_unit(match.group(1))
        found.append({"label": "Tokenized assets", "unit": unit, "role": "level", "low": _money_bn(float(match.group(2)), match.group(3)), "high": _money_bn(float(match.group(2)), match.group(3)), "at": match.start(), "time": match.group(4)})
        found.append({"label": "Tokenized assets", "unit": unit, "role": "level", "low": _money_bn(float(match.group(6)), match.group(7)), "high": _money_bn(float(match.group(6)), match.group(7)), "at": match.start(), "time": match.group(8)})

    for match in re.finditer(r"low\s*-?\s*to\s*mid\s*-?\s*(\d+(?:\.\d+)?)%", text, re.I):
        if not take(match.start(), match.end()):
            continue
        label = _percent_label(text, sentences, match.start())
        if not label:
            continue
        low = float(match.group(1))
        found.append({"label": label, "unit": "%", "role": "level", "low": low, "high": _round(low + 0.5), "at": match.start(), "estimated": True})

    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)%", text):
        if not take(match.start(), match.end()):
            continue
        label = _percent_label(text, sentences, match.start())
        if not label:
            continue
        sentence = _sentence_at(sentences, match.start())
        detail = re.search(r"\$(\d+(?:\.\d+)?)bn", sentence["text"], re.I) if sentence else None
        found.append({"label": label, "unit": "%", "role": "level", "low": float(match.group(1)), "high": float(match.group(2)), "at": match.start(), "detail": f"${detail.group(1)}bn" if detail else ""})

    for match in re.finditer(r"\b(?:up|down|rose|fell|higher|lower)\s+~?\s*(\d+(?:\.\d+)?)%", text, re.I):
        if take(match.start(), match.end()):
            found.append({"label": "Change", "unit": "%", "role": "change", "low": float(match.group(1)), "high": float(match.group(1)), "at": match.start()})

    for match in re.finditer(r"(\d+(?:\.\d+)?)%\s+(?:of|above|below)\b", text, re.I):
        take(match.start(), match.end())
    for match in re.finditer(r"near\s*-?\s*(\d+(?:\.\d+)?)%", text, re.I):
        take(match.start(), match.end())
    for match in re.finditer(r"(\d+(?:\.\d+)?)%\s*chance\b", text, re.I):
        take(match.start(), match.end())

    for match in re.finditer(r"~?(\d+(?:\.\d+)?)%", text):
        if not take(match.start(), match.end()):
            continue
        label = _percent_label(text, sentences, match.start())
        if not label:
            continue
        value = float(match.group(1))
        found.append({"label": label, "unit": "%", "role": "level", "low": value, "high": value, "at": match.start()})

    for match in re.finditer(r"~?\s*([€$£])\s*(\d+(?:\.\d+)?)\s*(bn|mn|million|billion)\b", text, re.I):
        if not take(match.start(), match.end()):
            continue
        sentence = _sentence_at(sentences, match.start())
        if sentence and re.search(r"\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?%|low\s*-?\s*to\s*mid", sentence["text"], re.I):
            continue
        label = _money_label(text[_clause_start(text, match.start(), sentence["start"] if sentence else 0):match.start()])
        if not label:
            continue
        value = _money_bn(float(match.group(2)), match.group(3))
        found.append({"label": label, "unit": _money_unit(match.group(1)), "role": "level", "low": value, "high": value, "at": match.start()})

    for match in re.finditer(r"~?(\d+(?:\.\d+)?)\s*bps?\b", text, re.I):
        if not take(match.start(), match.end()):
            continue
        after = text[match.end():match.end() + 48]
        window = "2 weeks" if re.search(r"two weeks|2 weeks", after, re.I) else "1 month" if re.search(r"past month|one month|1 month|\ba month\b", after, re.I) else ""
        if not window:
            continue
        tenor = re.search(r"(\d+)\s*-\s*year", text[max(0, match.start() - 80):match.start()], re.I)
        found.append({"label": window, "unit": "bp", "role": "change", "low": float(match.group(1)), "high": float(match.group(1)), "at": match.start(), "window": window, "detail": f"{tenor.group(1)}-year" if tenor else ""})

    for match in re.finditer(r"([+-]?\d+(?:\.\d+)?)z\b", text, re.I):
        if not take(match.start(), match.end()):
            continue
        before = text[max(0, match.start() - 18):match.start()]
        label = "After" if re.search(r"after\s*[-~(]?\s*$", before, re.I) else "MoM" if re.search(r"MoM\s*$", before) else "Week" if re.search(r"\bweek\b", before, re.I) else ""
        if not label:
            continue
        sentence = _sentence_at(sentences, match.start())
        subject = re.search(r"\b(OATs?|Bunds?|WTI|NDX)\b", sentence["text"]) if sentence else None
        found.append({"label": label, "unit": "z", "role": "change", "low": float(match.group(1)), "high": float(match.group(1)), "at": match.start(), "window": label, "detail": subject.group(1) if subject else ""})

    for match in re.finditer(r"\bduration is\s+(\d+(?:\.\d+)?)\s+years\b", text, re.I):
        if not take(match.start(), match.end()):
            continue
        sentence = _sentence_at(sentences, match.start())
        label = _percent_label(text, sentences, match.start()) or _money_label(text[(sentence["start"] if sentence else 0):match.start()])
        if not label:
            continue
        found.append({"label": label, "unit": "years", "role": "level", "low": float(match.group(1)), "high": float(match.group(1)), "at": match.start()})
    return _dedupe(found)


def _reference(text: str):
    match = re.search(r"near\s*-?\s*(\d+(?:\.\d+)?)%", text, re.I)
    return {"value": _num(float(match.group(1))), "label": f"near {match.group(1)}%"} if match else None


def _build(text: str) -> list:
    all_obs = _extract(text)
    used, ids, plans = set(), set(), []
    free = lambda: [item for item in all_obs if id(item) not in used]

    def consume(items):
        for item in items:
            used.add(id(item))

    lines = {}
    for item in free():
        if not item.get("time"):
            continue
        lines.setdefault(f"{item['label']}|{item['unit']}", []).append(item)
    for items in lines.values():
        if len(items) < 2:
            continue
        ordered = sorted(items, key=lambda item: item["at"])
        scale = _frame([item["low"] for item in ordered])
        plans.append({"kind": "line", "title": ordered[0]["label"], "unit": ordered[0]["unit"], **scale,
                      "points": [{"id": _unique(item.get("time") or item["label"], ids), "label": item.get("time") or item["label"], "value": _num(item["low"])} for item in ordered]})
        consume(ordered)

    by_label = {}
    for item in free():
        by_label.setdefault(item["label"], []).append(item)
    scatter = [(label, items) for label, items in by_label.items() if len({item["unit"] for item in items}) >= 2]
    if len(scatter) >= 2:
        units = list(dict.fromkeys(item["unit"] for item in scatter[0][1]))
        pair = ["years", "%"] if "years" in units and "%" in units else units[:2]
        ready = [(label, items) for label, items in scatter if all(any(item["unit"] == unit for item in items) for unit in pair)]
        if len(ready) >= 2 and pair[0] != pair[1]:
            points = []
            for label, items in ready:
                x = next(item for item in items if item["unit"] == pair[0])
                y = next(item for item in items if item["unit"] == pair[1])
                points.append({"label": label, "x": x["low"], "y": y["low"], "obs": [x, y]})
            x_scale, y_scale = _frame([point["x"] for point in points]), _frame([point["y"] for point in points])
            plans.append({"kind": "scatter", "title": f"{'Yield' if pair[1] == '%' else pair[1]} vs {pair[0]}",
                          "xUnit": pair[0], "yUnit": pair[1], "xAxis": x_scale["axis"], "yAxis": y_scale["axis"],
                          "xTicks": x_scale["ticks"], "yTicks": y_scale["ticks"],
                          "points": [{"id": _unique(point["label"], ids), "label": point["label"], "x": _num(point["x"]), "y": _num(point["y"])} for point in points]})
            consume([obs for point in points for obs in point["obs"]])

    levels = [item for item in free() if item["unit"] == "%" and item["role"] == "level"]
    if any(item["low"] != item["high"] for item in levels) or len(levels) >= 2:
        marks = sorted(levels, key=lambda item: (-item["high"], -item["low"], item["label"]))
        reference = _reference(text)
        scale = _frame([*[mark["high"] for mark in marks], *([reference["value"]] if reference else [])])
        plan = {"kind": "range", "title": "Dollar yields" if re.search(r"dollar yield", text, re.I) else "Yields", "unit": "%", **scale,
                "marks": [{"id": _unique(mark["label"], ids), "label": mark["label"], "detail": mark.get("detail") or "",
                           "low": _num(mark["low"]), "high": _num(mark["high"]), "estimated": bool(mark.get("estimated"))} for mark in marks]}
        if reference:
            plan["reference"] = reference
        if any(mark.get("estimated") for mark in marks):
            plan["sourceNote"] = "Desk band places the printed phrase on the axis. Not a printed coupon."
        plans.append(plan)
        consume(marks)

    groups = {}
    for item in free():
        if item["role"] == "change" and item["unit"] == "%" and not item.get("window"):
            continue
        groups.setdefault(f"{item['unit']}|{item.get('detail') or ''}|{item['role']}", []).append(item)
    for key, items in groups.items():
        peers = _drop_total(items) if key[:1] in "€$£" else items
        if len(peers) < 2:
            continue
        ordered = sorted(peers, key=lambda item: item["at"]) if any(item.get("window") for item in peers) else sorted(peers, key=lambda item: (-item["low"], item["label"]))
        scale = _frame([item["low"] for item in ordered])
        unit = ordered[0]["unit"]
        tenor = next((item.get("detail") for item in ordered if item["unit"] == "bp" and item.get("detail")), "")
        subject = next((item.get("detail") for item in ordered if item["unit"] == "z" and item.get("detail")), "")
        title = f"{tenor} change" if unit == "bp" and tenor else "Change" if unit == "bp" else subject or "Positioning" if unit == "z" else "Issuance" if re.search(r"issuance", text, re.I) else "Amounts"
        plan = {"kind": "bar", "title": title, "unit": unit, **scale,
                "bars": [{"id": _unique(item["label"], ids), "label": item["label"], "value": _num(item["low"])} for item in ordered]}
        windows = {item.get("window") for item in ordered}
        if "2 weeks" in windows and "1 month" in windows:
            plan["note"] = "2-week window sits inside the month. Not additive."
        plans.append(plan)
        consume(ordered)

    def earliest(plan_item):
        labels = [mark["label"] for mark in plan_item["marks"]] if plan_item["kind"] == "range" else [row["label"] for row in plan_item.get("bars", plan_item.get("points", []))]
        ats = [next((item["at"] for item in all_obs if item["label"] == label or item.get("time") == label or item.get("window") == label), 10**12) for label in labels]
        return min(ats) if ats else 0

    return sorted(plans, key=earliest)


def _close(left, right) -> bool:
    return abs(left - right) <= 0.0005


def _hits(sentence: str, unit: str) -> list:
    hits = []
    if unit == "%":
        for match in re.finditer(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)%", sentence):
            hits.append({"index": match.start(), "end": match.end(), "values": [float(match.group(1)), float(match.group(2))]})
        for match in re.finditer(r"(?<![\d.-])(\d+(?:\.\d+)?)%", sentence):
            if any(hit["index"] <= match.start() < hit["end"] for hit in hits):
                continue
            hits.append({"index": match.start(), "end": match.end(), "values": [float(match.group(1))]})
    elif unit == "bp":
        for match in re.finditer(r"(\d+(?:\.\d+)?)\s*bps?\b", sentence, re.I):
            hits.append({"index": match.start(), "end": match.end(), "values": [float(match.group(1))]})
    elif unit == "z":
        for match in re.finditer(r"([+-]?\d+(?:\.\d+)?)z\b", sentence, re.I):
            hits.append({"index": match.start(), "end": match.end(), "values": [float(match.group(1))]})
    elif unit == "years":
        for match in re.finditer(r"(\d+(?:\.\d+)?)\s+years\b", sentence, re.I):
            hits.append({"index": match.start(), "end": match.end(), "values": [float(match.group(1))]})
    else:
        symbol = "€" if unit.startswith("€") else "£" if unit.startswith("£") else r"\$"
        for match in re.finditer(symbol + r"\s*(\d+(?:\.\d+)?)\s*(bn|mn|million|billion)\b", sentence, re.I):
            hits.append({"index": match.start(), "end": match.end(), "values": [_money_bn(float(match.group(1)), match.group(2))]})
    return sorted(hits, key=lambda hit: hit["index"])


def _words(label: str) -> list:
    return ["virginia" if word == "VA" else word.lower() for word in label.split() if word]


def _grounded(text: str, label: str, value: float, unit: str, estimated: bool = False) -> bool:
    sentences = _sentences(text)
    words = _words(label)
    for index, sentence in enumerate(sentences):
        previous = f"{sentences[index - 1]['text']} " if re.match(r"^(Its|It|That|This)\b", sentence["text"]) and index else ""
        if estimated:
            if re.search(r"low\s*-?\s*to\s*mid\s*-?\s*\d+(?:\.\d+)?%", sentence["text"], re.I) and all(word in f"{previous}{sentence['text']}".lower() for word in words):
                return True
            continue
        for hit in [item for item in _hits(sentence["text"], unit) if any(_close(item_value, value) for item_value in item["values"])]:
            after = sentence["text"][hit["index"]:hit["end"] + 48]
            if label == "2 weeks":
                if re.search(r"two weeks|2 weeks", after, re.I):
                    return True
                continue
            if label == "1 month":
                if re.search(r"past month|one month|1 month|\ba month\b", after, re.I):
                    return True
                continue
            if label in {"After", "MoM", "Week"}:
                before = sentence["text"][max(0, hit["index"] - 18):hit["index"]]
                if label == "After" and re.search(r"after\s*[-~(]?\s*$", before, re.I):
                    return True
                if label == "MoM" and re.search(r"MoM\s*$", before):
                    return True
                if label == "Week" and re.search(r"\bweek\b", before, re.I):
                    return True
                continue
            if re.fullmatch(r"[A-Z][a-z]{2}\s+\d{4}", label):
                if label in after:
                    return True
                continue
            all_hits = _hits(sentence["text"], unit)
            earlier = [item for item in all_hits if item["index"] < hit["index"]]
            local = sentence["text"][(earlier[-1]["end"] if earlier else 0):hit["index"]]
            cuts = [local.rfind(","), local.rfind(";"), local.lower().rfind(" and ")]
            cut = max(cuts)
            gap = f"{previous}{sentence['text']}" if len(all_hits) == 1 else f"{previous}{local[cut + 1:] if cut >= 0 else local}"
            if all(word in gap.lower() for word in words):
                return True
    return False


def _verify(plan_item: dict, text: str) -> bool:
    kind = plan_item["kind"]
    if kind == "line":
        return len(plan_item["points"]) >= 2 and all(_grounded(text, point["label"], point["value"], plan_item["unit"]) for point in plan_item["points"])
    if kind == "bar":
        return len(plan_item["bars"]) >= 2 and all(_grounded(text, row["label"], row["value"], plan_item["unit"]) for row in plan_item["bars"])
    if kind == "range":
        marks = plan_item["marks"]
        if not any(mark["low"] != mark["high"] for mark in marks) and len(marks) < 2:
            return False
        for mark in marks:
            if mark["estimated"]:
                if not _grounded(text, mark["label"], mark["low"], plan_item["unit"], True):
                    return False
            elif not (_grounded(text, mark["label"], mark["low"], "%") and (mark["low"] == mark["high"] or _grounded(text, mark["label"], mark["high"], "%"))):
                return False
        return True
    if kind == "scatter":
        return len(plan_item["points"]) >= 2 and all(
            _grounded(text, point["label"], point["x"], plan_item["xUnit"]) and _grounded(text, point["label"], point["y"], plan_item["yUnit"])
            for point in plan_item["points"]
        )
    return False
