"""Prefer IPv4 in getaddrinfo results, keeping IPv6 as fallback.

The Hetzner VPS advertises a global IPv6 address and default route, but
several fetch destinations (query1.finance.yahoo.com,
raw.githubusercontent.com) are blackholed over IPv6 from that network:
the TCP SYN times out instead of being refused. urllib walks getaddrinfo
results sequentially and burns its full per-request timeout on each AAAA
attempt before reaching a working A record, which turned a measured
0.2s Yahoo chart call into 60s (2026-08-23, radon-divyield timeout on
its first production run; curl was immune only because of Happy
Eyeballs). Sorting IPv4 first restores the fast path everywhere while
leaving IPv6 usable as a genuine fallback.

Call prefer_ipv4() once at process start, before the first HTTP request.
"""
from __future__ import annotations

import socket

_original_getaddrinfo = socket.getaddrinfo


def _ipv4_first_getaddrinfo(*args, **kwargs):
    results = _original_getaddrinfo(*args, **kwargs)
    return sorted(results, key=lambda info: info[0] != socket.AF_INET)


def prefer_ipv4() -> None:
    socket.getaddrinfo = _ipv4_first_getaddrinfo
