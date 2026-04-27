import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";
import { needsGexRetry } from "../lib/useGex";

describe("needsGexRetry", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-22T14:00:00-04:00"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("retries when the same-day scan is stale intraday", () => {
    expect(needsGexRetry({ scan_time: new Date(Date.now() - 120_000).toISOString() } as never)).toBe(true);
  });

  it("does not retry when the same-day scan is still fresh", () => {
    expect(needsGexRetry({ scan_time: new Date(Date.now() - 20_000).toISOString() } as never)).toBe(false);
  });
});
