import { describe, it, expect } from "vitest";
import { classifyRateTier, demoRateLimitKey } from "@/lib/demo/rateTier";

describe("classifyRateTier", () => {
  it("AI routes are tier D regardless of method", () => {
    expect(classifyRateTier("POST", "/api/assistant")).toBe("D");
    expect(classifyRateTier("GET", "/api/ticker/seasonality")).toBe("D");
    expect(classifyRateTier("GET", "/api/ticker/info")).toBe("D");
    expect(classifyRateTier("POST", "/api/newsfeed/explain")).toBe("D");
  });

  it("expensive scans are tier B before the mutation bucket catches them", () => {
    expect(classifyRateTier("POST", "/api/gex")).toBe("B");
    expect(classifyRateTier("POST", "/api/gamma-rotation")).toBe("B");
    expect(classifyRateTier("POST", "/api/breadth")).toBe("B");
    expect(classifyRateTier("POST", "/api/leap/scan")).toBe("B");
    expect(classifyRateTier("POST", "/api/garch-convergence/scan")).toBe("B");
    expect(classifyRateTier("POST", "/api/scanner/vol-skew-mr/scan")).toBe("B");
  });

  it("operator account mutations stay in the write tier", () => {
    expect(classifyRateTier("POST", "/api/pi")).toBe("C");
    expect(classifyRateTier("POST", "/api/performance")).toBe("C");
    expect(classifyRateTier("POST", "/api/flow-analysis")).toBe("C");
  });

  it("keeps cached measurement reads out of producer quotas", () => {
    expect(classifyRateTier("GET", "/api/performance")).toBe("A");
    expect(classifyRateTier("GET", "/api/scanner/theta")).toBe("A");
    expect(classifyRateTier("GET", "/api/regime")).toBe("A");
    expect(classifyRateTier("GET", "/api/regime/gex")).toBe("A");
  });

  it("writes are tier C", () => {
    expect(classifyRateTier("POST", "/api/orders/place")).toBe("C");
    expect(classifyRateTier("POST", "/api/alerts")).toBe("C");
    expect(classifyRateTier("PUT", "/api/watchlist")).toBe("C");
    expect(classifyRateTier("DELETE", "/api/journal/123")).toBe("C");
  });

  it("websocket tickets use the reconnect-burst tier", () => {
    expect(classifyRateTier("POST", "/api/ib/ws-ticket")).toBe("E");
  });

  it("headline snapshots use an isolated realtime-polling budget", () => {
    expect(classifyRateTier("GET", "/api/headlines")).toBe("G");
  });

  it("passive shell polling uses a budget sized for the shipped cadence", () => {
    expect(classifyRateTier("GET", "/api/futures-quote")).toBe("I");
    expect(classifyRateTier("GET", "/api/service-health")).toBe("I");
    expect(classifyRateTier("GET", "/api/flex-token")).toBe("I");
    expect(classifyRateTier("GET", "/api/risk-free-rate")).toBe("I");
    expect(classifyRateTier("GET", "/api/portfolio")).toBe("I");
    expect(classifyRateTier("GET", "/api/orders")).toBe("I");
    expect(classifyRateTier("POST", "/api/orders")).toBe("C");
  });

  it("the bounded batch read gets its own fan-out tier", () => {
    // One POST reaches up to 30 symbols, each costing a UW call plus a Yahoo
    // fallback, so the ordinary-read ceiling priced it as a single cheap read.
    // It is not a producer either: its daily bucket must stay off the one the
    // scans share.
    expect(classifyRateTier("POST", "/api/previous-close")).toBe("M");
  });

  it("keeps the fan-out tier on a per-resource key", () => {
    expect(demoRateLimitKey("M", "user", "/api/previous-close"))
      .toBe("user:resource:previous-close");
  });

  it("plain reads are tier A", () => {
    expect(classifyRateTier("get", "/api/blotter")).toBe("A"); // case-insensitive
    expect(classifyRateTier("GET", "/api/preferences")).toBe("A");
  });

  it("keeps spend and mutation controls user-global", () => {
    expect(demoRateLimitKey("C", "user", "/api/watchlist")).toBe("user");
    expect(demoRateLimitKey("D", "user", "/api/assistant")).toBe("user");
    expect(demoRateLimitKey("E", "user", "/api/ib/ws-ticket")).toBe("user");
  });
});
