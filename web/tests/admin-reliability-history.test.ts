import { describe, it, expect } from "vitest";
import {
  serviceHistorySummaries,
  deployMarkers,
  reliabilityRollup,
  DEPLOY_SERVICE,
  RELIABILITY_WINDOW_MS,
  type ServiceHealthEventRow,
} from "../lib/adminReliability";

// Window: 7 days, 2026-06-01T00:00Z -> 2026-06-08T00:00Z (168h).
const WINDOW_START = Date.parse("2026-06-01T00:00:00Z");
const WINDOW_END = Date.parse("2026-06-08T00:00:00Z");
const opts = { windowStartMs: WINDOW_START, windowEndMs: WINDOW_END };

const HOUR_MS = 3_600_000;

function ev(service: string, state: string, createdAt: string, detail: string | null = null): ServiceHealthEventRow {
  return { service, state, detail, created_at: createdAt };
}

describe("serviceHistorySummaries", () => {
  it("steady-ok service with baseline and zero events is 100% up, zero transitions", () => {
    const out = serviceHistorySummaries([], { ...opts, baseline: { "vcg-scan": "ok" } });
    expect(out).toHaveLength(1);
    const s = out[0];
    expect(s.service).toBe("vcg-scan");
    expect(s.uptimePct).toBe(100);
    expect(s.transitions).toBe(0);
    expect(s.incidents).toBe(0);
    expect(s.mttrMs).toBeNull();
    expect(s.currentState).toBe("ok");
  });

  it("one resolved 6h incident scores uptime, incidents, and MTTR", () => {
    const events = [
      ev("cri-scan", "error", "2026-06-02T00:00:00Z", '{"message":"boom"}'),
      ev("cri-scan", "ok", "2026-06-02T06:00:00Z"),
    ];
    const [s] = serviceHistorySummaries(events, { ...opts, baseline: { "cri-scan": "ok" } });
    expect(s.uptimePct).toBeCloseTo((162 / 168) * 100, 10);
    expect(s.transitions).toBe(2);
    expect(s.incidents).toBe(1);
    expect(s.resolvedIncidents).toBe(1);
    expect(s.mttrMs).toBe(6 * HOUR_MS);
    expect(s.currentState).toBe("ok");
  });

  it("an unresolved error at window end counts downtime but not MTTR", () => {
    const events = [ev("relay", "error", "2026-06-07T00:00:00Z")];
    const [s] = serviceHistorySummaries(events, { ...opts, baseline: { relay: "ok" } });
    expect(s.uptimePct).toBeCloseTo((144 / 168) * 100, 10);
    expect(s.incidents).toBe(1);
    expect(s.resolvedIncidents).toBe(0);
    expect(s.mttrMs).toBeNull();
    expect(s.currentState).toBe("error");
  });

  it("an incident already open at window start clamps repair time to the window", () => {
    const events = [ev("svc", "ok", "2026-06-01T12:00:00Z")];
    const [s] = serviceHistorySummaries(events, { ...opts, baseline: { svc: "error" } });
    expect(s.uptimePct).toBeCloseTo((156 / 168) * 100, 10);
    // Entered error before the window: not a NEW incident, but its recovery resolves it.
    expect(s.incidents).toBe(0);
    expect(s.resolvedIncidents).toBe(1);
    expect(s.mttrMs).toBe(12 * HOUR_MS);
  });

  it("without a baseline, observation starts at the first in-window event", () => {
    const events = [ev("late-svc", "ok", "2026-06-04T00:00:00Z")];
    const [s] = serviceHistorySummaries(events, opts);
    expect(s.uptimePct).toBe(100);
    expect(s.observedMs).toBe(4 * 24 * HOUR_MS);
  });

  it("no baseline and no events yields null uptime (honest: no data)", () => {
    const out = serviceHistorySummaries([], opts);
    expect(out).toEqual([]);
  });

  it("ignores events outside the window and deploy rows", () => {
    const events = [
      ev("svc", "error", "2026-05-20T00:00:00Z"), // before window — ignored
      ev(DEPLOY_SERVICE, "ok", "2026-06-03T00:00:00Z", "abc1234"),
      ev("svc", "ok", "2026-06-04T00:00:00Z"),
    ];
    const out = serviceHistorySummaries(events, opts);
    expect(out.map((s) => s.service)).toEqual(["svc"]);
    expect(out[0].transitions).toBe(1);
  });

  it("non-error, non-ok states (syncing/paused) count as up", () => {
    const events = [
      ev("svc", "syncing", "2026-06-02T00:00:00Z"),
      ev("svc", "ok", "2026-06-02T01:00:00Z"),
    ];
    const [s] = serviceHistorySummaries(events, { ...opts, baseline: { svc: "ok" } });
    expect(s.uptimePct).toBe(100);
    expect(s.incidents).toBe(0);
  });
});

