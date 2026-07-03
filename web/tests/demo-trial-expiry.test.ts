import { describe, it, expect, vi } from "vitest";
import { computeTrialExpiry, DEFAULT_DEMO_TRADING_DAYS } from "@/lib/demo/trialExpiry";

describe("computeTrialExpiry (client)", () => {
  it("demo trials default to 2 trading days", () => {
    expect(DEFAULT_DEMO_TRADING_DAYS).toBe(2);
  });

  it("delegates to the injected fetcher and maps the response", async () => {
    const fetcher = vi.fn().mockResolvedValue({
      started_at: "2026-06-25T09:30:00-04:00",
      expires_at: "2026-06-29T16:00:00-04:00",
      trading_days: 3,
      active: true,
    });
    const out = await computeTrialExpiry("2026-06-25T09:30:00-04:00", { fetcher });
    expect(fetcher).toHaveBeenCalledWith(
      "2026-06-25T09:30:00-04:00",
      DEFAULT_DEMO_TRADING_DAYS,
    );
    expect(out).toEqual({
      startedAt: "2026-06-25T09:30:00-04:00",
      expiresAt: "2026-06-29T16:00:00-04:00",
      tradingDays: 3,
      active: true,
    });
  });

  it("passes a custom tradingDays through", async () => {
    const fetcher = vi.fn().mockResolvedValue({
      started_at: "x",
      expires_at: "y",
      trading_days: 5,
      active: true,
    });
    await computeTrialExpiry("x", { tradingDays: 5, fetcher });
    expect(fetcher).toHaveBeenCalledWith("x", 5);
  });
});
