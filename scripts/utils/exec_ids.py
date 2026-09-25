"""IB execution-id conventions shared by the journal importers.

IB re-delivers a CORRECTED execution under the same exec-id root with the
trailing correction segment incremented: ``0002920b.6a19d5a9.01.02``
supersedes ``0002920b.6a19d5a9.01.01``. The corrected report replaces the
original — it is not a second fill — so importers must dedupe on the root,
not on exact string equality, or a correction double-counts quantity/cost.

Distinct executions differ in the segments BEFORE the correction counter
(``…69f202e9.02.01`` vs ``…69f202e9.03.01`` are two real executions of one
order), which is why only the final two-digit segment may be stripped. Same
convention as the web read side: ``web/lib/journal/realizedPnl.ts``
(``stripCorrectionSuffix``) and ``web/lib/fillToasts.ts`` (``execKey``).
"""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple

# Root must itself be dotted (IB roots are `<hex>.<hex>.<NN>`) and the
# correction counter is exactly two digits, so ad-hoc ids ("ORDER-7",
# "backfill.3") stay opaque and keep exact-match dedupe.
_CORRECTION_SUFFIX = re.compile(r"^(?P<root>.+\..+)\.(?P<correction>\d{2})$")
# Live fills append one extra two-digit segment to Flex ibExecID.
# Flex `0000f126.6ab14023.03.01` is journaled as `….03.01.01`.
# Only that last segment is removed, so `.02` and `.03` stay different legs.
_LIVE_EXTRA_SEGMENT = re.compile(
    r"^(?P<flex>[0-9A-Fa-f]+\.[0-9A-Fa-f]+\.\d{2}\.\d{2})\.\d{2}$"
)


def exec_id_root(exec_id: Any) -> Tuple[str, int]:
    """Split an exec id into ``(root, correction_number)``.

    Correction number 0 means "no IB correction suffix" — the id is its own
    root and callers must fall back to exact-match dedupe. Composite ids
    (``"A+B"``, written by journal_rehydrate for multi-fill buckets) are
    likewise opaque: their parts are the correctable units, not the join.
    """
    raw = str(exec_id or "").strip()
    if not raw or "+" in raw:
        return raw, 0
    match = _CORRECTION_SUFFIX.match(raw)
    if not match:
        return raw, 0
    return match.group("root"), int(match.group("correction"))


def flex_ib_exec_id(exec_id: Any) -> Optional[str]:
    """Flex ibExecID for a live five-part exec id, else None.

    Returns None for Flex's own four-part id, composites, and anything that
    is not `<hex>.<hex>.<dd>.<dd>.<dd>`. Callers add the result beside the
    stored id. They must not strip a second segment.
    """
    raw = str(exec_id or "").strip()
    if not raw or "+" in raw:
        return None
    match = _LIVE_EXTRA_SEGMENT.match(raw)
    if not match:
        return None
    return match.group("flex")
