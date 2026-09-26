"""NVIDIA Nemotron Parse v1 on top of local pdf-inspector.

Local pdf-inspector runs on every page and stays the grounding text for
verbatim number gates. nvidia/nemotron-parse (v1) is called only on pages
that need it: no or garbled text layer, a large raster image, or a
figures.py candidate. Its text is fenced as image-derived. Numbers that
appear only there do not satisfy the verbatim gate.

nemotron-parse-2.0 is not in the default ladder. Set
RADON_RESEARCH_PARSE_MODELS to opt in. RADON_RESEARCH_PARSE_SELECTIVE=0
sends every page.

A page stays local-only on 429, 5xx, DEGRADED, timeout, empty content,
finish_reason=length, duplicated blocks, or table-number agreement below
RADON_RESEARCH_PARSE_TABLE_AGREE (default 0.90) when the page has a text
layer. After RADON_RESEARCH_PARSE_FAIL_LIMIT consecutive failures (default
3) Nemotron is parked for the provider cooldown and later pages stay local.
The per-document budget (RADON_RESEARCH_PARSE_BUDGET_S, default 100s) stops
further calls without blocking the local extract. One call per selected page.
"""
from __future__ import annotations

import base64
import io
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

_V2_PROMPT = "</s><s><predict_bbox><predict_classes><output_markdown><predict_text_in_pic>"
_CLASS_RE = re.compile(r"<class_([A-Za-z0-9_-]+)>")
_LEADING_COORDS = re.compile(
    r"(?:<(?:[xy]_)?(-?\d+(?:\.\d+)?)>\s*){4}"
)
_BBOX_TAG = re.compile(
    r"<bbox>\s*([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s+([0-9.eE+-]+)\s*</bbox>\s*"
)
_NUMERIC = re.compile(
    r"(?<![\w.])[-+]?\d{1,3}(?:,\d{3})*(?:\.\d+)?%?|(?<![\w.])[-+]?\d+(?:\.\d+)?%?"
)
_TABULAR = re.compile(r"\\begin\{tabular\}(?:\{[^}]*\})?(.*)\\end\{tabular\}", re.S)
_LATEX_CMD = re.compile(r"\\[a-zA-Z]+")
_NOISE = re.compile(r"</?s>|<predict_[a-z_]+>|<output_markdown>|<\|[^>]*\|>")
_IMAGE_FENCE = re.compile(
    r"<!--\s*image-derived\s*-->.*?<!--\s*/image-derived\s*-->",
    re.S,
)
_FENCE_OPEN = "<!-- image-derived -->"
_FENCE_CLOSE = "<!-- /image-derived -->"

DEFAULT_MODELS = ("nvidia/nemotron-parse",)
NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
_PICTURE_KINDS = {"chart", "picture", "image"}
_TITLE_KINDS = {"title", "section-header", "header", "section_header"}
_BREAKER = {"degraded", "empty", "length", "timeout", "network", "malformed", "duplicated"}
_LONG_DUP = 40

_CIRCUIT = {"failures": 0, "park_until": 0.0}
_CIRCUIT_LOCK = threading.Lock()


class PageParseError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def reset_parse_circuit() -> None:
    with _CIRCUIT_LOCK:
        _CIRCUIT["failures"] = 0
        _CIRCUIT["park_until"] = 0.0


def parser_mode(env: dict | None = None) -> str:
    """nemotron when asked, or when NVIDIA_API_KEY is set and the mode is unset."""
    source = os.environ if env is None else env
    explicit = (source.get("RADON_RESEARCH_PARSER") or "").strip().lower()
    if explicit == "nemotron":
        return "nemotron"
    if explicit:
        return "local"
    if (source.get("NVIDIA_API_KEY") or "").strip():
        return "nemotron"
    return "local"


def parse_models() -> tuple[str, ...]:
    raw = os.environ.get("RADON_RESEARCH_PARSE_MODELS", "")
    models = tuple(part.strip() for part in raw.split(",") if part.strip())
    return models or DEFAULT_MODELS


def numeric_tokens(text: str) -> set[str]:
    found = set()
    for match in _NUMERIC.finditer(text or ""):
        token = match.group(0).replace(",", "")
        if token not in {"-", "+"}:
            found.add(token)
    return found


