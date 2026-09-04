import { describe, it, expect } from "vitest";
import { classifyRateTier, demoRateLimitKey } from "@/lib/demo/rateTier";

describe("classifyRateTier", () => {
  it("AI routes are tier D regardless of method", () => {
    expect(classifyRateTier("POST", "/api/assistant")).toBe("D");
    expect(classifyRateTier("GET", "/api/ticker/seasonality")).toBe("D");
    expect(classifyRateTier("GET", "/api/ticker/info")).toBe("D");
  });

  it("expensive scans are tier B before the mutation bucket catches them", () => {
    expect(classifyRateTier("POST", "/api/vcg/scan")).toBe("B");
    expect(classifyRateTier("POST", "/api/gex/scan")).toBe("B");
    expect(classifyRateTier("POST", "/api/leap/scan")).toBe("B");
    expect(classifyRateTier("POST", "/api/pi/exec")).toBe("B");
    expect(classifyRateTier("GET", "/api/performance/foo")).toBe("B");
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
