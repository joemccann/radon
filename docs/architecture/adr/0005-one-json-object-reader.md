# 0005. One JSON-object reader

## Status

Accepted. 2026-11-28.

## Context

Five scanners and `utils.scan_cache_gate.cached_scan_if_fresh` each parsed a
cache file with the same rule: missing, unreadable, or non-object JSON is
absence. A later change to that rule would have to land in all six places.
`atomic_save` is a different contract. It embeds `_checksum` and must not
become this reader.

## Decision

`scripts/utils/json_dict.py:read_json_dict` is the reader. The scanners and
the off-hours gate call it. Writers that replace a temp file, or that verify
a checksum, stay on their own path.

`scripts/tests/test_json_dict_single_source.py` rejects a new
`_read_cache_file` under `scripts/` and pins the miss / corrupt / non-dict
contract.

## Consequences

A cache that is not a JSON object is absence everywhere this reader is used.
Checksum files and credential files are not migrated onto it.