def grounding_text(text: str) -> str:
    """Page text with image-derived Nemotron spans removed."""
    return _IMAGE_FENCE.sub("", text or "")


def image_derived_numbers(local_text: str, other_text: str) -> list[str]:
    return sorted(numeric_tokens(other_text) - numeric_tokens(local_text))


def compose_markdown(local_md: str, nemotron_md: str) -> str:
    extra = (nemotron_md or "").strip()
    base = local_md or ""
    if not extra:
        return base
    spacer = "\n\n" if base.strip() else ""
    return base.rstrip() + spacer + _FENCE_OPEN + "\n" + extra + "\n" + _FENCE_CLOSE + "\n"


def table_agreement(local_text: str, blocks: list) -> float | None:
    """Share of Nemotron table numbers that also appear in the local text."""
    tokens: set[str] = set()
    for block in blocks or []:
        kind = str(block.get("type") or "").lower()
        text = str(block.get("text") or "")
        if kind != "table" and "\\begin{tabular}" not in text:
            continue
        tokens |= numeric_tokens(latex_table_to_markdown(text))
    if not tokens:
        return None
    local = numeric_tokens(local_text)
    return len(tokens & local) / len(tokens)


def prepare_blocks(blocks: list) -> tuple[list, str | None]:
    """Reading order, drop zero-area pictures, drop exact dupes. Long dupes fail the page."""
    ordered = sorted(blocks or [], key=_bbox_key)
    cleaned = []
    seen = set()
    counts: dict[str, int] = {}
    duplicated = False
    for block in ordered:
        kind = str(block.get("type") or "Text")
        norm = kind.lower().replace("_", "-")
        bbox = block.get("bbox")
        if norm in _PICTURE_KINDS and _area(bbox) <= 0:
            continue
        text = " ".join(str(block.get("text") or "").split())
        ident = (norm, text, _bbox_tuple(bbox))
        if ident in seen:
            continue
        seen.add(ident)
        if len(text) >= _LONG_DUP:
            counts[text] = counts.get(text, 0) + 1
            if counts[text] > 1:
                duplicated = True
        cleaned.append(block)
    if duplicated:
        return cleaned, "duplicated"
    return cleaned, None


def chart_candidates(blocks: list, page_number: int) -> list:
    """Picture and chart boxes with area, in reading order. figures.py stays the other source."""
    found = []
    for block in blocks or []:
        kind = str(block.get("type") or "").lower().replace("_", "-")
        if kind not in _PICTURE_KINDS:
            continue
        bbox = block.get("bbox")
        if _area(bbox) <= 0:
            continue
        box = [float(bbox[key]) for key in ("xmin", "ymin", "xmax", "ymax")]
        found.append({
            "page": page_number,
            "bbox": [round(max(0.0, min(1.0, value)), 4) for value in box],
            "objects": 1,
            "kind": "raster",
            "title": None,
            "source_line": None,
            "origin": "nemotron",
        })
    return found


def page_recall(local_text: str, other_text: str) -> dict:
    local = numeric_tokens(grounding_text(local_text))
    other = numeric_tokens(grounding_text(other_text))
    overlap = local & other
    recall = (len(overlap) / len(local)) if local else 1.0
    return {
        "local_count": len(local),
        "nemotron_count": len(other),
        "overlap": len(overlap),
        "recall": recall,
    }


def latex_table_to_markdown(text: str) -> str:
    """Turn a LaTeX tabular into a Markdown table. Unparseable input is returned."""
    match = _TABULAR.search(text or "")
    body = match.group(1) if match else (text or "")
    body = re.sub(r"\\(?:hline|cline\{[^}]*\})", "", body)
    rows = []
    for raw in re.split(r"\\\\", body):
        line = raw.strip()
        if not line or line.startswith("\\begin") or line.startswith("\\end"):
            continue
        cells = [_latex_cell(cell) for cell in line.split("&")]
        if any(cells):
            rows.append(cells)
    if len(rows) < 1 or (match is None and "&" not in (text or "")):
        return text
    width = max(len(row) for row in rows)

    def fmt(row: list[str]) -> str:
        padded = row + [""] * (width - len(row))
        return "| " + " | ".join(padded) + " |"

    lines = [fmt(rows[0]), "| " + " | ".join(["---"] * width) + " |"]
    lines.extend(fmt(row) for row in rows[1:])
    return "\n".join(lines)


