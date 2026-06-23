/**
 * @vitest-environment jsdom
 *
 * Component tests for the /admin redesign: ReliabilityStrip, SystemStatusBar,
 * WriterFreshnessTable, and the extended ConfirmDialog (type-to-confirm +
 * cascade enumeration).
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import React from "react";

afterEach(cleanup);
import ReliabilityStrip from "../components/admin/ReliabilityStrip";
import SystemStatusBar from "../components/admin/SystemStatusBar";
import WriterFreshnessTable from "../components/admin/WriterFreshnessTable";
import ConfirmDialog from "../components/admin/ConfirmDialog";
import ServiceControlPanel from "../components/admin/ServiceControlPanel";
import type { ServicesListResponse } from "../lib/adminTypes";
import type {
  AdminHealthPayload,
  EdgeHealthStatus,
  ServiceHealthRow,
  UnitStatus,
} from "../lib/adminTypes";

const daemon = (unit: string, running = true): UnitStatus => ({
  unit,
  load_state: "loaded",
  active_state: running ? "active" : "inactive",
  sub_state: running ? "running" : "dead",
  description: unit,
  can_control: true,
  uptime_secs: running ? 1000 : null,
});

const HEALTH: AdminHealthPayload = {
  status: "ok",
  ib_gateway: { auth_state: "authenticated", port_listening: true },
  ib_pool: { sync: { connected: true, client_id: 3, managed_accounts: ["U1"] } },
};

const edge = (rows: ServiceHealthRow[]): EdgeHealthStatus & { reachable?: boolean } => ({
  reachable: true,
  service_health: { state: "ok", rows },
  external_probe: { source: "gh", ok: 1, latency_ms: 142, checked_at: new Date().toISOString() },
});

describe("ReliabilityStrip", () => {
  it("renders skeletons while loading (no values)", () => {
    render(
      <ReliabilityStrip units={[]} edge={null} health={null} edgeReachable={false} loading />,
    );
    expect(screen.getByTestId("reliability-strip")).toBeTruthy();
    expect(screen.queryByText("Authenticated")).toBeNull();
    expect(document.querySelectorAll(".admin-skeleton").length).toBeGreaterThan(0);
  });

  it("renders real values when loaded", () => {
    render(
      <ReliabilityStrip
        units={[daemon("radon-api.service"), daemon("radon-relay.service")]}
        edge={edge([{ service: "vcg-scan", state: "ok", updated_at: new Date().toISOString() }])}
        health={HEALTH}
        edgeReachable
      />,
    );
    expect(screen.getByTestId("tile-liveness").textContent).toContain("2/2");
    expect(screen.getByTestId("tile-ib-auth").textContent).toContain("Authenticated");
    expect(screen.getByTestId("tile-off-box-probe").textContent).toContain("142ms");
  });
});

describe("SystemStatusBar", () => {
  it("shows the rollup + IB state when loaded", () => {
    render(
      <SystemStatusBar
        units={[daemon("radon-api.service"), daemon("radon-relay.service")]}
        health={HEALTH}
        updatedSecsAgo={3}
        stalled={false}
      />,
    );
    const bar = screen.getByTestId("system-status-bar");
    expect(bar.textContent).toContain("2/2 OK");
    expect(bar.textContent).toContain("IB Authenticated");
    expect(bar.textContent).toContain("updated");
  });

  it("shows a stale badge when polling stalled", () => {
    render(
      <SystemStatusBar units={[daemon("radon-api.service")]} health={HEALTH} updatedSecsAgo={40} stalled />,
    );
    expect(screen.getByTestId("status-bar-stalled").textContent).toContain("Polling stalled");
  });

  it("renders a skeleton while loading", () => {
    render(<SystemStatusBar units={[]} health={null} updatedSecsAgo={null} stalled={false} loading />);
    expect(screen.getByTestId("system-status-bar").querySelector(".admin-skeleton")).toBeTruthy();
  });
});

describe("WriterFreshnessTable", () => {
  it("humanizes a JSON detail (never renders a raw blob)", () => {
    render(
      <WriterFreshnessTable
        reachable
        rows={[
          {
            service: "replica-watchdog",
            state: "ok",
            updated_at: new Date().toISOString(),
            last_error: '{"heartbeat_at": "2026-05-30T15:42:57.000000Z", "wal_conflicts_5m": 0}',
          },
        ]}
      />,
    );
    const row = screen.getByTestId("writer-row-replica-watchdog");
    expect(row.textContent).toContain("wal conflicts 5m: 0");
    expect(row.textContent).not.toContain("{");
  });

  it("shows skeletons while loading", () => {
    render(<WriterFreshnessTable reachable={false} rows={[]} loading />);
    expect(screen.getByTestId("writer-freshness").querySelector(".admin-skeleton")).toBeTruthy();
  });

  it("shows an empty message when reachable with no rows", () => {
    render(<WriterFreshnessTable reachable rows={[]} />);
    expect(screen.getByText(/No writer health rows reported/i)).toBeTruthy();
  });

  it("shows unreachable when the edge is down", () => {
    render(<WriterFreshnessTable reachable={false} rows={[]} />);
    expect(screen.getByText(/unreachable/i)).toBeTruthy();
  });
});

describe("ServiceControlPanel — Start disabled when running", () => {
  const services = (units: ServicesListResponse["units"]): ServicesListResponse => ({
    supported: true,
    units,
  });

  it("disables Start (with a reason tooltip) for a running daemon", () => {
    render(
      <ServiceControlPanel
        loading={false}
        error={null}
        onAction={vi.fn()}
        services={services([daemon("radon-api.service", true)])}
      />,
    );
    const start = screen.getByTestId("service-start-radon-api.service") as HTMLButtonElement;
    expect(start.disabled).toBe(true);
    expect(start.getAttribute("title")).toMatch(/Already running/);
    // Restart + Stop stay enabled for a running daemon.
    expect((screen.getByTestId("service-restart-radon-api.service") as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByTestId("service-stop-radon-api.service") as HTMLButtonElement).disabled).toBe(false);
  });

  it("enables Start for a stopped daemon", () => {
    render(
      <ServiceControlPanel
        loading={false}
        error={null}
        onAction={vi.fn()}
        services={services([daemon("radon-api.service", false)])}
      />,
    );
    expect((screen.getByTestId("service-start-radon-api.service") as HTMLButtonElement).disabled).toBe(false);
  });
});

describe("ConfirmDialog — type-to-confirm + cascade", () => {
  it("gates Confirm behind typing the exact value and lists cascade dependents", () => {
    render(
      <ConfirmDialog
        open
        title="Stop radon-ib-gateway.service?"
        body="This runs systemctl stop."
        confirmLabel="Stop"
        destructive
        affectedUnits={["radon-api.service", "radon-relay.service"]}
        requireTyped="radon-ib-gateway.service"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    // cascade dependents enumerated
    const cascade = screen.getByTestId("admin-confirm-cascade");
    expect(cascade.textContent).toContain("radon-api.service");
    expect(cascade.textContent).toContain("radon-relay.service");

    const confirm = screen.getByTestId("admin-confirm-action") as HTMLButtonElement;
    expect(confirm.disabled).toBe(true); // gated until typed

    const input = screen.getByTestId("admin-confirm-typed-input");
    fireEvent.change(input, { target: { value: "wrong" } });
    expect(confirm.disabled).toBe(true);

    fireEvent.change(input, { target: { value: "radon-ib-gateway.service" } });
    expect(confirm.disabled).toBe(false);
  });

  it("copies the required token to the clipboard and shows feedback", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    render(
      <ConfirmDialog
        open
        title="Stop radon-ib-gateway.service?"
        body="This runs systemctl stop."
        confirmLabel="Stop"
        destructive
        requireTyped="radon-ib-gateway.service"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    const copy = screen.getByTestId("admin-confirm-copy") as HTMLButtonElement;
    expect(copy.getAttribute("aria-label")).toBe(
      "Copy radon-ib-gateway.service to clipboard",
    );
    expect(copy.textContent).toBe("Copy");

    fireEvent.click(copy);
    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledWith("radon-ib-gateway.service");

    await screen.findByText("Copied");
  });

  it("fires onConfirm only after the typed gate is satisfied", () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        open
        title="Stop x?"
        body="b"
        confirmLabel="Stop"
        destructive
        requireTyped="x"
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByTestId("admin-confirm-typed-input"), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("admin-confirm-action"));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});
