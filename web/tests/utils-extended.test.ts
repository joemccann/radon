import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  createTimestamp,
  sleep,
  extractSingleArrayFromObject,
  formatJsonObject,
  formatGenericPayload,
  formatAssistantPayload,
  formatPiPayload,
  formatPortfolioPayload,
  formatJournalPayload,
} from "../lib/utils";

// =============================================================================
// createTimestamp
// =============================================================================

describe("createTimestamp", () => {
  it("returns HH:MM format string", () => {
    const ts = createTimestamp();
    // Should match HH:MM in 24-hour format
    expect(ts).toMatch(/^\d{2}:\d{2}$/);
  });

  it("returns consistent format across calls", () => {
    const ts1 = createTimestamp();
    const ts2 = createTimestamp();
    expect(ts1).toMatch(/^\d{2}:\d{2}$/);
    expect(ts2).toMatch(/^\d{2}:\d{2}$/);
  });
});

// =============================================================================
// sleep
// =============================================================================

describe("sleep", () => {
  it("resolves after the specified delay", async () => {
    vi.useFakeTimers();
    const promise = sleep(100);
    vi.advanceTimersByTime(100);
    await expect(promise).resolves.toBeUndefined();
    vi.useRealTimers();
  });

  it("returns a promise", () => {
    const result = sleep(0);
    expect(result).toBeInstanceOf(Promise);
  });
});

// =============================================================================
// extractSingleArrayFromObject
// =============================================================================

describe("extractSingleArrayFromObject", () => {
  it("returns null when no array fields exist", () => {
    const result = extractSingleArrayFromObject({ a: 1, b: "hello" });
    expect(result).toBeNull();
  });

  it("returns null when multiple array fields exist", () => {
    const result = extractSingleArrayFromObject({
      items: [{ name: "a" }],
      tags: [{ tag: "b" }],
    });
    expect(result).toBeNull();
  });

  it("returns null when single array field is empty", () => {
    const result = extractSingleArrayFromObject({ items: [] });
    expect(result).toBeNull();
  });

  it("returns null when single array contains non-objects", () => {
    const result = extractSingleArrayFromObject({ items: [1, 2, 3] });
    expect(result).toBeNull();
  });

  it("returns formatted table for valid single array field", () => {
    const result = extractSingleArrayFromObject({
      candidates: [
        { ticker: "AAPL", score: 80 },
        { ticker: "GOOG", score: 70 },
      ],
    });
    expect(result).not.toBeNull();
    expect(result).toContain("CANDIDATES:");
    expect(result).toContain("| Ticker | Score |");
    expect(result).toContain("AAPL");
    expect(result).toContain("GOOG");
  });

  it("uppercases the field name as section header", () => {
    const result = extractSingleArrayFromObject({
      alerts: [{ ticker: "SPY", type: "sweep" }],
    });
    expect(result).not.toBeNull();
    expect(result!.startsWith("ALERTS:")).toBe(true);
  });
});

// =============================================================================
// formatJsonObject
// =============================================================================

describe("formatJsonObject", () => {
  it("handles null values", () => {
    const result = formatJsonObject({ key: null });
    expect(result).toContain("Key: N/A");
  });

  it("handles string values", () => {
    const result = formatJsonObject({ name: "test" });
    expect(result).toBe("Name: test");
  });

  it("handles number values", () => {
    const result = formatJsonObject({ count: 42 });
    expect(result).toBe("Count: 42");
  });

  it("handles boolean values", () => {
    const result = formatJsonObject({ active: true });
    expect(result).toBe("Active: true");
  });

  it("handles nested objects", () => {
    const result = formatJsonObject({
      outer: { inner: "value" } as Record<string, unknown>,
    } as Record<string, unknown>);
    expect(result).toContain("Outer:");
    expect(result).toContain("  Inner: value");
  });

  it("handles arrays of objects", () => {
    const result = formatJsonObject({
      items: [
        { ticker: "AAPL", price: 200 },
        { ticker: "GOOG", price: 175 },
      ],
    } as Record<string, unknown>);
    expect(result).toContain("Items:");
    expect(result).toContain("1. ticker: AAPL | price: 200");
    expect(result).toContain("2. ticker: GOOG | price: 175");
  });

  it("handles empty arrays (shows '- none')", () => {
    const result = formatJsonObject({ items: [] } as Record<string, unknown>);
    expect(result).toContain("Items:");
    expect(result).toContain("  - none");
  });

  it("handles arrays of primitives", () => {
    const result = formatJsonObject({
      tags: ["alpha", "beta"],
    } as Record<string, unknown>);
    expect(result).toContain("Tags:");
    expect(result).toContain("1. alpha");
    expect(result).toContain("2. beta");
  });

  it("respects indent parameter", () => {
    const result = formatJsonObject({ name: "test" }, 1);
    expect(result).toBe("  Name: test");
  });

  it("handles deeper indentation for nested", () => {
    const result = formatJsonObject(
      { outer: { inner: "val" } as Record<string, unknown> } as Record<string, unknown>,
      0,
    );
    expect(result).toContain("Outer:");
    expect(result).toContain("  Inner: val");
  });

  it("skips empty string values", () => {
    // valueToText returns "" for objects, empty strings are skipped by formatJsonObject
    const result = formatJsonObject({ name: "" });
    // empty string from valueToText is falsy, so it's skipped
    expect(result).toBe("");
  });

  it("handles arrays with null entries", () => {
    const result = formatJsonObject({
      items: [null, "hello"],
    } as Record<string, unknown>);
    expect(result).toContain("Items:");
    expect(result).toContain("1. N/A");
    expect(result).toContain("2. hello");
  });
});

