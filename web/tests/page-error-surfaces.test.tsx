/** @vitest-environment jsdom */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import PanelRefreshError from "@/components/PanelRefreshError";
import RestartLog from "@/components/admin/RestartLog";
import IbGatewayCard from "@/components/admin/IbGatewayCard";

const raw = JSON.stringify({ scan_succeeded: false, error: "Radon API 502: Subprocess capacity exhausted" });
afterEach(cleanup);

describe("non-scanner error presentation", () => {
  it("shows refresh failures as safe toasts without an inline footprint", () => {
    const { container } = render(<PanelRefreshError error={raw} />);
    expect(screen.getByTestId("panel-refresh-error").textContent).toContain("This service is busy. Please try again shortly.");
    expect(container.innerHTML).toBe("");
    expect(screen.getByRole("alert").textContent).toContain("Showing the last available data");
  });
  it("keeps the existing gateway error surface with recovery copy", () => {
    const { container } = render(<IbGatewayCard health={null} loading={false} error={raw} />);
    expect(screen.getByText("This service is busy. Please try again shortly.")).toBeTruthy();
    expect(container.textContent).not.toContain("scan_succeeded");
  });
  it("retains action identity while suppressing diagnostics in failed admin actions", () => {
    render(<RestartLog entries={[{ at: "2026-09-17T12:00:00Z", action: "service-action", target: "scanner", ok: false, detail: "Traceback (most recent call last): /opt/radon/service.py" }]} />);
    expect(screen.getByText("scanner")).toBeTruthy();
    expect(screen.getByText("The action could not be completed. Review service status and try again.")).toBeTruthy();
    expect(screen.queryByText(/Traceback/)).toBeNull();
  });
});