describe("deployMarkers", () => {
  it("extracts deploy rows newest-first with the SHA from detail", () => {
    const events = [
      ev(DEPLOY_SERVICE, "ok", "2026-06-02T10:00:00Z", "aaa1111"),
      ev("svc", "ok", "2026-06-03T00:00:00Z"),
      ev(DEPLOY_SERVICE, "ok", "2026-06-05T10:00:00Z", "bbb2222"),
    ];
    expect(deployMarkers(events)).toEqual([
      { sha: "bbb2222", at: "2026-06-05T10:00:00Z" },
      { sha: "aaa1111", at: "2026-06-02T10:00:00Z" },
    ]);
  });

  it("missing detail yields a null sha", () => {
    expect(deployMarkers([ev(DEPLOY_SERVICE, "ok", "2026-06-05T10:00:00Z")])).toEqual([
      { sha: null, at: "2026-06-05T10:00:00Z" },
    ]);
  });

  it("empty input yields empty list", () => {
    expect(deployMarkers([])).toEqual([]);
  });
});

describe("reliabilityRollup", () => {
  it("aggregates worst uptime, top flapper, incident counts, and deploys", () => {
    const events = [
      // healthy-svc: one quick blip, resolved in 1h
      ev("healthy-svc", "error", "2026-06-02T00:00:00Z"),
      ev("healthy-svc", "ok", "2026-06-02T01:00:00Z"),
      // flappy-svc: two incidents, 6h + 2h, both resolved
      ev("flappy-svc", "error", "2026-06-03T00:00:00Z"),
      ev("flappy-svc", "ok", "2026-06-03T06:00:00Z"),
      ev("flappy-svc", "error", "2026-06-05T00:00:00Z"),
      ev("flappy-svc", "ok", "2026-06-05T02:00:00Z"),
      // deploys
      ev(DEPLOY_SERVICE, "ok", "2026-06-04T00:00:00Z", "abc1234"),
    ];
    const baseline = { "healthy-svc": "ok", "flappy-svc": "ok" };
    const r = reliabilityRollup(events, { ...opts, baseline });

    expect(r.worstUptime?.service).toBe("flappy-svc");
    expect(r.worstUptime?.uptimePct).toBeCloseTo((160 / 168) * 100, 10);
    expect(r.totalTransitions).toBe(6); // deploys excluded
    expect(r.topFlapper).toEqual({ service: "flappy-svc", transitions: 4 });
    expect(r.incidents).toBe(3);
    expect(r.resolvedIncidents).toBe(3);
    // mean of 1h, 6h, 2h = 3h
    expect(r.mttrMs).toBe(3 * HOUR_MS);
    expect(r.deploys).toEqual([{ sha: "abc1234", at: "2026-06-04T00:00:00Z" }]);
  });

  it("empty events yields a null-shaped rollup", () => {
    const r = reliabilityRollup([], opts);
    expect(r.worstUptime).toBeNull();
    expect(r.totalTransitions).toBe(0);
    expect(r.topFlapper).toBeNull();
    expect(r.incidents).toBe(0);
    expect(r.mttrMs).toBeNull();
    expect(r.deploys).toEqual([]);
  });
});

describe("window constant", () => {
  it("default reliability window is 7 days", () => {
    expect(RELIABILITY_WINDOW_MS).toBe(7 * 24 * HOUR_MS);
  });
});