// =============================================================================
// formatGenericPayload
// =============================================================================

describe("formatGenericPayload", () => {
  it("handles arrays of objects as table", () => {
    const result = formatGenericPayload([
      { ticker: "AAPL", score: 80 },
      { ticker: "GOOG", score: 70 },
    ]);
    expect(result).toContain("| Ticker | Score |");
    expect(result).toContain("AAPL");
  });

  it("handles objects with single array (extractSingleArrayFromObject path)", () => {
    const result = formatGenericPayload({
      candidates: [
        { ticker: "AAPL", score: 80 },
      ],
    });
    expect(result).toContain("CANDIDATES:");
    expect(result).toContain("AAPL");
  });

  it("handles flat objects", () => {
    const result = formatGenericPayload({ bankroll: 100000, status: "ok" });
    expect(result).toContain("Bankroll: 100000");
    expect(result).toContain("Status: ok");
  });

  it("handles primitive values", () => {
    const result = formatGenericPayload("hello world");
    expect(result).toBe("hello world");
  });

  it("handles numeric primitives", () => {
    const result = formatGenericPayload(42);
    expect(result).toBe("42");
  });

  it("handles empty array", () => {
    const result = formatGenericPayload([]);
    expect(result).toBe("No rows available.");
  });

  it("handles arrays of primitives (non-objects)", () => {
    // formatArrayAsTable returns null for non-object arrays, falls back to formatJsonObject
    const result = formatGenericPayload([1, 2, 3]);
    expect(result).toContain("1. 1");
    expect(result).toContain("2. 2");
    expect(result).toContain("3. 3");
  });
});

// =============================================================================
// formatAssistantPayload
// =============================================================================

describe("formatAssistantPayload", () => {
  it("passes through plain text unchanged", () => {
    expect(formatAssistantPayload("Hello world")).toBe("Hello world");
  });

  it("passes through non-JSON text", () => {
    expect(formatAssistantPayload("This is not JSON at all")).toBe("This is not JSON at all");
  });

  it("formats JSON object output", () => {
    const json = JSON.stringify({ bankroll: 100000, status: "ok" });
    const result = formatAssistantPayload(json);
    expect(result).toContain("Bankroll: 100000");
    expect(result).toContain("Status: ok");
  });

  it("formats JSON array output as table", () => {
    const json = JSON.stringify([
      { ticker: "AAPL", score: 80 },
      { ticker: "GOOG", score: 70 },
    ]);
    const result = formatAssistantPayload(json);
    expect(result).toContain("| Ticker | Score |");
  });

  it("handles empty string", () => {
    expect(formatAssistantPayload("")).toBe("");
  });

  it("handles whitespace-only string", () => {
    expect(formatAssistantPayload("   ")).toBe("");
  });
});

// =============================================================================
// formatPiPayload
// =============================================================================

describe("formatPiPayload", () => {
  it("routes 'scan' command to generic formatter", () => {
    const json = JSON.stringify([
      { ticker: "AAPL", score: 80 },
    ]);
    const result = formatPiPayload("scan", json);
    // Should use formatGenericPayload, which produces a table for object arrays
    expect(result).toContain("| Ticker | Score |");
  });

  it("routes 'discover' command to generic formatter", () => {
    const json = JSON.stringify({
      candidates: [{ ticker: "NET", score: 65 }],
    });
    const result = formatPiPayload("discover", json);
    expect(result).toContain("CANDIDATES:");
    expect(result).toContain("NET");
  });

  it("passes through non-JSON output as text", () => {
    const result = formatPiPayload("scan", "No data available");
    expect(result).toBe("No data available");
  });

  it("strips leading slash from command for routing", () => {
    const json = JSON.stringify({ bankroll: 50000, positions: [] });
    const result = formatPiPayload("/portfolio", json);
    expect(result).toContain("Portfolio Snapshot");
  });

  it("routes portfolio command correctly", () => {
    const json = JSON.stringify({
      bankroll: 981353,
      position_count: 5,
      defined_risk_count: 3,
      undefined_risk_count: 2,
      last_sync: "2026-03-05",
      positions: [],
    });
    const result = formatPiPayload("portfolio", json);
    expect(result).toContain("Portfolio Snapshot");
    expect(result).toContain("$981,353");
    expect(result).toContain("Positions: 5");
  });

  it("routes journal command correctly", () => {
    const json = JSON.stringify({
      trades: [
        { timestamp: "2026-03-01", ticker: "AAPL", decision: "BUY", confidence: "HIGH" },
      ],
    });
    const result = formatPiPayload("journal", json);
    expect(result).toContain("Recent Journal");
    expect(result).toContain("AAPL");
    expect(result).toContain("HIGH");
  });

  it("is case-insensitive for command routing", () => {
    const json = JSON.stringify({ bankroll: 1000, positions: [] });
    const result = formatPiPayload("PORTFOLIO", json);
    expect(result).toContain("Portfolio Snapshot");
  });
});