def parse_document(pdf_path, output, *, post=None, sleep=None, monotonic=None, jitter=None):
    from research.pdf import parse_local, write_evidence

    local = parse_local(pdf_path)
    if parser_mode() != "nemotron":
        _stamp_local(local)
        return write_evidence(output, local)
    if post is None and not os.environ.get("NVIDIA_API_KEY", "").strip():
        local["fallback_reason"] = "missing_api_key"
        local["fallback_attempts"] = []
        _stamp_local(local)
        return write_evidence(output, local)
    try:
        _run_nemotron(
            pdf_path, local, post=post, sleep=sleep, monotonic=monotonic, jitter=jitter,
        )
    except Exception as exc:
        from clients.model_ladder import safe_error_message
        local["fallback_reason"] = safe_error_message(exc)
        local["fallback_attempts"] = []
        _stamp_local(local)
    return write_evidence(output, local)


def _run_nemotron(pdf_path, local, *, post, sleep, monotonic, jitter):
    selected = _selected_pages(pdf_path, local)
    now = _clock(monotonic)
    if not selected or _parked(now):
        reason = "circuit" if selected and _parked(now) else None
        _stamp_local(local, reason)
        if reason:
            local["fallback_reason"] = reason
            local["fallback_attempts"] = [
                {"page": number, "model": "", "reason": reason} for number in selected
            ]
        _record_grounding(local, {})
        return
    deadline = now + _float_env("RADON_RESEARCH_PARSE_BUDGET_S", 100.0)
    images = _render_selected(pdf_path, selected)
    outcomes = _parse_selected(
        local, selected, images, post=post, sleep=sleep, monotonic=monotonic,
        jitter=jitter, deadline=deadline,
    )
    _apply(local, outcomes)


def _selected_pages(pdf_path, local) -> list[int]:
    pages = local["pages"]
    if not _selective():
        return [page["page_number"] for page in pages]
    visual = _visual_pages(pdf_path, len(pages))
    chosen = []
    for page in pages:
        number = page["page_number"]
        markdown = local["markdowns"].get(number, "")
        if page.get("needs_ocr") or not (markdown or "").strip() or number in visual:
            chosen.append(number)
    return chosen


def _selective() -> bool:
    raw = os.environ.get("RADON_RESEARCH_PARSE_SELECTIVE", "1").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def _visual_pages(pdf_path, page_count: int) -> set[int]:
    """Pages with a large raster image or a figures.py cluster. Text pages stay out."""
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as pdfium_c
        from research.figures import _figures_on_page, _frame, _norm
    except Exception:
        return set()
    selected = set()
    try:
        with pdfium.PdfDocument(str(pdf_path)) as document:
            for index in range(min(page_count, len(document))):
                page = document[index]
                number = index + 1
                frame, reason = _frame(page)
                raster = False
                if frame and not reason:
                    try:
                        objects = page.get_objects(filter=(pdfium_c.FPDF_PAGEOBJ_IMAGE,), max_depth=8)
                    except Exception:
                        objects = ()
                    for obj in objects:
                        try:
                            box = _norm(obj.get_bounds(), frame)
                        except Exception:
                            continue
                        if (box[2] - box[0]) * (box[3] - box[1]) >= 0.02:
                            raster = True
                            break
                page.close()
                if raster:
                    selected.add(number)
                    continue
                figures, _skip = _figures_on_page(pdf_path, number)
                if figures:
                    selected.add(number)
    except Exception:
        return selected
    return selected


