"""Repo-root conftest: budget `pytest -n` on developer machines.

xdist's `auto` claims every physical core (14 on the operator's laptop) and
each worker imports the whole app (~470 MB), so one local run took ~6.5 GB
and a load average over 100 (2026-09-07). Half the cores locally, for `auto`
and for an explicit `-n N`; CI and `PYTEST_XDIST_AUTO_NUM_WORKERS` lift the
budget. Policy and tests: `scripts/ci/worker_budget.py`,
`test_local_xdist_worker_budget.py`. `optionalhook` keeps a pytest without
xdist installed from rejecting the xdist hook.
"""
from __future__ import annotations

import os
from urllib import request as urllib_request
from urllib.parse import urlsplit

import pytest

from ci.worker_budget import budgeted_workers


PUSHOVER_CREDENTIAL_KEYS = ("PUSHOVER_USER", "PUSHOVER_TOKEN")


@pytest.fixture(autouse=True)
def _isolate_pushover(monkeypatch):
    """Tests opt into synthetic credentials and fake transports, never delivery.

    Cover every pytest subtree, including cloud and API tests. Keep an attempt
    latch because production notifiers deliberately catch transport exceptions.
    """
    def strip_credentials():
        for key in PUSHOVER_CREDENTIAL_KEYS:
            monkeypatch.delenv(key, raising=False)

    strip_credentials()
    try:
        import dotenv
    except ImportError:
        pass
    else:
        real_load_dotenv = dotenv.load_dotenv

        def load_dotenv_without_pushover(*args, **kwargs):
            loaded = real_load_dotenv(*args, **kwargs)
            strip_credentials()
            return loaded

        monkeypatch.setattr(dotenv, "load_dotenv", load_dotenv_without_pushover)

    attempts = []
    forbidden = "Pushover network delivery is forbidden in tests; mock the sender"

    def guard(url):
        host = urlsplit(url).hostname or ""
        if host == "pushover.net" or host.endswith(".pushover.net"):
            attempts.append(host)
            raise AssertionError(forbidden)

    real_open = urllib_request.OpenerDirector.open

    def guarded_open(opener, fullurl, *args, **kwargs):
        guard(fullurl.full_url if isinstance(fullurl, urllib_request.Request) else fullurl)
        return real_open(opener, fullurl, *args, **kwargs)

    monkeypatch.setattr(urllib_request.OpenerDirector, "open", guarded_open)
    try:
        import requests
    except ImportError:
        pass
    else:
        real_send = requests.sessions.Session.send

        def guarded_send(session, request, **kwargs):
            guard(request.url)
            return real_send(session, request, **kwargs)

        monkeypatch.setattr(requests.sessions.Session, "send", guarded_send)

    yield
    assert not attempts, forbidden


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_auto_num_workers(config: pytest.Config) -> int | None:
    return budgeted_workers(None, os.cpu_count() or 1, os.environ)


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """Clamp an explicit `-n N` before xdist's session start spawns the nodes.

    xdist's cmdline_main has already expanded `-n N` into N `popen` specs on
    `config.option.tx`, so the budget is applied to that list; a hand-written
    `--tx` topology is left alone.
    """
    # Strip inherited operator credentials before collection and xdist spawn.
    for key in PUSHOVER_CREDENTIAL_KEYS:
        os.environ.pop(key, None)
    specs = list(getattr(config.option, "tx", None) or [])
    if not specs or any(spec != "popen" for spec in specs):
        return
    clamped = budgeted_workers(len(specs), os.cpu_count() or 1, os.environ)
    if clamped is None or clamped >= len(specs):
        return
    config.option.numprocesses = clamped
    config.option.tx = ["popen"] * clamped
