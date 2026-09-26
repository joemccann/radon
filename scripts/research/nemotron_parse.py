"""NVIDIA Nemotron Parse for research PDFs, with a whole-document local fallback.

The hosted ladder is RADON_RESEARCH_PARSE_MODELS (default
nvidia/nemotron-parse-2.0, then nvidia/nemotron-parse). Each model has its
own request and response adapter. A single failed page, a bad numeric-token
recall against the local pdf-inspector text, or an exhausted budget drops
the whole document onto research.pdf.parse_local. Parsers are never mixed
inside one document.

Numeric recall is |local numbers ∩ nemotron numbers| / |local numbers|.
A page is gated only when the local text has at least
RADON_RESEARCH_PARSE_MIN_NUMERIC distinct numbers (default 3) and recall is
below RADON_RESEARCH_PARSE_MIN_RECALL (default 0.6). Fewer numbers are
recorded and do not force a fallback, so a date-only page cannot discard a
good parse. Downstream gates copy numbers verbatim; this is the check that
protects them.
"""
from __future__ import annotations

import base64
import copy
import io
import os
import random
import re
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

DEFAULT_MODELS = ("nvidia/nemotron-parse-2.0", "nvidia/nemotron-parse")
NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
_EMPTY_KINDS = {"chart", "picture", "image"}
_TITLE_KINDS = {"title", "section-header", "header", "section_header"}


class PageParseError(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


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


def page_recall(local_text: str, other_text: str) -> dict:
    local = numeric_tokens(local_text)
    other = numeric_tokens(other_text)
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
        return write_evidence(output, local)
    if post is None and not os.environ.get("NVIDIA_API_KEY", "").strip():
        local["fallback_reason"] = "missing_api_key"
        local["fallback_attempts"] = []
        return write_evidence(output, local)
    try:
        hosted, attempts, grounding = _try_models(
            pdf_path, local, post=post, sleep=sleep, monotonic=monotonic, jitter=jitter,
        )
    except Exception as exc:
        from clients.model_ladder import safe_error_message
        local["fallback_reason"] = safe_error_message(exc)
        local["fallback_attempts"] = []
        return write_evidence(output, local)
    if hosted is None:
        local["fallback_attempts"] = attempts
        local["fallback_reason"] = attempts[-1]["reason"] if attempts else "no_model"
        if grounding is not None:
            local["nemotron_grounding"] = grounding
        return write_evidence(output, local)
    return write_evidence(output, hosted)


def _try_models(pdf_path, local, *, post, sleep, monotonic, jitter):
    deadline = _clock(monotonic) + _float_env("RADON_RESEARCH_PARSE_BUDGET_S", 100.0)
    min_recall = _float_env("RADON_RESEARCH_PARSE_MIN_RECALL", 0.6)
    min_tokens = _int_env("RADON_RESEARCH_PARSE_MIN_NUMERIC", 3, 0, 1000)
    attempts = []
    last_grounding = None
    images = _render_pngs(pdf_path, len(local["pages"]))
    for model in parse_models():
        if _clock(monotonic) >= deadline:
            attempts.append({"model": model, "reason": "budget"})
            break
        try:
            parsed = _parse_model(
                model, images, post=post, sleep=sleep, monotonic=monotonic,
                jitter=jitter, deadline=deadline,
            )
        except PageParseError as exc:
            attempts.append({"model": model, "reason": exc.reason})
            continue
        stats = []
        for page, (markdown, _blocks) in zip(local["pages"], parsed):
            row = page_recall(local["markdowns"][page["page_number"]], markdown)
            row["page_number"] = page["page_number"]
            stats.append(row)
        grounding = {
            "method": "numeric_token_recall",
            "min_recall": min_recall,
            "min_local_tokens": min_tokens,
            "model": model,
            "pages": stats,
        }
        failed = any(
            row["local_count"] >= min_tokens and row["recall"] < min_recall for row in stats
        )
        if failed:
            grounding["failed"] = True
            last_grounding = grounding
            attempts.append({"model": model, "reason": "numeric_grounding"})
            continue
        grounding["failed"] = False
        return _hosted_result(local, model, parsed, grounding), attempts, grounding
    return None, attempts, last_grounding


def _hosted_result(local, model, parsed, grounding):
    hosted = copy.deepcopy(local)
    hosted["parser"] = model
    hosted["parser_version"] = model
    hosted["grounding"] = grounding
    for page, (markdown, blocks) in zip(hosted["pages"], parsed):
        number = page["page_number"]
        hosted["markdowns"][number] = markdown
        page["blocks"] = blocks
        if markdown.strip():
            page["needs_ocr"] = False
            page["ocr_reason"] = None
        else:
            page["needs_ocr"] = True
            page["ocr_reason"] = "nemotron returned no text"
    return hosted


def _parse_model(model, images, *, post, sleep, monotonic, jitter, deadline):
    workers = _int_env("RADON_RESEARCH_PARSE_CONCURRENCY", 4, 1, 8)
    results: list = [None] * len(images)
    error: PageParseError | None = None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _parse_page, model, image, post=post, sleep=sleep,
                monotonic=monotonic, jitter=jitter, deadline=deadline,
            ): index
            for index, image in enumerate(images)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except PageParseError as exc:
                error = exc
                break
        if error is not None:
            for future in futures:
                future.cancel()
            raise error
    if any(item is None for item in results):
        raise PageParseError("empty")
    return results


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
    timeout = _float_env("RADON_RESEARCH_PARSE_PAGE_TIMEOUT_S", 20.0)
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
    if _adapter(model) == "v2":
        content = _openai_text(payload)
        finish = ((payload.get("choices") or [{}])[0] or {}).get("finish_reason")
        if _meaningful(content):
            return None
        return "length" if finish == "length" else "empty"
    if _v1_blocks(payload) is None:
        return "malformed"
    return None


def _blocks_from_v2(payload) -> tuple[str, list]:
    content = _openai_text(payload)
    if not _meaningful(content):
        finish = ((payload.get("choices") or [{}])[0] or {}).get("finish_reason")
        raise PageParseError("length" if finish == "length" else "empty")
    blocks = _v2_blocks(content)
    if not blocks and not _meaningful(content):
        raise PageParseError("malformed")
    return _markdown(blocks), blocks


def _blocks_from_v1(payload) -> tuple[str, list]:
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
    import json
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
        if norm in _EMPTY_KINDS:
            continue
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


def _latex_cell(cell: str) -> str:
    cell = cell.replace(r"\%", "%").replace(r"\$", "$").replace(r"\&", "&")
    cell = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", cell)
    cell = _LATEX_CMD.sub("", cell)
    return " ".join(cell.replace("{", "").replace("}", "").split())


def _render_pngs(pdf_path, page_count: int) -> list[bytes]:
    import pypdfium2
    from pathlib import Path

    pngs = []
    with pypdfium2.PdfDocument(str(Path(pdf_path).resolve(strict=True))) as document:
        if len(document) != page_count:
            raise ValueError("page count changed while rendering")
        for number in range(page_count):
            page = document[number]
            pngs.append(_page_png(page))
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
