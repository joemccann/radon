/** @vitest-environment jsdom */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import TradingKillSwitch from "../components/admin/TradingKillSwitch";
import AdminWorkspace from "../components/admin/AdminWorkspace";
import AdminAttentionQueue from "../components/admin/AdminAttentionQueue";
import AdminSystemOverview from "../components/admin/AdminSystemOverview";
import type { AdminAttentionCondition, AdminAttentionSources } from "../lib/adminAttention";
import type { AdminHealthPayload, ServicesListResponse, EdgeHealthStatus } from "../lib/adminTypes";

afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });
const NOW = Date.parse("2026-09-25T18:00:00Z");
const condition = (id: string, action: AdminAttentionCondition["action"] = "services"): AdminAttentionCondition => ({
  id, action, title: `${id} needs attention`, detail: "Review the observed condition.", tone: "negative", source: "services", observedAt: new Date(NOW).toISOString(),
});
const HEALTH: AdminHealthPayload = {
  status: "ok", ib_gateway: { auth_state: "authenticated", port_listening: true },
  ib_pool: { sync: { connected: true, client_id: 3, managed_accounts: [] } },
};
const SERVICES: ServicesListResponse = {
  supported: true,
  units: [{ unit: "radon-api.service", load_state: "loaded", active_state: "active", sub_state: "running", description: "API", can_control: true }],
};
const EDGE: EdgeHealthStatus = {
  service_health: { state: "ok", rows: [{ service: "portfolio-sync", state: "ok", updated_at: new Date(NOW).toISOString() }] },
  external_probe: { source: "fixture", ok: 1, latency_ms: 142, checked_at: new Date(NOW).toISOString() },
};
const sources = (): AdminAttentionSources => ({
  health: { observedAt: NOW, loading: false }, services: { observedAt: NOW, loading: false }, edge: { observedAt: NOW, loading: false },
});

