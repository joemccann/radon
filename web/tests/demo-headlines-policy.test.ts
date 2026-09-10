import { describe, expect, it } from "vitest";
import {
  DEMO_HEADLINES_BURST_PER_MINUTE,
  DEMO_HEADLINES_DAILY_LIMIT,
  DEMO_HEADLINES_POLL_MS,
  DEMO_HEADLINES_SUPPORTED_PERSISTENT_TABS,
} from "@/lib/demo/headlinesPolicy";

describe("demo headline polling budget", () => {
  it("supports three persistent tabs without exhausting either ceiling", () => {
    const pollsPerTabPerDay = Math.ceil(24 * 60 * 60_000 / DEMO_HEADLINES_POLL_MS);

    expect(DEMO_HEADLINES_SUPPORTED_PERSISTENT_TABS).toBe(3);
    expect(DEMO_HEADLINES_BURST_PER_MINUTE).toBeGreaterThanOrEqual(
      DEMO_HEADLINES_SUPPORTED_PERSISTENT_TABS,
    );
    expect(DEMO_HEADLINES_DAILY_LIMIT).toBeGreaterThanOrEqual(
      pollsPerTabPerDay * DEMO_HEADLINES_SUPPORTED_PERSISTENT_TABS,
    );
  });
});
