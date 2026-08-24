/**
 * 2026-08-24 scan storm: the GET routes for GEX / VCG / regime each fired a
 * background POST whenever the snapshot looked stale, with an in-flight flag
 * that a rejected POST cleared instantly. Clients poll every 5 s while stale,
 * so a 502 from a lane-exhausted FastAPI re-fired a scan subprocess on every
 * poll. One shared trigger now dedupes in flight AND arms a backoff on
 * failure, so a refused scan is not asked for again until the window lapses.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createBackgroundScanTrigger } from "../lib/backgroundScan";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (err: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe("createBackgroundScanTrigger", () => {
  let now = 1_000_000;
  const clock = () => now;

  beforeEach(() => {
    now = 1_000_000;
    vi.spyOn(console, "log").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("dedupes while a scan is in flight", async () => {
    const pending = deferred<void>();
    const run = vi.fn(() => pending.promise);
    const trigger = createBackgroundScanTrigger({ label: "VCG", run, now: clock });

    expect(trigger()).toBe(true);
    expect(trigger()).toBe(false);
    expect(run).toHaveBeenCalledTimes(1);

    pending.resolve();
    await flush();
    expect(trigger()).toBe(true);
    expect(run).toHaveBeenCalledTimes(2);
  });

  it("arms a backoff after a failure instead of re-firing on the next poll", async () => {
    const run = vi.fn(() => Promise.reject(new Error("Radon API 502: Subprocess capacity exhausted")));
    const trigger = createBackgroundScanTrigger({ label: "GEX", run, now: clock, backoffMs: 60_000 });

    expect(trigger()).toBe(true);
    await flush();
    expect(trigger()).toBe(false);
    now += 59_000;
    expect(trigger()).toBe(false);
    expect(run).toHaveBeenCalledTimes(1);

    now += 1_000;
    expect(trigger()).toBe(true);
    expect(run).toHaveBeenCalledTimes(2);
  });

  it("does not block after a success", async () => {
    const run = vi.fn(() => Promise.resolve());
    const trigger = createBackgroundScanTrigger({ label: "CRI", run, now: clock });

    expect(trigger()).toBe(true);
    await flush();
    expect(trigger()).toBe(true);
    expect(run).toHaveBeenCalledTimes(2);
  });
});
