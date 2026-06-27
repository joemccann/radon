import { describe, it, expect } from "vitest";
import {
  deriveDemoStatus,
  formatExpiryCountdown,
  sumAiBurn,
  statusTone,
} from "@/lib/demo/demoUsersView";

const NOW = Date.parse("2026-06-25T12:00:00-04:00");

describe("deriveDemoStatus", () => {
  it("revoked wins over everything", () => {
    expect(
      deriveDemoStatus({ status: "revoked", expires_at: "2099-01-01T00:00:00-04:00" }, NOW),
    ).toBe("revoked");
  });
  it("a past expiry reads expired even if the row says active", () => {
    expect(
      deriveDemoStatus({ status: "active", expires_at: "2026-06-24T16:00:00-04:00" }, NOW),
    ).toBe("expired");
  });
  it("a future expiry reads active", () => {
    expect(
      deriveDemoStatus({ status: "active", expires_at: "2026-06-27T16:00:00-04:00" }, NOW),
    ).toBe("active");
  });
});

describe("formatExpiryCountdown", () => {
  it("future renders an 'in …' span", () => {
    // ~2 days 4 hours ahead
    expect(formatExpiryCountdown("2026-06-27T16:00:00-04:00", NOW)).toBe("in 2d 4h");
  });
  it("past renders an 'expired … ago' span", () => {
    expect(formatExpiryCountdown("2026-06-25T09:00:00-04:00", NOW)).toBe("expired 3h ago");
  });
  it("null / unparseable → dash", () => {
    expect(formatExpiryCountdown(null, NOW)).toBe("—");
    expect(formatExpiryCountdown("not-a-date", NOW)).toBe("—");
  });
});

describe("sumAiBurn", () => {
  it("sums all endpoints", () => {
    expect(sumAiBurn({ assistant: 3, "ticker/info": 2 })).toBe(5);
    expect(sumAiBurn({})).toBe(0);
  });
});

describe("statusTone", () => {
  it("maps to brand tones", () => {
    expect(statusTone("active")).toBe("positive");
    expect(statusTone("revoked")).toBe("negative");
    expect(statusTone("expired")).toBe("neutral");
  });
});