def _parse_selected(local, selected, images, *, post, sleep, monotonic, jitter, deadline):
    workers = _int_env("RADON_RESEARCH_PARSE_CONCURRENCY", 2, 1, 8)
    outcomes = {}
    futures = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for number in selected:
            now = _clock(monotonic)
            if now >= deadline:
                outcomes[number] = {"ok": False, "reason": "budget"}
                continue
            if _parked(now):
                outcomes[number] = {"ok": False, "reason": "circuit"}
                continue
            png = images.get(number)
            if not png:
                outcomes[number] = {"ok": False, "reason": "render"}
                continue
            futures[pool.submit(
                _parse_one, number, png, local, post=post, sleep=sleep,
                monotonic=monotonic, jitter=jitter, deadline=deadline,
            )] = number
        for future in as_completed(futures):
            number = futures[future]
            try:
                outcomes[number] = future.result()
            except Exception as exc:
                from clients.model_ladder import safe_error_message
                outcomes[number] = {"ok": False, "reason": safe_error_message(exc)}
    return outcomes


def _parse_one(number, png, local, *, post, sleep, monotonic, jitter, deadline):
    page = next(item for item in local["pages"] if item["page_number"] == number)
    local_md = local["markdowns"].get(number, "")
    last = "empty"
    last_model = ""
    for model in parse_models():
        now = _clock(monotonic)
        if now >= deadline:
            return {"ok": False, "reason": "budget", "model": last_model}
        if _parked(now):
            return {"ok": False, "reason": "circuit", "model": last_model}
        last_model = model
        try:
            _markdown_ignored, blocks = _parse_page(
                model, png, post=post, sleep=sleep, monotonic=monotonic,
                jitter=jitter, deadline=deadline,
            )
        except PageParseError as exc:
            last = exc.reason
            if _trips_breaker(last):
                _note_failure(_clock(monotonic))
            continue
        prepared, problem = prepare_blocks(blocks)
        if problem == "duplicated":
            _note_failure(_clock(monotonic))
            return {"ok": False, "reason": "duplicated", "model": model}
        agree = table_agreement(local_md, prepared)
        minimum = _float_env("RADON_RESEARCH_PARSE_TABLE_AGREE", 0.90)
        if _has_text_layer(page, local_md) and agree is not None and agree < minimum:
            return {
                "ok": False, "reason": "table_agreement", "model": model, "table_agreement": agree,
            }
        _note_success()
        return {
            "ok": True,
            "model": model,
            "markdown": _markdown(prepared),
            "blocks": prepared,
            "table_agreement": agree,
        }
    return {"ok": False, "reason": last, "model": last_model}


def _apply(local, outcomes):
    attempts = []
    used = None
    for page in local["pages"]:
        number = page["page_number"]
        page["sources"] = ["local"]
        outcome = outcomes.get(number)
        if not outcome:
            continue
        if not outcome.get("ok"):
            reason = outcome.get("reason") or "empty"
            page["fallback_reason"] = reason
            attempts.append({"page": number, "model": outcome.get("model") or "", "reason": reason})
            if outcome.get("table_agreement") is not None:
                page["table_agreement"] = outcome["table_agreement"]
            continue
        local_md = local["markdowns"].get(number, "")
        nemotron_md = outcome.get("markdown") or ""
        used = outcome["model"]
        local["markdowns"][number] = compose_markdown(local_md, nemotron_md)
        page["sources"] = ["local", "nemotron"]
        page["model"] = used
        page["blocks"] = outcome["blocks"]
        page["chart_candidates"] = chart_candidates(outcome["blocks"], number)
        page["image_derived_numbers"] = image_derived_numbers(local_md, nemotron_md)
        if outcome.get("table_agreement") is not None:
            page["table_agreement"] = outcome["table_agreement"]
    if used:
        local["parser"] = f"{used}+firecrawl/pdf-inspector"
        local["parser_version"] = used
    if attempts:
        local["fallback_attempts"] = attempts
        if not used:
            local["fallback_reason"] = attempts[-1]["reason"]
    _record_grounding(local, outcomes)


def _record_grounding(local, outcomes):
    rows = []
    for page in local["pages"]:
        number = page["page_number"]
        outcome = outcomes.get(number) or {}
        rows.append({
            "page_number": number,
            "sources": page.get("sources") or ["local"],
            "fallback_reason": page.get("fallback_reason"),
            "table_agreement": outcome.get("table_agreement", page.get("table_agreement")),
            "model": page.get("model") or outcome.get("model") or "",
        })
    local["grounding"] = {
        "method": "local_text_layer",
        "table_agreement_min": _float_env("RADON_RESEARCH_PARSE_TABLE_AGREE", 0.90),
        "pages": rows,
    }


