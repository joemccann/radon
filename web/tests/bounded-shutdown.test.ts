/**
 * radon-nextjs sat in `final-sigterm` until systemd's 90 s SIGKILL on
 * 2026-08-24 (15:10, 18:17, 20:31 UTC) because Next's graceful close waits
 * for every open connection, and RTH connections include radonFetch calls
 * with 130 s timeouts against an API the deploy had already stopped. The
 * deploy waits 60 s, so each of those releases rolled back. The server's own
 * cleanup still runs; this caps the drain.
 */
import { EventEmitter } from "node:events";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { installBoundedShutdown } from "../lib/boundedShutdown";

describe("installBoundedShutdown", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("exits with the signal's conventional code once the grace elapses", () => {
    const proc = new EventEmitter();
    const exit = vi.fn();
    installBoundedShutdown({ proc, exit, graceMs: 10_000 });

    proc.emit("SIGTERM");

    expect(exit).not.toHaveBeenCalled();
    vi.advanceTimersByTime(9_999);
    expect(exit).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(exit).toHaveBeenCalledWith(143);
  });

  it("arms once across repeated signals", () => {
    const proc = new EventEmitter();
    const exit = vi.fn();
    installBoundedShutdown({ proc, exit, graceMs: 10_000 });

    proc.emit("SIGTERM");
    proc.emit("SIGINT");
    vi.advanceTimersByTime(10_000);

    expect(exit).toHaveBeenCalledTimes(1);
  });

  it("does not exit when no signal arrived", () => {
    const proc = new EventEmitter();
    const exit = vi.fn();
    installBoundedShutdown({ proc, exit, graceMs: 10_000 });

    vi.advanceTimersByTime(60_000);

    expect(exit).not.toHaveBeenCalled();
  });
});
