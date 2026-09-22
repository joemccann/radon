// @vitest-environment jsdom
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import ErrorToast from "@/components/ErrorToast";
import RequestError from "@/components/RequestError";
import OrderErrorBanner from "@/components/OrderErrorBanner";
import ToastContainer from "@/components/Toast";

afterEach(cleanup);

describe("toast-only error presentation", () => {
  it("leaves no inline footprint, retains safe copy and supports retry", () => {
    const retry = vi.fn();
    const { container } = render(<RequestError error="Radon API 502: Subprocess capacity exhausted" retainedData onRetry={retry} />);
    expect(container.innerHTML).toBe("");
    const alert = screen.getByRole("alert");
    expect(alert.closest("[data-toast-viewport]")?.parentElement).toBe(document.body);
    expect(alert.textContent).toContain("This service is busy.");
    expect(alert.textContent).toContain("Showing the last available data.");
    expect(alert.textContent).not.toContain("Subprocess");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("does not duplicate in StrictMode or reopen a dismissed error on rerender", () => {
    const view = (message: string) => <StrictMode><ErrorToast message={<div>{message}</div>} onRetry={() => {}} /></StrictMode>;
    const { rerender } = render(view("Refresh failed."));
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    rerender(view("Refresh failed."));
    expect(screen.queryByRole("alert")).toBeNull();
    rerender(view("Your session has expired."));
    expect(screen.getByRole("alert").textContent).toContain("Your session has expired.");
  });

  it("allows the same error again after recovery and removes errors when resolved", () => {
    const { rerender } = render(<RequestError error="Refresh failed." />);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    rerender(<RequestError error={null} />);
    expect(screen.queryByRole("alert")).toBeNull();
    rerender(<RequestError error="Refresh failed." />);
    expect(screen.getByRole("alert")).toBeTruthy();
    rerender(<RequestError error={null} />);
    expect(document.querySelector("[data-toast-viewport]")).toBeNull();
  });

  it("shares one viewport with existing success and warning notifications", () => {
    const { unmount } = render(<>
      <ToastContainer toasts={[{ id: "saved", type: "success", message: "Saved" }]} exitingIds={new Set()} onDismiss={() => {}} />
      <ErrorToast message="Scan failed." />
      <ErrorToast message="Sync failed." />
    </>);
    expect(document.querySelectorAll("[data-toast-viewport]")).toHaveLength(1);
    expect(document.querySelectorAll("[data-toast-viewport] > .toast")).toHaveLength(3);
    fireEvent.click(screen.getAllByRole("button", { name: "Dismiss" })[1]);
    expect(screen.getByRole("status").textContent).toContain("Saved");
    expect(screen.getByRole("alert").closest('[role="status"]')).toBeNull();
    expect(screen.getByText("Sync failed.")).toBeTruthy();
    unmount();
    expect(document.querySelector("[data-toast-viewport]")).toBeNull();
  });

  it("preserves formatted broker rejection guidance and amounts in the toast", () => {
    const { container } = render(<OrderErrorBanner error="Order rejected by IB: Insufficient margin. Initial margin required is $503.00." />);
    expect(container.innerHTML).toBe("");
    expect(screen.getByRole("alert").textContent).toContain("$503.00");
    expect(screen.getByRole("alert").textContent).toContain("Insufficient margin");
  });
});
