"""Single-owner Advanced collector with bounded reader/writer separation."""
from __future__ import annotations

import fcntl
import json
import logging
import os
import queue
import random
import threading
import time
from pathlib import Path
from urllib.parse import quote

from .config import Config
from .store import Store

logger = logging.getLogger(__name__)


class BoundedFrames:
    """Bound both message count and serialized bytes before writer work."""
    def __init__(self, maxsize: int, max_bytes: int = 8 * 1024 * 1024):
        self.queue = queue.Queue(maxsize=maxsize)
        self.max_bytes = max_bytes
        self.bytes = 0
        self.lock = threading.Lock()

    def put_nowait(self, item) -> None:
        size = len(json.dumps(item[1], separators=(",", ":"), allow_nan=False).encode())
        with self.lock:
            if self.bytes + size > self.max_bytes:
                raise queue.Full
            self.queue.put_nowait((item, size))
            self.bytes += size

    def put(self, item, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.put_nowait(item)
                return
            except queue.Full:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)

    def get(self, timeout: float):
        item, size = self.queue.get(timeout=timeout)
        with self.lock:
            self.bytes -= size
        return item

    def qsize(self) -> int:
        return self.queue.qsize()

    def empty(self) -> bool:
        return self.queue.empty()


class OwnerLock:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "collector.lock"
        self.handle = None

    def __enter__(self):
        self.handle = self.path.open("a+")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.handle.close()
            raise RuntimeError("Another research collector owns this host") from None
        return self

    def __exit__(self, *_):
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


def decode_frame(raw: str | bytes, limit: int) -> tuple[str, dict]:
    if len(raw) > limit:
        raise ValueError("Research frame exceeds bound")
    value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
    if not isinstance(value, list) or len(value) != 2 or not isinstance(value[0], str) or not isinstance(value[1], dict):
        raise ValueError("Unexpected research envelope")
    return value[0], value[1]


def mirror_compact(snapshot: dict) -> None:
    """Only low-frequency compact metadata crosses Turso, never raw trades."""
    try:
        from db.writer import upsert_scan_snapshot, record_service_health
        payload = {k: snapshot.get(k) for k in ("schema_version", "source", "status", "updated_at", "connection", "channels", "metrics")}
        upsert_scan_snapshot("uw-stream", snapshot["updated_at"], payload)
        status = "ok" if snapshot["status"] in {"live", "disabled"} else "error"
        record_service_health("uw-stream", status, finished_at=snapshot["updated_at"], error=None if status == "ok" else {"message": "Research stream incomplete; retained observations may be stale"})
    except Exception:
        # Keep raw payloads, token-bearing transport URLs and DB credentials out
        # of logs even when a dependency interpolates them in its exception.
        logger.warning("UW compact mirror unavailable")


def run(config: Config, token: str | None = None, *, stop: threading.Event | None = None, connect=None, mirror=mirror_compact) -> None:
    stop = stop or threading.Event()
    if connect is None and config.enabled:
        from websockets.sync.client import connect
    # websockets' own warning path can contain the token-bearing URL. Errors
    # are classified below, not formatted from transport exceptions.
    transport_logger = logging.getLogger("radon.uw_transport")
    transport_logger.disabled = True
    events = BoundedFrames(maxsize=config.queue_size)
    overflow = threading.Event()
    fatal = threading.Event()
    socket_guard = threading.Lock()
    active_socket = [None]

    with OwnerLock(config.directory):
        def writer():
            store = None
            try:
                store = Store(config)
                batch, last_flush, last_mirror, batch_bytes = [], 0.0, 0.0, 0
                if not config.enabled:
                    store.publish_status("disabled", time.time())
                while not stop.is_set() or not events.empty():
                    now = time.time()
                    if overflow.is_set():
                        overflow.clear()
                        store.model.state["metrics"]["dropped"] += 1
                        store.model.gap(now, "input-queue-overflow")
                    try:
                        item = events.get(timeout=0.2)
                        batch.append(item)
                        batch_bytes += len(json.dumps(item[1], separators=(",", ":")).encode())
                    except queue.Empty:
                        pass
                    if len(batch) >= 500 or batch_bytes >= 2 * 1024 * 1024 or now - last_flush >= config.flush_seconds or (stop.is_set() and events.empty()):
                        if not config.enabled:
                            store.model.state["status"] = "disabled"
                        store.model.state["metrics"]["queue_bytes"] = events.bytes
                        snapshot = store.flush(batch, now, events.qsize())
                        batch.clear()
                        batch_bytes = 0
                        last_flush = now
                        if now - last_mirror >= 60:
                            mirror(snapshot)
                            last_mirror = now
                if config.enabled:
                    store.model.disconnect(time.time(), "collector-stopped")
                    store.flush([], time.time())
            except Exception:
                logger.error("UW collector storage unavailable")
                fatal.set()
                stop.set()
                with socket_guard:
                    if active_socket[0] is not None:
                        active_socket[0].close()
            finally:
                if store is not None:
                    store.close()

        writer_thread = threading.Thread(target=writer, name="uw-stream-writer")
        writer_thread.start()
        attempt = 0
        try:
            if not config.enabled:
                while not stop.wait(0.5):
                    if fatal.is_set():
                        break
            elif not token:
                raise ValueError("UW_TOKEN is required for enabled collection")
            else:
                while not stop.is_set():
                    try:
                        # Token never enters an argv, snapshot, event or log.
                        url = "wss://api.unusualwhales.com/socket?token=" + quote(token, safe="")
                        with connect(url, user_agent_header="radon-uw-stream/1.0", open_timeout=15, close_timeout=2, ping_interval=20, ping_timeout=20, max_size=config.max_frame_bytes, max_queue=64, logger=transport_logger) as socket:
                            with socket_guard:
                                active_socket[0] = socket
                            events.put(("__control__", {"event": "connect"}, time.time()), timeout=1)
                            for channel in config.channels:
                                socket.send(json.dumps({"channel": channel, "msg_type": "join"}))
                            while not stop.is_set():
                                try:
                                    raw = socket.recv(timeout=1)
                                except TimeoutError:
                                    continue
                                try:
                                    channel, payload = decode_frame(raw, config.max_frame_bytes)
                                    if channel not in config.channels:
                                        raise ValueError("Unrequested research channel")
                                except (ValueError, TypeError, UnicodeError):
                                    try:
                                        events.put_nowait(("__control__", {"event": "invalid"}, time.time()))
                                    except queue.Full:
                                        overflow.set()
                                    continue
                                try:
                                    events.put_nowait((channel, payload, time.time()))
                                except queue.Full:
                                    overflow.set()
                                attempt = 0
                    except Exception:
                        if not stop.is_set():
                            logger.warning("UW research transport disconnected; retained data marked incomplete")
                    finally:
                        with socket_guard:
                            active_socket[0] = None
                        try:
                            events.put_nowait(("__control__", {"event": "disconnect"}, time.time()))
                        except queue.Full:
                            overflow.set()
                    if not stop.is_set():
                        attempt = min(attempt + 1, 6)
                        stop.wait(min(60, 2 ** attempt) + random.uniform(0, 1))
        finally:
            stop.set()
            writer_thread.join(timeout=15)
            if writer_thread.is_alive():
                raise RuntimeError("Research collector did not finish its bounded flush")
        if fatal.is_set():
            raise RuntimeError("Research collector stopped after storage failure")
