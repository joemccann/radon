"""Split knowledge vector writes that would exceed the Hrana request cap."""
from __future__ import annotations

import struct

from db.hrana_http import HranaHttpError


def vector_payload(vector) -> bytes:
    """Little-endian float32 blob. libSQL vector32() accepts this or JSON."""
    values = [float(value) for value in vector]
    return struct.pack(f"<{len(values)}f", *values)


def is_size_error(exc: BaseException) -> bool:
    return isinstance(exc, HranaHttpError) and "exceeds bounded size" in str(exc)


def run_size_bounded(items, write, fits, *, on_row_too_large) -> None:
    """write(batch) is one request. A size error, or a batch that does not
    fit, halves until a single row. One row that still does not fit is
    reported and does not abort the rest of the batch."""

    def attempt(batch):
        if not batch:
            return
        if len(batch) > 1 and not fits(batch):
            mid = len(batch) // 2
            attempt(batch[:mid])
            attempt(batch[mid:])
            return
        if len(batch) == 1 and not fits(batch):
            on_row_too_large(batch[0])
            return
        try:
            write(batch)
        except HranaHttpError as exc:
            if not is_size_error(exc):
                raise
            if len(batch) == 1:
                on_row_too_large(batch[0])
                return
            mid = len(batch) // 2
            attempt(batch[:mid])
            attempt(batch[mid:])

    attempt(list(items))
