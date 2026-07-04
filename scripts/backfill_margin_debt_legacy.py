#!/usr/bin/env python3
"""One-time backfill: legacy NYSE margin debt (1959-1996) from the Wayback Machine.

NYSE published "Securities market credit ($ in mils.)" monthly member-firm
tables in its Facts & Figures viewer until the series was suspended in Dec
2017. nyxdata.com is gone; the decade tables survive in the Wayback Machine.
This script parses those archived year pages into data/margin_debt_legacy.csv,
which fetch_margin_debt.py splices in front of the FINRA series.

The archived viewer renders ONE YEAR per page. Decade editions are reachable
via viewer_edition.asp?mode=table&key=<decade key>&category=8 and paged by
year; each page carries the year in its "pageTitle" cell. Column sets vary by
era (1960s tables have a single "Credit balances in margin accounts" column;
1990s tables add free credit cash accounts), so columns are mapped by header
text, not position.

Usage:
    python3 scripts/backfill_margin_debt_legacy.py --html-dir <dir>  # parse saved pages
    python3 scripts/backfill_margin_debt_legacy.py --check           # summarize existing CSV
"""
from __future__ import annotations

import argparse
import csv
import html as html_lib
import re
import sys
from pathlib import Path
from typing import Any, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent

LEGACY_CSV = _PROJECT_DIR / "data" / "margin_debt_legacy.csv"
CSV_FIELDS = ["date", "level", "free_credit_cash", "free_credit_margin"]

_MONTHS = {
    name: i + 1
    for i, name in enumerate(
        ["January", "February", "March", "April", "May", "June",
         "July", "August", "September", "October", "November", "December"]
    )
}

_TITLE_YEAR_RE = re.compile(r"Securities market credit[^,]*,\s*(\d{4})")
_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_CELL_RE = re.compile(r"<td[^>]*>(.*?)(?:</td>|$)", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _cell_text(cell_html: str) -> str:
    return html_lib.unescape(_TAG_RE.sub("", cell_html)).strip()


def _parse_money(text: str) -> Optional[float]:
    cleaned = text.replace("$", "").replace(",", "").strip()
    if not cleaned or cleaned in {"-", "N/A", "NA"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _classify_columns(header_cells: list[str]) -> dict[int, str]:
    """Map column index -> row field from the header text of an era's table."""
    mapping: dict[int, str] = {}
    for idx, text in enumerate(header_cells):
        lowered = text.lower()
        if "margin debt" in lowered:
            mapping[idx] = "level"
        elif "cash" in lowered:
            mapping[idx] = "free_credit_cash"
        elif "credit" in lowered:
            mapping[idx] = "free_credit_margin"
    return mapping


def parse_legacy_page_html(page_html: str) -> list[dict[str, Any]]:
    """Parse an archived NYSE Facts & Figures page into month rows.

    Decade editions render up to ten "Securities market credit ..., <year>"
    tables in one document; each title starts a section attributed to that
    year. Single-year captures are just the one-section case.
    """
    titles = list(_TITLE_YEAR_RE.finditer(page_html))
    if not titles:
        raise ValueError("no 'Securities market credit ..., <year>' title found")
    rows: list[dict[str, Any]] = []
    for match, following in zip(titles, titles[1:] + [None]):
        end = following.start() if following else len(page_html)
        rows.extend(_parse_year_section(int(match.group(1)), page_html[match.end():end]))
    rows.sort(key=lambda r: r["date"])
    return rows


def parse_legacy_year_html(page_html: str) -> tuple[int, list[dict[str, Any]]]:
    """Parse a single-year capture into (year, month rows).

    Rows carry the same shape fetch_margin_debt uses: date (YYYY-MM), level,
    free_credit_cash, free_credit_margin — credits are None when the era's
    table lacks that column.
    """
    title = _TITLE_YEAR_RE.search(_TAG_RE.sub("", page_html))
    if not title:
        raise ValueError("no 'Securities market credit ..., <year>' title found")
    year = int(title.group(1))
    return year, _parse_year_section(year, page_html)


def _parse_year_section(year: int, section_html: str) -> list[dict[str, Any]]:
    columns: dict[int, str] = {}
    rows: list[dict[str, Any]] = []
    for row_html in _ROW_RE.findall(section_html):
        cells = [_cell_text(c) for c in _CELL_RE.findall(row_html)]
        if not cells:
            continue
        if "End of month" in cells[0]:
            columns = _classify_columns(cells)
            continue
        month = _MONTHS.get(cells[0])
        if month is None or not columns:
            continue
        row: dict[str, Any] = {
            "date": f"{year}-{month:02d}",
            "level": None,
            "free_credit_cash": None,
            "free_credit_margin": None,
        }
        for idx, field in columns.items():
            if idx < len(cells):
                row[field] = _parse_money(cells[idx])
        if row["level"] is not None:
            rows.append(row)

    rows.sort(key=lambda r: r["date"])
    return rows


def load_legacy_csv(path: Path = LEGACY_CSV) -> list[dict[str, Any]]:
    """Read the backfilled legacy series (ascending). Empty list if absent."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open() as fh:
        for raw in csv.DictReader(fh):
            rows.append(
                {
                    "date": raw["date"],
                    "level": float(raw["level"]),
                    "free_credit_cash": float(raw["free_credit_cash"]) if raw.get("free_credit_cash") else None,
                    "free_credit_margin": float(raw["free_credit_margin"]) if raw.get("free_credit_margin") else None,
                }
            )
    rows.sort(key=lambda r: r["date"])
    return rows


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        out = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        out.writeheader()
        for row in sorted(rows, key=lambda r: r["date"]):
            out.writerow({k: ("" if row.get(k) is None else row[k]) for k in CSV_FIELDS})


def _parse_html_dir(html_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in sorted(html_dir.glob("*.htm*")):
        try:
            page_rows = parse_legacy_page_html(page.read_text(errors="replace"))
        except ValueError as exc:
            print(f"[legacy] skip {page.name}: {exc}", file=sys.stderr)
            continue
        years = sorted({r["date"][:4] for r in page_rows})
        print(f"[legacy] {page.name}: {years[0]}..{years[-1]} -> {len(page_rows)} months", file=sys.stderr)
        rows.extend(page_rows)
    deduped = {r["date"]: r for r in rows}
    return [deduped[d] for d in sorted(deduped)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill legacy NYSE margin debt from archived pages")
    parser.add_argument("--html-dir", type=Path, help="Directory of saved Wayback year pages to parse")
    parser.add_argument("--check", action="store_true", help="Summarize the existing legacy CSV")
    args = parser.parse_args()

    if args.check:
        rows = load_legacy_csv()
        if rows:
            print(f"{len(rows)} months, {rows[0]['date']} .. {rows[-1]['date']}", file=sys.stderr)
        else:
            print("no legacy CSV yet", file=sys.stderr)
        return

    if not args.html_dir:
        parser.error("--html-dir or --check required")
    rows = _parse_html_dir(args.html_dir)
    if not rows:
        print("[legacy] nothing parsed; CSV untouched", file=sys.stderr)
        sys.exit(1)
    _write_csv(rows, LEGACY_CSV)
    print(f"[legacy] wrote {len(rows)} months to {LEGACY_CSV}", file=sys.stderr)


if __name__ == "__main__":
    main()
