/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import Ib2faControls from "../components/admin/Ib2faControls";
import type { AdminHealthPayload, UnitStatus } from "../lib/adminTypes";

afterEach(cleanup);

const gateway: UnitStatus = {
  unit: "radon-ib-gateway.service", load_state: "loaded", active_state: "active",
  sub_state: "running", description: "IB Gateway", can_control: true,
};
const health: AdminHealthPayload = {
  status: "ok",
  ib_gateway: {
    auth_state: "authenticated", port_listening: true, gateway_mode: "docker",
    host: "127.0.0.1", port: 4001, container_state: "running", container_health: "healthy",
    restart_backoff: { attempt_count: 0, last_attempt_at: 0, next_attempt_after: 0,
      next_attempt_in_secs: 0, last_outcome: null, push_lock: null },
  },
  ib_pool: {},
};
function props() {
  return {
    health, gatewayUnit: gateway, servicesSupported: true,
    onForcePush: vi.fn().mockResolvedValue(undefined),
    onResetBackoff: vi.fn().mockResolvedValue(undefined),
    onRestartStack: vi.fn().mockResolvedValue(true),
    onStartGateway: vi.fn().mockResolvedValue(true),
    onRestartGateway: vi.fn().mockResolvedValue(true),
    onStopGateway: vi.fn().mockResolvedValue(true),
  };
}
function button(name: string): HTMLButtonElement {
  return within(screen.getByTestId("ib-controls")).getByRole("button", { name, exact: true }) as HTMLButtonElement;
}

