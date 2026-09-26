"""Compare local pdf-inspector output with Nemotron Parse on a folder of PDFs.

Writes a markdown report. Network runs only for the Nemotron pass, and only
when NVIDIA_API_KEY is set. Tests inject post= and never call the network.

    python -m research.compare_parsers /path/to/pdfs -o /tmp/parser-comparison.md
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
import time
from pathlib import Path

_TABLE_DELIM = re.compile(
    r"\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*"
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args(argv)
    folder = args.folder
    output = args.output or (folder / "parser-comparison.md")
    report = compare_folder(folder, output)
    sys.stdout.write(report)


def compare_folder(folder: Path, output: Path, *, post=None) -> str:
    pdfs = sorted(path for path in Path(folder).iterdir() if path.suffix.lower() == ".pdf" and path.is_file())
    rows = []
    for pdf in pdfs:
        rows.append(compare_pdf(pdf, post=post))
    report = render_report(rows)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report)
    return report


def compare_pdf(pdf: Path, *, post=None) -> dict:
    from clients.model_ladder import safe_error_message

    row = {
        "file": pdf.name,
        "pages": 0,
        "local_chars": 0,
        "nemotron_chars": 0,
        "local_tables": 0,
        "nemotron_tables": 0,
        "numeric_recall": 0.0,
        "charts": 0,
        "pictures": 0,
        "local_seconds": 0.0,
        "nemotron_seconds": 0.0,
        "nemotron_parser": "",
        "fallback": "",
    }
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            started = time.perf_counter()
            local = _parse_as(pdf, root / "local", "local")
            row["local_seconds"] = time.perf_counter() - started
            started = time.perf_counter()
            hosted = _parse_as(pdf, root / "nemotron", "nemotron", post=post)
            row["nemotron_seconds"] = time.perf_counter() - started
            row.update(_measure(local, root / "local", hosted, root / "nemotron"))
    except Exception as exc:
        row["fallback"] = safe_error_message(exc)
    return row


def render_report(rows: list[dict]) -> str:
    lines = [
        "# Parser comparison",
        "",
        "Numeric recall is the minimum per-page overlap of distinct numeric tokens against the local extract.",
        "",
        "| file | pages | local chars | nemotron chars | local tables | nemotron tables | numeric recall | charts | pictures | local s | nemotron s | parser | fallback |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {file} | {pages} | {local_chars} | {nemotron_chars} | {local_tables} | {nemotron_tables} | {recall} | {charts} | {pictures} | {local_s} | {nemotron_s} | {parser} | {fallback} |".format(
                file=row.get("file", ""),
                pages=row.get("pages", 0),
                local_chars=row.get("local_chars", 0),
                nemotron_chars=row.get("nemotron_chars", 0),
                local_tables=row.get("local_tables", 0),
                nemotron_tables=row.get("nemotron_tables", 0),
                recall=f"{float(row.get('numeric_recall', 0.0)):.3f}",
                charts=row.get("charts", 0),
                pictures=row.get("pictures", 0),
                local_s=f"{float(row.get('local_seconds', 0.0)):.3f}",
                nemotron_s=f"{float(row.get('nemotron_seconds', 0.0)):.3f}",
                parser=row.get("nemotron_parser", ""),
                fallback=row.get("fallback", "") or "",
            )
        )
    lines.append("")
    return "\n".join(lines)


def _parse_as(pdf: Path, output: Path, mode: str, post=None):
    from research.pdf import parse

    previous = os.environ.get("RADON_RESEARCH_PARSER")
    os.environ["RADON_RESEARCH_PARSER"] = mode
    try:
        return parse(pdf, output, post=post)
    finally:
        if previous is None:
            os.environ.pop("RADON_RESEARCH_PARSER", None)
        else:
            os.environ["RADON_RESEARCH_PARSER"] = previous


def _measure(local: dict, local_dir: Path, hosted: dict, hosted_dir: Path) -> dict:
    from research.nemotron_parse import page_recall

    pages = local.get("pages") or []
    recalls = []
    local_chars = nemotron_chars = local_tables = nemotron_tables = 0
    charts = pictures = 0
    for page in pages:
        name = page["markdown_file"]
        local_text = (local_dir / name).read_text(encoding="utf-8")
        hosted_text = (hosted_dir / name).read_text(encoding="utf-8")
        local_chars += len(local_text)
        nemotron_chars += len(hosted_text)
        local_tables += _markdown_tables(local_text)
        nemotron_tables += _markdown_tables(hosted_text)
        recalls.append(page_recall(local_text, hosted_text)["recall"])
    for page in hosted.get("pages") or []:
        for block in page.get("blocks") or []:
            kind = str(block.get("type") or "").lower()
            if kind == "chart":
                charts += 1
            elif kind in {"picture", "image"}:
                pictures += 1
    return {
        "pages": len(pages),
        "local_chars": local_chars,
        "nemotron_chars": nemotron_chars,
        "local_tables": local_tables,
        "nemotron_tables": nemotron_tables,
        "numeric_recall": min(recalls) if recalls else 1.0,
        "charts": charts,
        "pictures": pictures,
        "nemotron_parser": hosted.get("parser") or "",
        "fallback": hosted.get("fallback_reason") or "",
    }


def _markdown_tables(text: str) -> int:
    lines = text.splitlines()
    count = 0
    for index in range(len(lines) - 1):
        if lines[index].strip() and _TABLE_DELIM.fullmatch(lines[index + 1] or ""):
            count += 1
    return count


if __name__ == "__main__":
    main()
