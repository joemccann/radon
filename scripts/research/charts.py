"""Chart plans for text-only research findings.

Mirrors web/lib/planCharts.ts. A lone point, a lone basis-point change, a
dollar amount, or a multiple is not a series. A printed range is.
"""
from __future__ import annotations

import math
import re

STOP = {
    "The", "Its", "It", "But", "That", "Earlier", "Credit", "Markets", "Rates",
    "This", "An", "US", "HY", "AI", "GS", "PMI", "Fed", "OpenAI", "September",
    "Virginia", "Chart", "Source", "Official", "Foreign", "Net", "August", "July",
    "June", "May", "April", "March", "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday", "In", "Of", "And", "For", "To", "On", "By",
    "At", "From", "With", "After", "Before", "During", "About", "Over", "Under",
    "Near", "Above", "Below", "Data", "Centre", "Center", "Financing", "Investors",
    "Investor", "Treasury", "Treasuries", "Research", "Global", "Investment",
    "Bank", "Note", "Desk", "Week", "Month", "Year", "End",
}


def plan(raw: str) -> list:
    text = re.sub(r"[ \t]+", " ", (raw or "").replace("–", "-").replace("—", "-")).strip()
    if not text:
        return []
    sentences = _sentences(text)
    used: set[str] = set()
    consumed: list[tuple[int, int]] = []
    marks: list[dict] = []
    reference = None

    for match in re.finditer(r"low\s*-?\s*to\s*mid\s*-?\s*(\d+(?:\.\d+)?)%", text, re.I):
        consumed.append((match.start(), match.end()))
        low = float(match.group(1))
        named = _label_at(text, match.start(), sentences)
        marks.append({
            "id": _unique_id(named["label"], used), "label": named["label"], "detail": "desk band",
            "low": _num(low), "high": _num(low + 0.5), "kind": "desk-band",
        })

    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)%", text):
        if _overlaps(consumed, match.start(), match.end()):
            continue
        consumed.append((match.start(), match.end()))
        named = _label_at(text, match.start(), sentences)
        marks.append({
            "id": _unique_id(named["label"], used), "label": named["label"], "detail": named["detail"],
            "low": _num(float(match.group(1))), "high": _num(float(match.group(2))), "kind": "printed-range",
        })

    for match in re.finditer(r"near\s*-?\s*(\d+(?:\.\d+)?)%", text, re.I):
        if _overlaps(consumed, match.start(), match.end()):
            continue
        consumed.append((match.start(), match.end()))
        if reference is None:
            value = _num(float(match.group(1)))
            reference = {"value": value, "label": f"near {_plain(value)}%"}

    for match in re.finditer(r"~?(\d+(?:\.\d+)?)%(?!\s*chance)", text):
        if _overlaps(consumed, match.start(), match.end()):
            continue
        consumed.append((match.start(), match.end()))
        value = _num(float(match.group(1)))
        named = _label_at(text, match.start(), sentences)
        marks.append({
            "id": _unique_id(named["label"], used), "label": named["label"],
            "detail": named["detail"] or "spot", "low": value, "high": value, "kind": "point",
        })

    bars: list[dict] = []
    readout = None
    for match in re.finditer(r"~?(\d+(?:\.\d+)?)\s*bps?\b", text, re.I):
        around = text[max(0, match.start() - 24):match.end() + 48]
        bp = _num(float(match.group(1)))
        if re.search(r"tightening|year\s*-?\s*end", around, re.I):
            if readout is None:
                readout = {"label": "Year-end tightening", "bp": bp}
            continue
        after = text[match.end():match.end() + 48]
        label = None
        if re.search(r"two weeks|2 weeks", after, re.I):
            label = "2 weeks"
        elif re.search(r"past month|one month|1 month|\ba month\b", after, re.I):
            label = "1 month"
        if label:
            bars.append({"id": _unique_id(label, used), "label": label, "bp": bp})

    probability = None
    prob = re.search(
        r"(\d+(?:\.\d+)?)%\s*chance(?:\s+of(?:\s+an?)?)?(?:\s+([A-Z][a-z]+)(?:\s+([a-z]+))?)?",
        text,
    )
    if prob:
        tail = f" {prob.group(3)}" if prob.group(3) and prob.group(3) not in {"and", "or", "of", "by", "the", "a", "to", "with"} else ""
        probability = {"label": f"{prob.group(2)}{tail}" if prob.group(2) else "Probability", "pct": _num(float(prob.group(1)))}

    plans: list[dict] = []
    ranges = [mark for mark in marks if mark["kind"] != "point"]
    points = [mark for mark in marks if mark["kind"] == "point"]
    if ranges or len(points) >= 2:
        kept = sorted(marks, key=lambda mark: (-mark["high"], -mark["low"], mark["label"]))
        peak = max([mark["high"] for mark in kept] + ([reference["value"]] if reference else [0]))
        axis = _levels_ceiling(peak)
        plan_levels = {"kind": "levels", "title": "Dollar yields" if re.search(r"yield", text, re.I) else "Levels", **axis, "marks": kept}
        dek = _dek(text)
        if dek:
            plan_levels["dek"] = dek
        if reference:
            plan_levels["reference"] = reference
        if any(mark["kind"] == "desk-band" for mark in kept):
            plan_levels["sourceNote"] = "Desk band places the printed phrase on the axis. Not a printed coupon."
        plans.append(plan_levels)

    plotted = [] if probability and len(bars) < 2 else bars if len(bars) >= 2 else []
    if len(plotted) >= 2 or probability:
        plotted = sorted(plotted, key=lambda bar: (-bar["bp"], bar["label"]))
        subject = "10-year" if plotted and re.search(r"\b10\s*-\s*year\b", text, re.I) else "2-year" if plotted and re.search(r"\b2\s*-\s*year\b", text, re.I) else None
        bar_aria = f"{subject} change in basis points" if subject else "Change in basis points"
        if probability and plotted:
            title = f"{subject} move and the {probability['label']}" if subject else probability["label"]
        elif plotted:
            title = f"{subject} change" if subject else "Basis-point change"
        else:
            title = probability["label"] if probability else "Rates"
        ceiling = _bar_ceiling(max(bar["bp"] for bar in plotted)) if plotted else {"axisMax": 0, "ticks": [0]}
        plan_move = {"kind": "move", "title": title, "bars": plotted, **ceiling, "barAria": bar_aria}
        if probability:
            plan_move["probability"] = probability
        if readout and (len(plotted) >= 2 or probability):
            plan_move["readout"] = readout
        windows = {bar["label"] for bar in plotted}
        if "2 weeks" in windows and "1 month" in windows:
            plan_move["note"] = "2-week window sits inside the month. Not additive."
        plans.append(plan_move)
    return plans