def _stamp_local(local, reason: str | None = None):
    for page in local["pages"]:
        page["sources"] = ["local"]
        if reason:
            page["fallback_reason"] = reason


def _has_text_layer(page, markdown: str) -> bool:
    if page.get("needs_ocr"):
        return False
    return bool((markdown or "").strip())


def _parked(now: float) -> bool:
    with _CIRCUIT_LOCK:
        return now < _CIRCUIT["park_until"]


def _note_failure(now: float) -> None:
    limit = _int_env("RADON_RESEARCH_PARSE_FAIL_LIMIT", 3, 1, 50)
    with _CIRCUIT_LOCK:
        _CIRCUIT["failures"] += 1
        if _CIRCUIT["failures"] >= limit:
            from research.model import PROVIDER_PARK_SECS
            _CIRCUIT["park_until"] = now + PROVIDER_PARK_SECS
            _CIRCUIT["failures"] = 0


def _note_success() -> None:
    with _CIRCUIT_LOCK:
        _CIRCUIT["failures"] = 0


def _trips_breaker(reason: str) -> bool:
    return reason in _BREAKER or reason == "http_429" or reason.startswith("http_5")


def _parse_page(model, png: bytes, *, post, sleep, monotonic, jitter, deadline):
    if _clock(monotonic) >= deadline:
        raise PageParseError("budget")
    data_uri = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    body = _request_body(model, data_uri)
    payload = _post_with_retries(
        model, body, post=post, sleep=sleep, monotonic=monotonic,
        jitter=jitter, deadline=deadline,
    )
    if _adapter(model) == "v2":
        return _blocks_from_v2(payload)
    return _blocks_from_v1(payload)


def _request_body(model: str, data_uri: str) -> dict:
    if _adapter(model) == "v2":
        return {
            "model": model,
            "temperature": 0,
            "max_tokens": 3500,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": _V2_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }],
        }
    return {
        "model": model,
        "temperature": 0,
        "max_tokens": 4096,
        "tools": [{"type": "function", "function": {"name": "markdown_bbox"}}],
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_uri}},
            ],
        }],
    }


def _adapter(model: str) -> str:
    if "nemotron-parse-2" in model:
        return "v2"
    return "v1"


class _CapturingPost:
    def __init__(self, post):
        self._post = post
        self.headers: dict = {}

    def __call__(self, url, **kwargs):
        response = self._post(url, **kwargs)
        self.headers = dict(getattr(response, "headers", {}) or {})
        return response


def _post_with_retries(model, body, *, post, sleep, monotonic, jitter, deadline):
    from clients.model_ladder import _classify_http_failure, _default_post, _request

    sender = _CapturingPost(post or _default_post)
    key = os.environ.get("NVIDIA_API_KEY", "").strip() or ("test-key" if post else "")
    if not key:
        raise PageParseError("missing_api_key")
    headers = {"authorization": f"Bearer {key}", "content-type": "application/json"}
    timeout = _float_env("RADON_RESEARCH_PARSE_PAGE_TIMEOUT_S", 25.0)
    retries = _int_env("RADON_RESEARCH_PARSE_MAX_RETRIES", 2, 0, 5)
    delay = 0.5
    last = "empty"
    for attempt in range(retries + 1):
        if _clock(monotonic) >= deadline:
            raise PageParseError("budget")
        try:
            status, text, payload = _request(sender, NVIDIA_CHAT_URL, headers, body, timeout=timeout)
        except RuntimeError as exc:
            last = "timeout" if "timeout" in str(exc).lower() else "network"
            if attempt >= retries or not _retryable(last):
                raise PageParseError(last) from exc
            _sleep(min(delay, 8.0) + _jitter(jitter), sleep)
            delay = min(delay * 2, 8.0)
            continue
        failure = _failure_reason(model, status, text or "", payload, _classify_http_failure)
        if failure is None:
            return payload
        last = failure
        if attempt >= retries or not _retryable(failure):
            raise PageParseError(failure)
        retry_after = _retry_after(sender.headers)
        wait = min(retry_after, 8.0) if retry_after is not None else min(delay, 8.0)
        _sleep(max(0.0, wait) + _jitter(jitter), sleep)
        delay = min(delay * 2, 8.0)
    raise PageParseError(last)


