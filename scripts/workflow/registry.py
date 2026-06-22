"""Node-type registry.

Maps a node-type string (as it appears in a persisted graph) to a factory that
produces the node-callable. Built-ins are registered from ``nodes``; custom
node types can be added with ``register_node`` without editing the executor.
"""
from __future__ import annotations

from typing import Callable, Dict

from . import nodes as _nodes


class UnknownNodeError(KeyError):
    """Raised when a graph references a node type that isn't registered."""


_REGISTRY: Dict[str, Callable[[], Callable]] = dict(_nodes.BUILTIN_FACTORIES)


def register_node(node_type: str, factory: Callable[[], Callable]) -> None:
    """Register (or override) the factory for ``node_type``."""
    _REGISTRY[node_type] = factory


def get_node_factory(node_type: str) -> Callable[[], Callable]:
    """Return the factory for ``node_type`` or raise ``UnknownNodeError``."""
    try:
        return _REGISTRY[node_type]
    except KeyError as exc:
        raise UnknownNodeError(node_type) from exc


def registered_types() -> list[str]:
    return sorted(_REGISTRY)
