import { describe, expect, it } from "vitest";
import {
  ADMIN_OBSERVATION_STALE_MS,
  deriveAdminAttention,
  isAdminObservationCurrent,
  type AdminAttentionInput,
} from "../lib/adminAttention";
import type { ServiceHealthRow, UnitStatus } from "../lib/adminTypes";

const NOW = Date.parse("2026-09-25T16:00:00Z");
const at = (offset = 0) => new Date(NOW - offset).toISOString();
const MINUTE = 60_000;
function unit(overrides: Partial<UnitStatus> = {}): UnitStatus {
  return { unit: "radon-api.service", load_state: "loaded", active_state: "active", sub_state: "running",
    description: "API", can_control: true, ...overrides };
}
function writer(overrides: Partial<ServiceHealthRow> = {}): ServiceHealthRow {
  return { service: "portfolio-sync", state: "ok", updated_at: at(), ...overrides };
}
function input(): AdminAttentionInput {
  return {
    now: NOW,
    health: { status: "ok", ib_gateway: { auth_state: "authenticated", port_listening: true },
      ib_pool: { orders: { connected: true, client_id: 1, managed_accounts: [] } } },
    services: { supported: true, units: [unit()] },
    edge: { generated_at: at(), service_health: { state: "ok", rows: [writer()] },
      external_probe: { source: "github", ok: 1, checked_at: at(), latency_ms: 100 } },
    sources: {
      health: { observedAt: NOW, loading: false },
      services: { observedAt: NOW, loading: false },
      edge: { observedAt: NOW, loading: false },
    },
  };
}
const ids = (data: AdminAttentionInput) => deriveAdminAttention(data).map(row => row.id);