describe("operator action queue", () => {
  it("distinguishes unresolved first load from no outstanding conditions", () => {
    const { rerender } = render(<AdminAttentionQueue conditions={[]} loading now={NOW} onReview={vi.fn()} />);
    expect(screen.getByTestId("admin-attention-loading").textContent).toContain("Checking");
    expect(screen.queryByTestId("admin-attention-empty")).toBeNull();
    rerender(<AdminAttentionQueue conditions={[]} now={NOW} onReview={vi.fn()} />);
    expect(screen.getByTestId("admin-attention-empty").textContent).toContain("No action needed");
  });

  it("renders model priority and exposes the complete queue without discarding conditions", () => {
    const rows = Array.from({ length: 6 }, (_, index) => condition(`unit:${index}`));
    const onReview = vi.fn();
    render(<AdminAttentionQueue conditions={rows} now={NOW} onReview={onReview} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
    expect(screen.getAllByRole("listitem")[0].getAttribute("data-priority")).toBe("primary");
    fireEvent.click(within(screen.getAllByRole("listitem")[0]).getByRole("button", { name: "Review services" }));
    expect(onReview).toHaveBeenCalledWith("services");
    fireEvent.click(screen.getByRole("button", { name: "Show all 6 conditions" }));
    expect(screen.getAllByRole("listitem")).toHaveLength(6);
    expect(screen.getByRole("button", { name: "Show fewer conditions" }).getAttribute("aria-expanded")).toBe("true");
  });

  it("does not move a focused recovery control when a poll adds a higher priority condition", () => {
    const original = condition("unit:api");
    const incoming = condition("broker:auth", "gateway");
    const { rerender } = render(<AdminAttentionQueue conditions={[original]} now={NOW} onReview={vi.fn()} />);
    const button = screen.getByRole("button", { name: "Review services" });
    fireEvent.focus(button);
    rerender(<AdminAttentionQueue conditions={[incoming, original]} now={NOW + 5000} onReview={vi.fn()} />);
    expect(screen.getAllByRole("listitem")[0].getAttribute("data-testid")).toBe("admin-attention-unit:api");
    expect(within(screen.getAllByRole("listitem")[0]).getByRole("button")).toBe(button);
    fireEvent.blur(button, { relatedTarget: document.body });
    expect(screen.getAllByRole("listitem")[0].getAttribute("data-testid")).toBe("admin-attention-broker:auth");
  });

  it("immediately disables a resolved condition retained under the pointer", () => {
    const { rerender } = render(<AdminAttentionQueue conditions={[condition("unit:api")]} now={NOW} onReview={vi.fn()} />);
    fireEvent.pointerEnter(screen.getByTestId("admin-attention-queue"));
    rerender(<AdminAttentionQueue conditions={[]} now={NOW + 5000} onReview={vi.fn()} />);
    const resolved = screen.getByRole("button", { name: "Resolved" }) as HTMLButtonElement;
    expect(resolved.disabled).toBe(true);
    expect(screen.getByTestId("admin-attention-unit:api").textContent).toContain("No longer observed");
    fireEvent.pointerLeave(screen.getByTestId("admin-attention-queue"));
    expect(screen.getByTestId("admin-attention-empty")).toBeTruthy();
  });
});

describe("operator source-scoped overview", () => {
  it("does not let successful service and edge polls make retained broker health look current", () => {
    const observations = sources();
    observations.health = { observedAt: NOW - 90_000, loading: false, error: true };
    render(<AdminSystemOverview health={HEALTH} services={SERVICES} edge={EDGE} sources={observations} now={NOW} />);
    expect(screen.getByTestId("overview-broker").textContent).toContain("Last known");
    expect(screen.getByTestId("overview-broker").textContent).toContain("Observed 1m ago");
    expect(screen.getByTestId("overview-broker").textContent).not.toContain("Authenticated");
    expect(screen.getByTestId("overview-services").textContent).toContain("1 / 1 OK");
    expect(screen.getByTestId("overview-probe").textContent).toContain("Passed");
  });

  it("labels sources with no observations as unknown instead of healthy zero counts", () => {
    const observations = Object.fromEntries(["health", "services", "edge"].map((source) => [source, { observedAt: null, loading: false, error: true }])) as AdminAttentionSources;
    render(<AdminSystemOverview health={null} services={null} edge={null} sources={observations} now={NOW} />);
    for (const source of ["broker", "services", "writers", "probe"]) {
      expect(screen.getByTestId(`overview-${source}`).textContent).toContain("Unknown");
      expect(screen.getByTestId(`overview-${source}`).textContent).not.toContain("0 / 0 OK");
    }
  });

  it("retains probe observation age when its aggregate source becomes unavailable", () => {
    const observations = sources();
    observations.edge = { observedAt: NOW - 120_000, loading: false, error: true };
    render(<AdminSystemOverview health={HEALTH} services={SERVICES} edge={{ ...EDGE, external_probe: { ...EDGE.external_probe!, checked_at: new Date(NOW - 120_000).toISOString() } }} sources={observations} now={NOW} />);
    expect(screen.getByTestId("overview-probe").textContent).toContain("Last known");
    expect(screen.getByTestId("overview-probe").textContent).toContain("Observed 2m ago");
    expect(screen.getByTestId("overview-probe").textContent).not.toContain("Passed");
  });
});


describe("operator polling integration", () => {
  it("keeps a failed broker poll visibly unconfirmed while other sources continue refreshing", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    let failHealth = false;
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      const payloads: Record<string, unknown> = {
        "/api/admin/health": HEALTH,
        "/api/admin/services": SERVICES,
        "/api/admin/edge-health": { ...EDGE, reachable: true },
        "/api/admin/reliability": { events: [], baseline: {} },
        "/api/admin/host-metrics": { rows: [] },
        "/api/admin/slo": { rows: [] },
        "/api/admin/trading/status": { halted: false },
        "/api/admin/demo-users": { users: [] },
      };
      const failed = url === "/api/admin/health" && failHealth;
      return new Response(JSON.stringify(failed ? { error: "Health unavailable" } : payloads[url]), { status: failed ? 503 : 200, headers: { "content-type": "application/json" } });
    }));
    const settle = async () => { await act(async () => { for (let i = 0; i < 10; i += 1) await Promise.resolve(); }); };
    render(<AdminWorkspace />);
    await settle();
    expect(screen.getByTestId("overview-broker").textContent).toContain("Authenticated");
    failHealth = true;
    await act(async () => { vi.advanceTimersByTime(5_100); });
    await settle();
    expect(screen.getByTestId("overview-broker").textContent).toContain("Last known");
    expect(screen.getByTestId("overview-broker").textContent).not.toContain("Authenticated");
    expect(screen.getByTestId("overview-services").textContent).toContain("1 / 1 OK");
    expect(screen.getByTestId("admin-attention-source:health").textContent).toContain("Showing the last received values");
    expect(screen.getByTestId("ib-auth-state").textContent).toContain("Authenticated");
    expect(calls.filter((url) => url === "/api/admin/services").length).toBeGreaterThan(1);
  });
});


describe("trading status observation freshness", () => {
  it.each([false, true])("marks a failed refresh Unknown and neutral (compact=%s)", async (compact) => {
    vi.useFakeTimers();
    let failStatus = false;
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(failStatus ? { error: "Status unavailable" } : { halted: false }), { status: failStatus ? 503 : 200, headers: { "content-type": "application/json" } })));
    const settle = async () => { await act(async () => { for (let i = 0; i < 6; i += 1) await Promise.resolve(); }); };
    render(<TradingKillSwitch compact={compact} />);
    await settle();
    expect(screen.getByTestId("trading-halt-state").textContent).toBe("Active");
    failStatus = true;
    await act(async () => { vi.advanceTimersByTime(5_100); });
    await settle();
    const status = screen.getByTestId("trading-halt-state");
    expect(status.textContent).toBe("Unknown");
    expect(status.className).toContain("admin-pill-neutral");
    expect(status.className).not.toContain("admin-pill-positive");
    if (compact) {
      expect(screen.getByTestId("trading-controls-button").textContent).toContain("Unknown");
      expect(screen.getByTestId("trading-controls-button").textContent).not.toContain("Active");
    }
  });
});
