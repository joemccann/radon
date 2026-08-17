import { describe, it, expect } from "vitest";
import { planDepthAdmission } from "./depthBudget.js";

const A = { id: "client-a" };
const B = { id: "client-b" };

function ticket(key, focusedAt, ...subscribers) {
  return { key, focusedAt, subscribers: new Set(subscribers) };
}

describe("planDepthAdmission — per-client depth budget (R-082)", () => {
  it("admits without eviction while under the budget", () => {
    const plan = planDepthAdmission({
      activeTickets: [ticket("SPY", 1, A)],
      requestingClient: A,
      exceptKey: "QQQ",
      maxConcurrent: 3,
    });
    expect(plan).toEqual({ admit: true, evictKeys: [] });
  });

  it("recycles the requesting client's own oldest ticket at the cap", () => {
    const plan = planDepthAdmission({
      activeTickets: [ticket("SPY", 3, A), ticket("QQQ", 1, A), ticket("IWM", 2, A)],
      requestingClient: A,
      exceptKey: "TSLA",
      maxConcurrent: 3,
    });
    expect(plan).toEqual({ admit: true, evictKeys: ["QQQ"] });
  });

  it("never evicts another session's leg: refuses instead", () => {
    // The R-082 failure: two sessions with 2-leg implied books evicted each
    // other's legs through the relay-global cap, and the victim silently lost
    // half its montage. The budget must refuse the newcomer, not evict.
    const plan = planDepthAdmission({
      activeTickets: [ticket("ES", 1, A), ticket("NQ", 2, A), ticket("SPY", 3, A)],
      requestingClient: B,
      exceptKey: "QQQ",
      maxConcurrent: 3,
    });
    expect(plan).toEqual({ admit: false, evictKeys: [] });
  });

  it("never recycles a ticket shared with another session", () => {
    const plan = planDepthAdmission({
      activeTickets: [ticket("SPY", 1, A, B), ticket("ES", 2, B), ticket("NQ", 3, B)],
      requestingClient: B,
      exceptKey: "QQQ",
      maxConcurrent: 3,
    });
    // B's only exclusive tickets are ES (oldest) and NQ; SPY is shared with A
    // and must survive even though it is the oldest overall.
    expect(plan).toEqual({ admit: true, evictKeys: ["ES"] });
  });

  it("never evicts the key being (re)subscribed", () => {
    const plan = planDepthAdmission({
      activeTickets: [ticket("SPY", 1, A), ticket("QQQ", 2, A), ticket("IWM", 3, A)],
      requestingClient: A,
      exceptKey: "SPY",
      maxConcurrent: 3,
    });
    expect(plan).toEqual({ admit: true, evictKeys: ["QQQ"] });
  });

  it("refuses at the cap when there is no requesting client (restore path)", () => {
    const plan = planDepthAdmission({
      activeTickets: [ticket("SPY", 1, A), ticket("QQQ", 2, A), ticket("IWM", 3, B)],
      requestingClient: null,
      exceptKey: "TSLA",
      maxConcurrent: 3,
    });
    expect(plan).toEqual({ admit: false, evictKeys: [] });
  });
});