describe("header gateway commands", () => {
  it("presents start, restart and stop together with state-aware availability", () => {
    render(<Ib2faControls {...props()} />);
    expect(button("Start Gateway").disabled).toBe(true);
    expect(button("Restart Gateway").disabled).toBe(false);
    expect(button("Stop Gateway").disabled).toBe(false);
    expect(screen.getAllByTestId("gateway-power-button")).toHaveLength(1);
  });

  it("confirms a single-gateway restart with the exact unit and a fresh 2FA notice", async () => {
    const callbacks = props();
    render(<Ib2faControls {...callbacks} />);
    fireEvent.click(button("Restart Gateway"));
    const confirm = screen.getByTestId("admin-confirm-action") as HTMLButtonElement;
    expect(screen.getByRole("dialog", { name: "Restart the IB Gateway?" }).textContent).toContain("one fresh IBKR Mobile 2FA push");
    expect(confirm.disabled).toBe(true);
    expect(callbacks.onRestartGateway).not.toHaveBeenCalled();
    fireEvent.change(screen.getByTestId("admin-confirm-typed-input"), { target: { value: gateway.unit } });
    await act(async () => { fireEvent.click(confirm); });
    expect(callbacks.onRestartGateway).toHaveBeenCalledTimes(1);
    expect(callbacks.onRestartStack).not.toHaveBeenCalled();
    expect(callbacks.onStopGateway).not.toHaveBeenCalled();
  });

  it.each([
    ["stale observations", { primaryObservationCurrent: false }],
    ["another workspace command is pending", { externalPending: true }],
    ["unreachable API", { apiUnreachable: true }],
    ["unknown unit state", { gatewayUnit: { ...gateway, active_state: "unknown" } }],
    ["read-only gateway", { gatewayUnit: { ...gateway, can_control: false } }],
    ["unsupported local control", { servicesSupported: false }],
    ["transitioning gateway", { gatewayUnit: { ...gateway, active_state: "activating" } }],
  ] as const)("blocks restart when %s", (_label, override) => {
    const callbacks = props();
    render(<Ib2faControls {...callbacks} {...override} />);
    const restart = screen.getByTestId("gateway-restart-button") as HTMLButtonElement;
    expect(restart.disabled).toBe(true);
    fireEvent.click(restart);
    expect(callbacks.onRestartGateway).not.toHaveBeenCalled();
    expect(screen.queryByTestId("admin-confirm")).toBeNull();
  });

  it("allows the explicitly controllable remote gateway from an app host", () => {
    render(<Ib2faControls {...props()} hostRole="app" servicesSupported={false} />);
    expect(button("Restart Gateway").disabled).toBe(false);
    expect(button("Stop Gateway").disabled).toBe(false);
  });

  it("blocks gateway restart under an existing push lease while retaining stop", () => {
    const lockedHealth: AdminHealthPayload = { ...health, ib_gateway: { ...health.ib_gateway,
      restart_backoff: { ...health.ib_gateway.restart_backoff!, push_lock: {
        holder: "ib_watchdog", acquired_at: 0, expires_at: 30, remaining_secs: 30, reason: "login",
      } },
    } };
    render(<Ib2faControls {...props()} health={lockedHealth} />);
    expect(button("Restart Gateway").disabled).toBe(true);
    expect(button("Restart Gateway").title).toContain("ib_watchdog");
    expect(button("Stop Gateway").disabled).toBe(false);
  });

  it("cross-disables recovery controls while a gateway restart is pending", async () => {
    let finish!: (value: boolean) => void;
    const callbacks = { ...props(), onRestartGateway: vi.fn(() => new Promise<boolean>((resolve) => { finish = resolve; })) };
    render(<Ib2faControls {...callbacks} />);
    fireEvent.click(button("Restart Gateway"));
    fireEvent.change(screen.getByTestId("admin-confirm-typed-input"), { target: { value: gateway.unit } });
    fireEvent.click(screen.getByTestId("admin-confirm-action"));
    for (const id of ["force-2fa-button", "reset-backoff-button", "restart-stack-button", "gateway-restart-button", "gateway-power-button", "gateway-start-button"]) {
      expect((screen.getByTestId(id) as HTMLButtonElement).disabled).toBe(true);
    }
    fireEvent.click(screen.getByTestId("admin-confirm-action"));
    expect(callbacks.onRestartGateway).toHaveBeenCalledTimes(1);
    await act(async () => { finish(true); });
  });

  it("rechecks observation freshness after a confirmation was opened", () => {
    const callbacks = props();
    const view = render(<Ib2faControls {...callbacks} />);
    fireEvent.click(button("Restart Gateway"));
    fireEvent.change(screen.getByTestId("admin-confirm-typed-input"), { target: { value: gateway.unit } });
    view.rerender(<Ib2faControls {...callbacks} primaryObservationCurrent={false} />);
    expect((screen.getByTestId("admin-confirm-action") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByTestId("admin-confirm-action").textContent).toBe("Restart Gateway");
    expect((screen.getByRole("button", { name: "Cancel", exact: true }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByTestId("admin-confirm").textContent).toContain("Refresh gateway status before running a command.");
    fireEvent.click(screen.getByTestId("admin-confirm-action"));
    expect(callbacks.onRestartGateway).not.toHaveBeenCalled();
  });

  it("describes app-host stack restart without cycling its remote broker", () => {
    render(<Ib2faControls {...props()} hostRole="app" />);
    fireEvent.click(screen.getByTestId("restart-stack-button"));
    expect(screen.getByTestId("admin-confirm").textContent).toContain("Gateway stays on the broker.");
    expect(screen.getByTestId("admin-confirm").textContent).not.toContain("fresh 2FA");
  });

  it("portals only the existing stack trigger into service controls", async () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    const callbacks = props();
    render(<Ib2faControls {...callbacks} stackActionContainer={target} />);
    expect(screen.getAllByTestId("restart-stack-button")).toHaveLength(1);
    expect(target.contains(screen.getByTestId("restart-stack-button"))).toBe(true);
    fireEvent.click(screen.getByTestId("restart-stack-button"));
    expect(callbacks.onRestartStack).not.toHaveBeenCalled();
    await act(async () => { fireEvent.click(screen.getByTestId("admin-confirm-action")); });
    expect(callbacks.onRestartStack).toHaveBeenCalledTimes(1);
    target.remove();
  });
});