def _retryable(reason: str) -> bool:
    return reason in {"degraded", "empty", "length", "timeout", "network"} or reason == "http_429" or reason.startswith("http_5")


def _failure_reason(model, status, text, payload, classify) -> str | None:
    if status == 429:
        return "http_429"
    if status == 400 and "degraded" in text.lower():
        return "degraded"
    if status != 200 or not isinstance(payload, dict):
        return "degraded" if "degraded" in text.lower() else classify(status, text)
    finish = _finish_reason(payload)
    if finish == "length":
        return "length"
    if _adapter(model) == "v2":
        content = _openai_text(payload)
        if _meaningful(content):
            return None
        return "empty"
    if _v1_blocks(payload) is None:
        content = _openai_text(payload)
        return "malformed" if _meaningful(content) else "empty"
    return None


def _finish_reason(payload) -> str | None:
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        return None
    return choices[0].get("finish_reason")


def _blocks_from_v2(payload) -> tuple[str, list]:
    content = _openai_text(payload)
    if not _meaningful(content):
        finish = _finish_reason(payload)
        raise PageParseError("length" if finish == "length" else "empty")
    blocks = _v2_blocks(content)
    return _markdown(blocks), blocks


def _blocks_from_v1(payload) -> tuple[str, list]:
    if _finish_reason(payload) == "length":
        raise PageParseError("length")
    raw = _v1_blocks(payload)
    if not raw:
        raise PageParseError("malformed")
    blocks = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "Text")
        text = str(item.get("text") or "")
        blocks.append({"type": kind, "text": text, "bbox": _norm_bbox(item.get("bbox"))})
    if not blocks:
        raise PageParseError("malformed")
    return _markdown(blocks), blocks


def _v1_blocks(payload):
    choices = payload.get("choices") or []
    if not choices:
        return None
    message = (choices[0] or {}).get("message") or {}
    calls = message.get("tool_calls") or []
    if not calls:
        return None
    arguments = ((calls[0] or {}).get("function") or {}).get("arguments")
    parsed = _loads_json(arguments)
    blocks = _flatten_blocks(parsed)
    return blocks or None


def _loads_json(value, depth: int = 0):
    import json
    if isinstance(value, str) and depth < 2:
        try:
            return _loads_json(json.loads(value), depth + 1)
        except json.JSONDecodeError:
            return None
    return value


def _flatten_blocks(parsed) -> list:
    if isinstance(parsed, dict) and any(key in parsed for key in ("bbox", "text", "type")):
        return [parsed]
    if isinstance(parsed, dict):
        found = []
        for value in parsed.values():
            found.extend(_flatten_blocks(value))
        return found
    if isinstance(parsed, list):
        found = []
        for item in parsed:
            found.extend(_flatten_blocks(item))
        return found
    return []


def _v2_blocks(content: str) -> list:
    cleaned = _NOISE.sub("", content or "")
    parts = _CLASS_RE.split(cleaned)
    blocks = []
    if len(parts) == 1:
        bbox, text = _leading_bbox(parts[0])
        if text.strip():
            blocks.append({"type": "Text", "text": text.strip(), "bbox": bbox})
        return blocks
    pending = parts[0]
    index = 1
    while index < len(parts):
        kind = parts[index]
        pending_next = parts[index + 1] if index + 1 < len(parts) else ""
        bbox, text = _leading_bbox(pending)
        blocks.append({"type": kind, "text": text.strip(), "bbox": bbox})
        pending = pending_next
        index += 2
    return blocks


def _leading_bbox(text: str):
    stripped = (text or "").lstrip()
    match = _BBOX_TAG.match(stripped)
    if match:
        nums = [float(match.group(i)) for i in range(1, 5)]
        bbox = {"xmin": nums[0], "ymin": nums[1], "xmax": nums[2], "ymax": nums[3]}
        return bbox, stripped[match.end():]
    match = _LEADING_COORDS.match(stripped)
    if match:
        nums = [float(value) for value in re.findall(r"<(?:[xy]_)?(-?\d+(?:\.\d+)?)>", match.group(0))]
        if len(nums) >= 4:
            bbox = {"xmin": nums[0], "ymin": nums[1], "xmax": nums[2], "ymax": nums[3]}
            return bbox, stripped[match.end():]
    return None, text or ""


