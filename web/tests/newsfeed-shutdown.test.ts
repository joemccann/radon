/**
 * radon-newsfeed hung on SIGTERM for the full 90 s systemd stop timeout on
 * 2026-08-24 (18:13, 18:14, 18:19 UTC): the handler only aborted a controller,
 * and a Playwright scrape mid-flight does not observe that abort, so the
 * deploy's 60 s wait for the unit to go inactive expired and rolled back three
 * releases in a row. Shutdown now bounds itself: abort, then exit after a grace.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { createShutdown } = await import("../../scripts/newsfeed/scheduler.js");

describe("createShutdown", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(console, "info").mockImplementation(() => {});
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("aborts the run and exits once the grace elapses", () => {
    const controller = new AbortController();
    const exit = vi.fn();
    const shutdown = createShutdown({ controller, exit, graceMs: 10_000 });

    shutdown("SIGTERM");

    expect(controller.signal.aborted).toBe(true);
    expect(exit).not.toHaveBeenCalled();
    vi.advanceTimersByTime(9_999);
    expect(exit).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(exit).toHaveBeenCalledWith(0);
  });

  // T-229: the exit CODE is the whole contract with systemd. 75 (EX_TEMPFAIL)
  // is what radon-newsfeed.service maps through SuccessExitStatus=75 so a
  // truncated cycle ends `inactive` rather than `failed` -> start-limit-hit.
  // Nothing covered the 75 branch, so the value could have drifted to any
  // non-zero and only production would have noticed.
  it("exits 75 when the grace expires with a cycle still in flight", () => {
    const controller = new AbortController();
    const exit = vi.fn();
    const shutdown = createShutdown({
      controller,
      exit,
      graceMs: 10_000,
      isCycleInFlight: () => true,
    });

    shutdown("SIGTERM");
    vi.advanceTimersByTime(10_000);

    expect(exit).toHaveBeenCalledWith(75);
  });

  it("exits 0 when the grace expires between cycles", () => {
    const controller = new AbortController();
    const exit = vi.fn();
    const shutdown = createShutdown({
      controller,
      exit,
      graceMs: 10_000,
      isCycleInFlight: () => false,
    });

    shutdown("SIGTERM");
    vi.advanceTimersByTime(10_000);

    expect(exit).toHaveBeenCalledWith(0);
  });

  it("is idempotent across repeated signals", () => {
    const controller = new AbortController();
    const exit = vi.fn();
    const shutdown = createShutdown({ controller, exit, graceMs: 10_000 });

    shutdown("SIGTERM");
    shutdown("SIGINT");
    vi.advanceTimersByTime(10_000);

    expect(exit).toHaveBeenCalledTimes(1);
  });
});
