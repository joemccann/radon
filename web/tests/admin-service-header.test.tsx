/** @vitest-environment jsdom */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import ServiceControlPanel from "../components/admin/ServiceControlPanel";
import type { ServicesListResponse } from "../lib/adminTypes";

const SERVICES: ServicesListResponse = {
  supported: true,
  units: ["radon-api.service", "radon-monitor.service"].map((unit) => ({
    unit, load_state: "loaded", active_state: "inactive", sub_state: "dead",
    description: unit, can_control: true,
  })),
};
const actionButton = (action: string, unit = "radon-api.service") => screen.getByTestId(`service-${action}-${unit}`) as HTMLButtonElement;
const allActions = () => SERVICES.units.flatMap(({ unit }) => ["start", "restart", "stop"].map((action) => actionButton(action, unit)));
const baseProps = { services: SERVICES, loading: false, error: null };

afterEach(cleanup);

describe("header service controls", () => {
  it("keeps all row commands disabled when retained observations are stale", () => {
    const onAction = vi.fn().mockResolvedValue(undefined);
    render(<ServiceControlPanel {...baseProps} observationCurrent={false} onAction={onAction} />);
    for (const button of allActions()) {
      expect(button.disabled).toBe(true);
      fireEvent.click(button);
    }
    expect(screen.queryByTestId("admin-confirm")).toBeNull();
    expect(onAction).not.toHaveBeenCalled();
  });

  it("keeps every service row disabled while another command owner is pending", () => {
    const onAction = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(<ServiceControlPanel {...baseProps} externalPending onAction={onAction} />);
    for (const button of allActions()) {
      expect(button.disabled).toBe(true);
      fireEvent.click(button);
    }
    expect(onAction).not.toHaveBeenCalled();
    rerender(<ServiceControlPanel {...baseProps} externalPending={false} onAction={onAction} />);
    for (const button of allActions()) expect(button.disabled).toBe(false);
  });

  for (const action of ["restart", "stop"] as const) {
    it(`${action} confirmation cannot execute after observations become stale, and remains cancellable`, async () => {
      const onAction = vi.fn().mockResolvedValue(undefined);
      const { rerender } = render(<ServiceControlPanel {...baseProps} onAction={onAction} />);
      fireEvent.click(actionButton(action));
      expect(screen.getByTestId("admin-confirm")).toBeTruthy();
      rerender(<ServiceControlPanel {...baseProps} observationCurrent={false} onAction={onAction} />);
      const confirm = screen.getByTestId("admin-confirm-action") as HTMLButtonElement;
      expect(confirm.disabled).toBe(true);
      fireEvent.click(confirm);
      expect(onAction).not.toHaveBeenCalled();
      const cancel = screen.getByRole("button", { name: "Cancel", exact: true }) as HTMLButtonElement;
      expect(cancel.disabled).toBe(false);
      fireEvent.click(cancel);
      await waitFor(() => expect(screen.queryByTestId("admin-confirm")).toBeNull());
    });
  }

  it("blocks an already-open confirmation when a gateway or stack command begins", async () => {
    const onAction = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(<ServiceControlPanel {...baseProps} onAction={onAction} />);
    fireEvent.click(actionButton("restart"));
    rerender(<ServiceControlPanel {...baseProps} externalPending onAction={onAction} />);
    const confirm = screen.getByTestId("admin-confirm-action") as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    fireEvent.click(confirm);
    expect(onAction).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel", exact: true }));
    await waitFor(() => expect(screen.queryByTestId("admin-confirm")).toBeNull());
  });

  it("locks every row until the pending service action settles", async () => {
    let finish!: () => void;
    const pending = new Promise<void>((resolve) => { finish = resolve; });
    const onAction = vi.fn(() => pending);
    render(<ServiceControlPanel {...baseProps} onAction={onAction} />);
    fireEvent.click(actionButton("start"));
    expect(onAction).toHaveBeenCalledWith("radon-api.service", "start");
    for (const button of allActions()) {
      expect(button.disabled).toBe(true);
      fireEvent.click(button);
    }
    expect(onAction).toHaveBeenCalledTimes(1);
    await act(async () => { finish(); await pending; });
    for (const button of allActions()) expect(button.disabled).toBe(false);
  });
});
