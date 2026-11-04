/**
 * @vitest-environment jsdom
 *
 * Ib2faControls power intent: an acknowledged Stop/Start is an intent the
 * poll has not confirmed yet. It renders as in-transition and never arms the
 * opposite action early; its safety ceiling runs from the moment the intent
 * was recorded and is not re-armed by every poll change.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import Ib2faControls from "../components/admin/Ib2faControls";
import type { AdminHealthPayload, UnitStatus } from "../lib/adminTypes";

const UNIT = "radon-ib-gateway.service";

function health(portListening: boolean): AdminHealthPayload {
  return {
    status: "ok",
    ib_gateway: { auth_state: "authenticated", port_listening: portListening },
    ib_pool: { sync: { connected: true, client_id: 3, managed_accounts: ["U1"] } },
  };
}

function gateway(active_state: string, sub_state = "running"): UnitStatus {
  return { unit: UNIT, load_state: "loaded", active_state, sub_state, description: "IB Gateway", can_control: true, uptime_secs: 10 };
}

function controls(unit: UnitStatus, onStopGateway: () => Promise<boolean>) {
  return (
    <Ib2faControls
      health={health(unit.active_state === "active")}
      onForcePush={vi.fn()}
      onResetBackoff={vi.fn()}
      onRestartStack={vi.fn()}
      gatewayUnit={unit}
      servicesSupported
      onStopGateway={onStopGateway}
      onStartGateway={vi.fn()}
    />
  );
}

async function confirmStop() {
  fireEvent.click(screen.getByTestId("gateway-power-button"));
  fireEvent.change(screen.getByTestId("admin-confirm-typed-input"), { target: { value: UNIT } });
  await act(async () => {
    fireEvent.click(screen.getByTestId("admin-confirm-action"));
    for (let i = 0; i < 6; i += 1) await Promise.resolve();
  });
}

function startButton(): HTMLButtonElement | null {
  const byPower = screen.queryByTestId("gateway-power-button") as HTMLButtonElement | null;
  if (byPower?.textContent === "Start Gateway") return byPower;
  return screen.queryByTestId("gateway-start-button") as HTMLButtonElement | null;
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("Ib2faControls power intent", () => {
  it("does not arm Start after an acknowledged Stop until the poll confirms stopped", async () => {
    const onStop = vi.fn().mockResolvedValue(true);
    const { rerender } = render(controls(gateway("active"), onStop));
    await confirmStop();

    expect(onStop).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("gateway-power-status").getAttribute("data-state")).toBe("transitional");
    expect(screen.getByTestId("gateway-power-button").textContent).toBe("Stopping...");
    expect(startButton()?.disabled ?? true).toBe(true);

    rerender(controls(gateway("inactive", "dead"), onStop));
    expect(screen.getByTestId("gateway-power-status").getAttribute("data-state")).toBe("stopped");
    expect(startButton()?.disabled).toBe(false);
  });

  it("clears an unconfirmed intent at the safety ceiling even when the poll keeps changing", async () => {
    vi.useFakeTimers();
    const onStop = vi.fn().mockResolvedValue(true);
    const { rerender } = render(controls(gateway("active"), onStop));
    await confirmStop();
    expect(screen.getByTestId("gateway-power-status").getAttribute("data-state")).toBe("transitional");

    // The poll wobbles but never confirms the stop.
    await act(async () => { vi.advanceTimersByTime(60_000); });
    rerender(controls(gateway("deactivating", "stop"), onStop));
    await act(async () => { vi.advanceTimersByTime(40_000); });
    rerender(controls(gateway("active"), onStop));
    await act(async () => { vi.advanceTimersByTime(25_000); });

    // 125s after the intent: the 120s ceiling has passed, so the poll rules.
    expect(screen.getByTestId("gateway-power-status").getAttribute("data-state")).toBe("running");
    expect(screen.getByTestId("gateway-power-button").textContent).toBe("Stop Gateway");
  });
});