def _markdown(blocks: list) -> str:
    parts = []
    for block in blocks:
        kind = str(block.get("type") or "Text")
        norm = kind.lower().replace("_", "-")
        text = str(block.get("text") or "").strip()
        if not text:
            continue
        if norm == "table" or "\\begin{tabular}" in text:
            text = latex_table_to_markdown(text)
        elif norm in _TITLE_KINDS and not text.startswith("#"):
            text = "# " + text
        parts.append(text)
    return "\n\n".join(parts)


def _meaningful(text: str) -> bool:
    cleaned = _NOISE.sub("", text or "")
    cleaned = _CLASS_RE.sub("", cleaned)
    cleaned = re.sub(r"<(?:[xy]_)?-?\d+(?:\.\d+)?>", "", cleaned)
    cleaned = _BBOX_TAG.sub("", cleaned)
    return bool(cleaned.strip())


def _openai_text(payload: dict) -> str:
    from clients.model_ladder import _text_from_openai
    return _text_from_openai(payload)


def _norm_bbox(raw):
    if not isinstance(raw, dict):
        return None
    try:
        xmin, ymin, xmax, ymax = (float(raw[key]) for key in ("xmin", "ymin", "xmax", "ymax"))
    except (KeyError, TypeError, ValueError):
        return None
    return {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax}


def _bbox_key(block) -> tuple[float, float]:
    bbox = block.get("bbox") if isinstance(block, dict) else None
    if not isinstance(bbox, dict):
        return (1.0, 1.0)
    try:
        return (float(bbox.get("ymin", 1.0)), float(bbox.get("xmin", 1.0)))
    except (TypeError, ValueError):
        return (1.0, 1.0)


def _bbox_tuple(bbox):
    if not isinstance(bbox, dict):
        return None
    try:
        return tuple(round(float(bbox[key]), 4) for key in ("xmin", "ymin", "xmax", "ymax"))
    except (KeyError, TypeError, ValueError):
        return None


def _area(bbox) -> float:
    if not isinstance(bbox, dict):
        return 0.0
    try:
        width = float(bbox["xmax"]) - float(bbox["xmin"])
        height = float(bbox["ymax"]) - float(bbox["ymin"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    return max(0.0, width) * max(0.0, height)


def _latex_cell(cell: str) -> str:
    cell = cell.replace(r"\%", "%").replace(r"\$", "$").replace(r"\&", "&")
    cell = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", cell)
    cell = _LATEX_CMD.sub("", cell)
    return " ".join(cell.replace("{", "").replace("}", "").split())


def _render_selected(pdf_path, numbers: list[int]) -> dict[int, bytes]:
    import pypdfium2
    from pathlib import Path

    pngs = {}
    with pypdfium2.PdfDocument(str(Path(pdf_path).resolve(strict=True))) as document:
        for number in numbers:
            if number < 1 or number > len(document):
                continue
            page = document[number - 1]
            pngs[number] = _page_png(page)
            page.close()
    return pngs


def _page_png(page) -> bytes:
    width = page.get_width() or 1
    height = page.get_height() or 1
    scale = 1664 / max(width, height)
    scale = min(max(scale, 72 / 72), 300 / 72)
    if width * height * scale * scale > 24_000_000:
        scale = (24_000_000 / (width * height)) ** 0.5
    bitmap = page.render(scale=scale)
    image = bitmap.to_pil()
    image.thumbnail((1664, 2048))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    image.close()
    bitmap.close()
    return buffer.getvalue()


def _retry_after(headers: dict):
    for key, value in (headers or {}).items():
        if str(key).lower() == "retry-after":
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def _clock(monotonic):
    return monotonic() if monotonic is not None else time.monotonic()


def _jitter(jitter):
    if jitter is not None:
        return float(jitter())
    return random.uniform(0, 0.25)


def _sleep(seconds: float, sleep):
    if seconds <= 0:
        return
    if sleep is not None:
        sleep(seconds)
    else:
        time.sleep(seconds)


def _int_env(name: str, default: int, low: int, high: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(low, min(value, high))


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default
