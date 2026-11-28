# 0004. One knowledge query policy

## Status

Accepted. 2026-11-28.

## Context

`scripts/knowledge/embed.py` asks the model gateway for vectors. The gateway's
`_embedding_backend` calls NVIDIA and, for local 384, loads fastembed.
Both modules defined `embedding_text`, `embed_backend`, `dual_write_enabled`,
the 60-second `embedding_v2` coverage cache, and `resolve_query_vector`.
The app server, the MCP server, and the golden eval import the app copy.
The embedding tests import the native copy. The two decisions could drift,
and a process that imported both kept two caches.

## Decision

`scripts/model_gateway/embed_rules.py` owns that policy. `resolve_query_vector`
there takes the query transport as an argument. Each module keeps a binder
that passes its own `embed_query`. Constants and the coverage cache are
re-exported, so existing imports keep their names.

`scripts/tests/test_embed_rules_single_source.py` rejects a second definition
of those functions, and rejects a second copy of the coverage SQL or the
dimension error.

## Consequences

A change to when a query uses 2048-d versus local 384 happens in one file.
The gateway HTTP client and the app's gateway call stay separate. The
coverage cache is one object, so a reset in either importer clears it.
