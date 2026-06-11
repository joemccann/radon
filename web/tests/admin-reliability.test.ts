import { describe, it, expect } from "vitest";
import {
  unitKind,
  unitVerdict,
  unitDependents,
  serviceControlDisabledReason,
  humanizeDetail,
  UNIT_DEPENDENTS,
} from "../lib/adminFormat";
import {
  livenessSummary,
  freshnessSummary,
  ibAuthSummary,
  externalProbeSummary,
  EXTERNAL_PROBE_STALE_AFTER_SECONDS,
} from "../lib/adminReliability";
import type { UnitStatus, AdminHealthPayload, ServiceHealthRow, ExternalProbeRow } from "../lib/adminTypes";

function unit(p: Partial<UnitStatus>): UnitStatus {
  return {
    unit: "radon-x.service",
    load_state: "loaded",
    active_state: "active",
    sub_state: "running",
    description: "x",
    can_control: true,
    ...p,
  };
}

const daemonRunning = unit({ unit: "radon-api.service", active_state: "active", sub_state: "running", uptime_secs: 12000 });
const daemonStopped = unit({ unit: "radon-api.service", active_state: "inactive", sub_state: "dead", uptime_secs: null });
const daemonFailed = unit({ active_state: "failed", sub_state: "failed", uptime_secs: null });
const jobIdleOk = unit({ unit: "radon-leap.service", active_state: "inactive", sub_state: "dead", uptime_secs: null, last_exit_code: 0 });
const jobFailed = unit({ active_state: "inactive", sub_state: "dead", uptime_secs: null, last_exit_code: 3 });
const starting = unit({ active_state: "activating", sub_state: "start", uptime_secs: null });
const oneshotExited = unit({ active_state: "active", sub_state: "exited", uptime_secs: null, last_exit_code: 0 });
const uncontrollable = unit({ can_control: false, active_state: "active", sub_state: "running" });

describe("unitKind", () => {
  it("daemon when uptime_secs is a number", () => expect(unitKind(daemonRunning)).toBe("daemon"));
  it("job when no uptime_secs", () => expect(unitKind(jobIdleOk)).toBe("job"));
});

describe("unitVerdict", () => {
  it("running daemon", () => expect(unitVerdict(daemonRunning)).toEqual({ label: "Running", tone: "positive" }));
  it("stopped daemon is an outage", () => expect(unitVerdict(daemonStopped)).toEqual({ label: "Stopped", tone: "negative" }));
  it("failed", () => expect(unitVerdict(daemonFailed)).toEqual({ label: "Failed", tone: "negative" }));
  it("idle job rc=0 is healthy", () => expect(unitVerdict(jobIdleOk)).toEqual({ label: "Idle", tone: "positive" }));
  it("failed job rc!=0", () => expect(unitVerdict(jobFailed)).toEqual({ label: "Failed", tone: "negative" }));
  it("starting", () => expect(unitVerdict(starting)).toEqual({ label: "Starting", tone: "warning" }));
  it("oneshot active+exited reads idle/positive", () => expect(unitVerdict(oneshotExited)).toEqual({ label: "Idle", tone: "positive" }));
  it("uncontrollable is unknown", () => expect(unitVerdict(uncontrollable)).toEqual({ label: "Unknown", tone: "neutral" }));
});

describe("unitDependents / cascade map", () => {
  it("ib-gateway carries down the data plane (not api, which Wants the gateway)", () => {
    expect(unitDependents("radon-ib-gateway.service")).toEqual([
      "radon-relay.service", "radon-monitor.service",
    ]);
  });
  it("no dependents for a leaf", () => expect(unitDependents("radon-leap.service")).toEqual([]));
  it("map is the source of truth", () => expect(UNIT_DEPENDENTS["radon-ib-gateway.service"]).toHaveLength(2));
});

describe("serviceControlDisabledReason", () => {
  it("read-only when unsupported", () =>
    expect(serviceControlDisabledReason({ unit: daemonRunning, action: "restart", supported: false, pending: false }))
      .toMatch(/Read-only/));
  it("allowlist when not controllable", () =>
    expect(serviceControlDisabledReason({ unit: uncontrollable, action: "restart", supported: true, pending: false }))
      .toMatch(/allowlist/));
  it("in-flight when pending", () =>
    expect(serviceControlDisabledReason({ unit: daemonRunning, action: "restart", supported: true, pending: true }))
      .toMatch(/in flight/));
  it("start disabled when already running", () =>
    expect(serviceControlDisabledReason({ unit: daemonRunning, action: "start", supported: true, pending: false }))
      .toMatch(/Already running/));
  it("enabled otherwise", () =>
    expect(serviceControlDisabledReason({ unit: daemonStopped, action: "start", supported: true, pending: false }))
      .toBeNull());
});

