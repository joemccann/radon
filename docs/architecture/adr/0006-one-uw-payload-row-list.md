# 0006. One UW payload row list

## Status

Accepted. 2026-11-28.

## Context

Unusual Whales answers with either a list of row objects or
`{"data": [rows]}`. Three scanners defined `_as_rows`. Earnings dates,
catalysts, and informed flow defined `_rows_from_payload`. The two
shapes agreed on every input that matters: keep dict rows, drop the rest.

Number 0006 leaves 0003-0005 for the open catalog, embed, and cache PR.

## Decision

`scripts/utils/payload_rows.py:rows_from_payload` is the reader.
Callers bind it to the old private names so existing patches still land
on the module global.

`scripts/tests/test_payload_rows_single_source.py` rejects a new
`def _as_rows` or `def _rows_from_payload` under `scripts/`.

## Consequences

A change to which UW envelope counts as rows happens in one file.
Checksum JSON and Cboe CSV parsing stay on their own readers.
