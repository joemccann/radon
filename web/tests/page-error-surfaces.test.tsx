/** @vitest-environment jsdom */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import PanelRefreshError from "@/components/PanelRefreshError";
import RestartLog from "@/components/admin/RestartLog";
import IbGatewayCard from "@/components/admin/IbGatewayCard";

const raw = JSON.stringify({ scan_succeeded: false, error: "Radon API 502: Subprocess capacity exhausted" });
afterEach(cleanup);

describe("non-scanner error presentation", () => {
  it("does not leak the failed response through refresh tooltips", () => {
    const { container } = render(<PanelRefreshError error={raw} />);
    expect(screen.getByTestId("panel-refresh-error").getAttribute("title")).toBe("This service is busy. Please try again shortly.");
    expect(container.innerHTML).not.toContain("Subprocess");
    expect(screen.getByText("REFRESH FAILED")).toBeTruthy();
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