describe("operator attention projection", () => {
  it("does not manufacture incidents from current healthy observations", () => {
    expect(deriveAdminAttention(input())).toEqual([]);
  });

  it("keeps unresolved initial loads distinct from measured health", () => {
    const data = input();
    data.health = data.services = data.edge = null;
    data.sources = {
      health: { observedAt: null, loading: true }, services: { observedAt: null, loading: true }, edge: { observedAt: null, loading: true },
    };
    expect(deriveAdminAttention(data)).toEqual([]);
    for (const source of Object.values(data.sources)) source.loading = false;
    expect(ids(data)).toEqual(["source:edge", "source:health", "source:services"]);
  });

  it("orders authentication, failed daemon, failed writer, overdue writer", () => {
    const data = input();
    data.health!.ib_gateway.auth_state = "awaiting_2fa";
    data.services!.units = [unit({ active_state: "failed", sub_state: "failed" })];
    data.edge!.service_health!.rows = [writer({ service: "newsfeed-scraper", updated_at: at(10 * MINUTE) }), writer({ state: "error" })];
    expect(ids(data)).toEqual(["broker:auth", "unit:radon-api.service", "writer:portfolio-sync", "writer:newsfeed-scraper"]);
    expect(deriveAdminAttention(data)[0]).toMatchObject({ action: "gateway", source: "health", observedAt: at() });
  });

  it("distinguishes authenticated pool failure from awaiting authentication", () => {
    const data = input();
    data.health!.ib_pool.orders.connected = false;
    expect(deriveAdminAttention(data)).toEqual([expect.objectContaining({ id: "broker:pool", action: "services", subject: "radon-api.service" })]);
    data.health!.ib_gateway.auth_state = "awaiting_2fa";
    expect(ids(data)).toEqual(["broker:auth"]);
  });

  it("does not infer live API clients from authenticated gateway with an empty pool", () => {
    const data = input();
    data.health!.ib_pool = {};
    expect(deriveAdminAttention(data)[0]).toMatchObject({ id: "broker:pool", title: "Broker API clients unconfirmed" });
  });

  it("surfaces a partially disconnected pool without claiming every client is stuck", () => {
    const data = input();
    data.health!.ib_pool.quotes = { connected: false, client_id: 2, managed_accounts: [] };
    expect(deriveAdminAttention(data)[0]).toMatchObject({ title: "Broker API connection incomplete", detail: expect.stringContaining("1 of 2") });
  });

  it.each(["unreachable", "unknown"] as const)("surfaces %s authentication", state => {
    const data = input();
    data.health!.ib_gateway.auth_state = state;
    expect(ids(data)).toEqual(["broker:auth"]);
  });

  it("does not infer remote gateway failure from a missing local listener", () => {
    const data = input();
    data.health!.ib_gateway = { auth_state: "remote", port_listening: false };
    expect(ids(data)).toEqual([]);
    data.health!.ib_gateway.upstream_dead = true;
    expect(ids(data)).toEqual(["broker:auth"]);
  });

  it("surfaces stopped daemons even when host controls are unavailable", () => {
    const data = input();
    data.services!.supported = false;
    data.services!.units = [unit({ active_state: "inactive", sub_state: "dead", can_control: false })];
    expect(deriveAdminAttention(data)[0]).toMatchObject({ id: "unit:radon-api.service", tone: "negative", action: "services" });
  });

  it("does not treat idle successful or never-run jobs as stopped daemons", () => {
    const data = input();
    data.services!.units = [unit({ unit: "radon-backup.service", active_state: "inactive", sub_state: "dead", last_exit_code: 0 }),
      unit({ unit: "radon-other.service", active_state: "inactive", sub_state: "dead" })];
    expect(ids(data)).toEqual([]);
    data.services!.units[0].last_exit_code = 1;
    expect(ids(data)).toEqual(["unit:radon-backup.service"]);
  });

  it("retains transitional and unknown service observations", () => {
    const data = input();
    data.services!.units = [unit({ active_state: "activating" }), unit({ unit: "radon-monitor.service", active_state: "unknown", can_control: false })];
    expect(ids(data)).toEqual(["unit:radon-monitor.service", "unit:radon-api.service"]);
  });

  it("evaluates fresh failure and overdue success independently", () => {
    const data = input();
    data.edge!.service_health!.rows = [writer({ state: "error" }), writer({ service: "newsfeed-scraper", updated_at: at(10 * MINUTE) })];
    const conditions = deriveAdminAttention(data);
    expect(conditions[0]).toMatchObject({ title: "portfolio-sync: update failed", tone: "negative" });
    expect(conditions[0].detail).not.toContain("overdue");
    expect(conditions[1]).toMatchObject({ title: "newsfeed-scraper: update overdue", tone: "warning" });
  });

  it.each(["warn", "warning"])("surfaces a fresh %s writer result independently of freshness", state => {
    const data = input();
    data.edge!.service_health!.rows = [writer({ service: "flow-refresh", state })];
    expect(deriveAdminAttention(data)[0]).toMatchObject({ id: "writer:flow-refresh", title: "flow-refresh: update warning", tone: "warning" });
    expect(deriveAdminAttention(data)[0].detail).not.toContain("overdue");
  });

  it("includes both a failed result and overdue schedule in one stable writer row", () => {
    const data = input();
    data.edge!.service_health!.rows = [writer({ state: "error", updated_at: at(60 * MINUTE) })];
    const conditions = deriveAdminAttention(data);
    expect(conditions).toHaveLength(1);
    expect(conditions[0].detail).toMatch(/result is an error.*scheduled update is overdue/);
    const id = conditions[0].id;
    data.edge!.service_health!.rows[0].updated_at = at();
    expect(deriveAdminAttention(data)[0].id).toBe(id);
  });

  it("uses market-aware windows and does not flag overnight scheduled silence", () => {
    const data = input();
    data.now = Date.parse("2026-09-26T16:00:00Z");
    for (const source of Object.values(data.sources)) source.observedAt = data.now;
    data.edge!.generated_at = new Date(data.now).toISOString();
    data.edge!.external_probe!.checked_at = new Date(data.now).toISOString();
    expect(ids(data)).toEqual([]);
  });

  it("honors opening grace for scheduled market-hour writers", () => {
    const data = input();
    data.now = Date.parse("2026-09-25T13:35:00Z");
    for (const source of Object.values(data.sources)) source.observedAt = data.now;
    data.edge!.generated_at = new Date(data.now).toISOString();
    data.edge!.external_probe!.checked_at = new Date(data.now).toISOString();
    data.edge!.service_health!.rows = [writer({ updated_at: "2026-09-24T20:00:00Z" })];
    expect(ids(data)).toEqual([]);
  });

  it("keeps dormant on-demand writers neutral but surfaces their explicit failures", () => {
    const data = input();
    data.edge!.service_health!.rows = [writer({ service: "flex-web-service", updated_at: at(20 * 24 * 60 * MINUTE) })];
    expect(ids(data)).toEqual([]);
    data.edge!.service_health!.rows[0].state = "error";
    expect(ids(data)).toEqual(["writer:flex-web-service"]);
    expect(deriveAdminAttention(data)[0].detail).not.toContain("overdue");
  });

  it("requires valid timestamps to establish scheduled writer freshness", () => {
    const data = input();
    data.edge!.service_health!.rows = [writer({ updated_at: "invalid" })];
    expect(deriveAdminAttention(data)[0]).toMatchObject({ id: "writer:portfolio-sync", observedAt: null });
  });

  it("deduplicates writer observations using latest time and preserves stable ordering", () => {
    const data = input();
    const rows = [writer({ state: "error", updated_at: at(MINUTE) }), writer(),
      writer({ service: "b", state: "error" }), writer({ service: "a", state: "error" })];
    data.edge!.service_health!.rows = rows;
    expect(ids(data)).toEqual(["writer:a", "writer:b"]);
    data.edge!.service_health!.rows = [...rows].reverse();
    expect(ids(data)).toEqual(["writer:a", "writer:b"]);
  });

  it("preserves an error when duplicate writer times are equal", () => {
    const data = input();
    data.edge!.service_health!.rows = [writer(), writer({ state: "error" })];
    expect(ids(data)).toEqual(["writer:portfolio-sync"]);
  });

  it("does not expose exception bodies, writer errors or external details", () => {
    const data = input();
    data.sources.health.error = true;
    data.edge!.service_health!.rows = [writer({ state: "error", last_error: '{"token":"SECRET"}' })];
    data.edge!.external_probe!.ok = 0;
    data.edge!.external_probe!.detail = "Traceback SECRET";
    expect(JSON.stringify(deriveAdminAttention(data))).not.toContain("SECRET");
  });

  it("keeps source timestamps separate and identifies retained observations", () => {
    const data = input();
    data.sources.health.observedAt = NOW - MINUTE;
    data.health!.ib_gateway.auth_state = "awaiting_2fa";
    const conditions = deriveAdminAttention(data);
    expect(conditions[0]).toMatchObject({ id: "source:health", observedAt: at(MINUTE), action: "refresh" });
    expect(conditions[1].detail).toMatch(/^Last recorded observation/);
    expect(conditions.some(row => row.id === "source:services")).toBe(false);
  });

  it("rejects stale edge payloads even when the response was just received", () => {
    const data = input();
    data.edge!.generated_at = at(MINUTE);
    expect(ids(data)).toContain("source:edge");
  });

  it("surfaces old edge service snapshots independently of live inventory", () => {
    const data = input();
    data.edge!.units = { "radon-api.service": { state: "active" } };
    data.edge!.units_age_secs = 60;
    expect(deriveAdminAttention(data)[0]).toMatchObject({ id: "edge:units", observedAt: at(MINUTE), action: "services" });
  });

  it("does not assume edge snapshots with unknown ages are current", () => {
    const data = input();
    data.edge!.units = { "radon-api.service": { state: "active" } };
    expect(deriveAdminAttention(data)[0]).toMatchObject({ id: "edge:units", observedAt: null });
    data.edge!.units_age_secs = 30;
    expect(ids(data)).toEqual([]);
  });

  it("does not call empty service or writer inventories healthy", () => {
    const data = input();
    data.services!.units = [];
    data.edge!.service_health!.rows = [];
    expect(ids(data)).toEqual(["services:empty", "writers:source"]);
  });

  it("reports writer source failure even with retained successful rows", () => {
    const data = input();
    data.edge!.service_health!.state = "error";
    expect(ids(data)).toEqual(["writers:source"]);
  });

  it("uses the actual external probe sample time and dead-man window", () => {
    const data = input();
    data.edge!.external_probe!.ok = 0;
    expect(deriveAdminAttention(data)[0]).toMatchObject({ title: "External probe reported a failure", action: "reliability" });
    data.edge!.external_probe!.checked_at = at(3 * 60 * MINUTE);
    expect(deriveAdminAttention(data)[0]).toMatchObject({ title: "External probe unconfirmed", observedAt: at(3 * 60 * MINUTE) });
    data.edge!.external_probe = null;
    expect(deriveAdminAttention(data)[0].observedAt).toBeNull();
  });
});

describe("source observation freshness", () => {
  it("requires a successful finite receipt and tolerates small future clock skew", () => {
    expect(isAdminObservationCurrent({ observedAt: null, loading: false }, NOW)).toBe(false);
    expect(isAdminObservationCurrent({ observedAt: NaN, loading: false }, NOW)).toBe(false);
    expect(isAdminObservationCurrent({ observedAt: NOW, loading: false, error: true }, NOW)).toBe(false);
    expect(isAdminObservationCurrent({ observedAt: NOW + 1000, loading: false }, NOW)).toBe(true);
    expect(isAdminObservationCurrent({ observedAt: NOW + 6000, loading: false }, NOW)).toBe(false);
    expect(isAdminObservationCurrent({ observedAt: NOW - ADMIN_OBSERVATION_STALE_MS, loading: false }, NOW)).toBe(true);
    expect(isAdminObservationCurrent({ observedAt: NOW - ADMIN_OBSERVATION_STALE_MS - 1, loading: false }, NOW)).toBe(false);
  });
});