def _sentences(text: str) -> list[dict]:
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


def _overlaps(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(start < stop and end > begin for begin, stop in spans)


def _last_name(text: str) -> str | None:
    found = None
    for match in re.finditer(r"\b([A-Z][A-Za-z0-9]+)(?:['’]s)?(?:\s+([A-Z][A-Za-z0-9]*))?", text):
        first, second = match.group(1), match.group(2)
        if first in STOP:
            continue
        found = f"{first} {second}" if second and second not in STOP else first
    return found


def _label_at(text: str, index: int, sentences: list[dict]) -> dict:
    sentence = next((item for item in sentences if item["start"] <= index < item["end"]), None)
    if sentence is None:
        return {"label": "Level", "detail": ""}
    position = sentences.index(sentence)
    previous = sentences[position - 1] if position else None
    pronoun = re.match(r"^(Its|It|That|This|But)\b", sentence["text"]) is not None
    name_source = f"{previous['text']} {sentence['text']}" if pronoun and previous else sentence["text"]
    before = text[sentence["start"]:index]
    previous_break = max(before.rfind("%"), before.rfind("bp"))
    lookback = before[previous_break + 1:] if previous_break >= 0 else before
    tenor = re.search(r"(\d+)\s*-\s*year", lookback, re.I)
    if tenor:
        return {"label": f"US {tenor.group(1)}-year", "detail": "spot"}
    name = _last_name(name_source)
    label = name or "Level"
    if name and re.search(r"\bHY\b", sentence["text"]):
        label = f"{name} HY"
    elif name and re.search(r"\bVirginia\b", sentence["text"]):
        label = f"{name} VA"
    money = re.search(r"\$(\d+(?:\.\d+)?)bn", sentence["text"], re.I)
    return {"label": label, "detail": f"${money.group(1)}bn" if money else ""}


def _slug(label: str) -> str:
    return re.sub(r"^-+|-+$", "", re.sub(r"[^a-z0-9]+", "-", label.lower())) or "mark"


def _unique_id(label: str, used: set[str]) -> str:
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


def _num(value: float) -> int | float:
    return int(value) if value == int(value) else value


def _plain(value: float) -> str:
    return str(int(value)) if value == int(value) else str(value)


def _levels_ceiling(peak: float) -> dict:
    step = 2 if peak <= 12 else 5 if peak <= 40 else 10
    ceiling = max(step, math.ceil((peak * 1.2) / step) * step)
    return {"axis": [0, ceiling], "ticks": list(range(0, ceiling + 1, step))}


def _bar_ceiling(peak: float) -> dict:
    rounded = math.ceil(peak / 10) * 10
    axis_max = rounded + 10 if rounded == peak else rounded
    return {"axisMax": axis_max, "ticks": list(range(0, axis_max + 1, 10))}


def _dek(text: str) -> str | None:
    multiple = re.search(r"S&P[^.]{0,80}?~?\s*(\d+(?:\.\d+)?)x", text, re.I)
    return f"S&P near {multiple.group(1)}x." if multiple else None