// =============================================================================
// formatPortfolioPayload (extended edge cases)
// =============================================================================

describe("formatPortfolioPayload extended", () => {
  it("formats positions with structured data", () => {
    const data = {
      bankroll: 100000,
      position_count: 1,
      defined_risk_count: 1,
      undefined_risk_count: 0,
      last_sync: "2026-03-05",
      positions: [
        {
          ticker: "GOOG",
          structure: "Bull Call Spread",
          expiry: "2026-06-19",
          risk_profile: "defined",
          entry_cost: 5200,
        },
      ],
    };
    const result = formatPortfolioPayload(data);
    expect(result).toContain("Portfolio Snapshot");
    expect(result).toContain("$100,000");
    expect(result).toContain("GOOG");
  });

  it("handles positions without structure info", () => {
    const data = {
      bankroll: 50000,
      positions: [
        { ticker: "AAPL" },
      ],
    };
    const result = formatPortfolioPayload(data);
    expect(result).toContain("AAPL");
  });

  it("handles missing positions array", () => {
    const data = { bankroll: 50000 };
    const result = formatPortfolioPayload(data);
    expect(result).toContain("No positions found.");
  });

  it("handles null payload fields gracefully", () => {
    const result = formatPortfolioPayload({});
    expect(result).toContain("Portfolio Snapshot");
    expect(result).toContain("No positions found.");
  });
});

// =============================================================================
// formatPortfolioPayload — human-readable snapshot header
// =============================================================================

describe("formatPortfolioPayload snapshot header", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-29T20:10:30Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const snapshot = {
    bankroll: 1315883,
    position_count: 10,
    defined_risk_count: 9,
    undefined_risk_count: 1,
    last_sync: "2026-08-29T20:06:24.768526+00:00",
    positions: [],
  };

  it("renders sync age plus an ET wall clock, never the raw ISO timestamp", () => {
    const result = formatPortfolioPayload(snapshot);
    expect(result).toContain("Synced 4m ago (16:06 ET)");
    expect(result).not.toContain("2026-08-29T20:06:24.768526+00:00");
    expect(result).not.toContain("Last Sync:");
  });

  it("collapses the risk split onto the positions line", () => {
    const result = formatPortfolioPayload(snapshot);
    expect(result).toContain("Positions: 10 (9 defined risk, 1 undefined risk)");
    expect(result).not.toContain("Defined Risk: 9");
    expect(result).not.toContain("Undefined Risk: 1");
  });

  it("says the sync time is unavailable when the payload has none", () => {
    const result = formatPortfolioPayload({ bankroll: 1000, positions: [] });
    expect(result).toContain("Sync time unavailable");
    expect(result).not.toContain("N/A");
    expect(result).not.toContain("Invalid Date");
  });

  it("says the sync time is unavailable when it cannot be parsed", () => {
    const result = formatPortfolioPayload({ ...snapshot, last_sync: "not-a-timestamp" });
    expect(result).toContain("Sync time unavailable");
    expect(result).not.toContain("unknown");
    expect(result).not.toContain("Invalid Date");
  });
});

// =============================================================================
// formatJournalPayload (extended edge cases)
// =============================================================================

describe("formatJournalPayload extended", () => {
  it("formats multiple trades as table when all are objects", () => {
    const data = {
      trades: [
        {
          timestamp: "2026-03-01",
          ticker: "AAPL",
          decision: "BUY",
          confidence: "HIGH",
          note: "Strong dark pool signal",
        },
        {
          timestamp: "2026-03-02",
          ticker: "GOOG",
          decision: "SELL",
          confidence: 0.85,
          note: "Exit on target",
        },
      ],
    };
    const result = formatJournalPayload(data);
    expect(result).toContain("Recent Journal");
    // formatArrayAsTable succeeds for uniform objects, produces markdown table
    expect(result).toContain("| Timestamp | Ticker | Decision | Confidence | Note |");
    expect(result).toContain("AAPL");
    expect(result).toContain("GOOG");
    expect(result).toContain("HIGH");
    expect(result).toContain("Strong dark pool signal");
  });

  it("formats trades as table with all fields visible", () => {
    const data = {
      trades: [
        { timestamp: "2026-03-01", ticker: "AAPL" },
      ],
    };
    const result = formatJournalPayload(data);
    expect(result).toContain("AAPL");
    // Single-object trades also get table treatment
    expect(result).toContain("| Timestamp | Ticker |");
  });

  it("handles non-object trades in array", () => {
    const data = { trades: ["raw trade string"] };
    const result = formatJournalPayload(data);
    expect(result).toContain("1. raw trade string");
  });

  it("handles missing trades field", () => {
    const result = formatJournalPayload({});
    expect(result).toContain("No trades logged.");
  });
});