describe("livenessSummary", () => {
  it("counts healthy controllable units", () => {
    expect(livenessSummary([daemonRunning, daemonStopped, jobIdleOk, uncontrollable]))
      .toEqual({ ok: 2, total: 3 }); // running + idle-ok healthy; stopped not; uncontrollable excluded
  });
});

describe("freshnessSummary", () => {
  const now = Date.parse("2026-05-30T12:00:00Z");
  const fresh: ServiceHealthRow = { service: "vcg-scan", state: "ok", updated_at: "2026-05-30T11:59:30Z" };
  const ancient: ServiceHealthRow = { service: "vcg-scan", state: "ok", updated_at: "2026-05-29T00:00:00Z" };
  it("flags rows past their window", () => {
    const s = freshnessSummary([fresh, ancient], "open", now);
    expect(s.total).toBe(2);
    expect(s.stale).toBe(1);
    expect(s.staleServices).toEqual(["vcg-scan"]);
  });
  it("missing updated_at is stale", () => {
    expect(freshnessSummary([{ service: "x", state: "ok", updated_at: null }], "open", now).stale).toBe(1);
  });
});

describe("ibAuthSummary", () => {
  const authed = (connected: boolean): AdminHealthPayload => ({
    status: "ok",
    ib_gateway: { auth_state: "authenticated", port_listening: true },
    ib_pool: { sync: { connected, client_id: 3, managed_accounts: [] } },
  });
  it("authenticated + connected pool is positive", () =>
    expect(ibAuthSummary(authed(true))).toMatchObject({ tone: "positive", poolStuck: false }));
  it("authenticated + all-disconnected pool is the stuck-pool warning", () =>
    expect(ibAuthSummary(authed(false))).toEqual({ tone: "warning", label: "Auth OK, pool stuck", poolStuck: true }));
  it("awaiting_2fa is warning", () =>
    expect(ibAuthSummary({ status: "ok", ib_gateway: { auth_state: "awaiting_2fa", port_listening: true }, ib_pool: {} }))
      .toMatchObject({ tone: "warning", poolStuck: false }));
  it("null health is unknown", () => expect(ibAuthSummary(null)).toEqual({ tone: "neutral", label: "Unknown", poolStuck: false }));
});

describe("humanizeDetail — never render raw JSON in the Detail column", () => {
  it("empty/null -> empty string", () => {
    expect(humanizeDetail(null)).toBe("");
    expect(humanizeDetail(undefined)).toBe("");
    expect(humanizeDetail("  ")).toBe("");
  });
  it("plain human text passes through", () => {
    expect(humanizeDetail("Failed to connect to IB")).toBe("Failed to connect to IB");
  });
  it("a JSON heartbeat blob becomes a readable summary (no braces, no quotes)", () => {
    const out = humanizeDetail('{"heartbeat_at": "2026-05-30T15:42:57.887640Z", "wal_conflicts_5m": 0}');
    expect(out).toBe("heartbeat at: 15:42:57 UTC · wal conflicts 5m: 0");
    expect(out).not.toContain("{");
    expect(out).not.toContain('"');
  });
  it("the watchdog-alerts bucket blob is readable", () => {
    expect(humanizeDetail('{"bucket": "error"}')).toBe("bucket: error");
  });
  it("booleans humanize", () => {
    expect(humanizeDetail('{"connected": false}')).toBe("connected: no");
  });
  it("malformed JSON-looking text is returned as-is (not crashed)", () => {
    expect(humanizeDetail("{not valid")).toBe("{not valid");
  });
});

describe("externalProbeSummary", () => {
  const now = Date.parse("2026-05-30T12:00:00Z");
  const at = (iso: string, ok = 1, latency: number | null = 142): ExternalProbeRow =>
    ({ source: "gh/edge", ok, latency_ms: latency, checked_at: iso });
  it("healthy when recent + ok", () =>
    expect(externalProbeSummary(at("2026-05-30T11:59:30Z"), now)).toEqual({ state: "healthy", latencyMs: 142 }));
  it("down when recent + not ok", () =>
    expect(externalProbeSummary(at("2026-05-30T11:59:30Z", 0), now)).toEqual({ state: "down", latencyMs: 142 }));
  it("stale past the dead-man window regardless of ok", () => {
    const old = new Date(now - (EXTERNAL_PROBE_STALE_AFTER_SECONDS + 60) * 1000).toISOString();
    expect(externalProbeSummary(at(old), now).state).toBe("stale");
  });
  it("unknown when missing", () =>
    expect(externalProbeSummary(null, now)).toEqual({ state: "unknown", latencyMs: null }));
});
