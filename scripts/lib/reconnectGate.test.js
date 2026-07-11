import { describe, expect, it, vi } from "vitest";

import { createReconnectGate } from "./reconnectGate.js";


describe("createReconnectGate", () => {
  it("invalidates a pending reconnect when the socket recovers", () => {
    const callback = vi.fn();
    const scheduled = [];
    const cleared = [];
    const gate = createReconnectGate({
      delayMs: 5000,
      setTimer: (fn) => {
        scheduled.push(fn);
        return scheduled.length;
      },
      clearTimer: (id) => cleared.push(id),
    });

    expect(gate.schedule(callback)).toBe(true);
    gate.invalidate();
    scheduled[0](); // queued callback races after clearTimeout

    expect(cleared).toEqual([1]);
    expect(callback).not.toHaveBeenCalled();
  });

  it("deduplicates pending work and allows a new generation after rotation", () => {
    const scheduled = [];
    const gate = createReconnectGate({
      delayMs: 5000,
      setTimer: (fn) => {
        scheduled.push(fn);
        return scheduled.length;
      },
      clearTimer: vi.fn(),
    });

    expect(gate.schedule(vi.fn())).toBe(true);
    expect(gate.schedule(vi.fn())).toBe(false);
    gate.invalidate();
    expect(gate.schedule(vi.fn())).toBe(true);
  });

  it("uses the same cancellable gate for short rotation delays", () => {
    const delays = [];
    const gate = createReconnectGate({
      delayMs: 5000,
      setTimer: (_fn, delay) => {
        delays.push(delay);
        return delays.length;
      },
      clearTimer: vi.fn(),
    });

    expect(gate.schedule(vi.fn(), 2000)).toBe(true);
    expect(delays).toEqual([2000]);
    expect(gate.schedule(vi.fn())).toBe(false);
  });
});
