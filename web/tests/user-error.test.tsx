// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { readErrorResponse, userErrorMessage } from "@/lib/userError";
import { formatOrderError } from "@/lib/orderError";
import RequestError from "@/components/RequestError";
import ToastContainer from "@/components/Toast";

afterEach(cleanup);
const raw = JSON.stringify({ scan_time: "", results: [], scan_succeeded: false, error: "Radon API 502: Subprocess capacity exhausted" });

describe("safe error presentation", () => {
  it.each([raw, { error: { detail: raw } }, new Error(raw)])("rewrites nested capacity envelopes", input => {
    expect(userErrorMessage(input)).toBe("This service is busy. Please try again shortly.");
  });
  it.each(["<html><body>nginx /opt/radon/service.py</body></html>", "Traceback (most recent call last): /home/radon/service.py", '{"internal":"secret"}', '{"error":', "TypeError: Cannot read properties", "token=privatevalue", "SELECT secret FROM credentials", "https://internal.example/api?secret=value"])("does not expose diagnostics: %s", input => {
    expect(userErrorMessage(input, "Could not load data.")).toBe("Could not load data.");
  });
  it.each(["Quantity must not exceed 500 shares.", "Initial margin required is $503.00.", "Enter a price below 429."])("preserves numbers in financial and validation copy", text => {
    expect(userErrorMessage(text)).toBe(text);
  });
  it("keeps a useful validation message and broker rejection amounts", () => {
    expect(userErrorMessage({ detail: "Enter at least one ticker." })).toBe("Enter at least one ticker.");
    expect(formatOrderError(JSON.stringify({ error: "Order rejected by IB: Insufficient margin" })).details).toEqual(["Insufficient margin."]);
    expect(formatOrderError(raw).summary).toContain("Check order status");
  });
  it.each([[401, "Your session"], [403, "do not have access"], [429, "Too many requests"], [502, "temporarily unavailable"], [504, "took too long"]])("uses HTTP %s when a proxy returns HTML", async (status, phrase) => {
    const message = await readErrorResponse(new Response("<html>proxy error</html>", { status: status as number }));
    expect(message).toContain(phrase);
    expect(message).not.toContain("<html>");
  });
  it.each(["HTTP 502", "Order failed (502)", "Order placement timeout"])("does not suggest blind order resubmission", message => {
    expect(formatOrderError(message).summary).toContain("Check order status before trying again.");
    expect(formatOrderError(message).summary).not.toContain("502");
  });
  it("retains last data notice and exposes an explicit retry", () => {
    const retry = vi.fn();
    render(<RequestError error={raw} onRetry={retry} retainedData />);
    expect(screen.getByRole("alert").textContent).toContain("Showing the last available data");
    expect(screen.getByRole("alert").textContent).not.toMatch(/scan_time|Subprocess|502/);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledTimes(1);
  });
  it("protects error toasts without changing informational messages", () => {
    render(<ToastContainer toasts={[{ id: "error", type: "error", message: raw }, { id: "success", type: "success", message: "Order filled" }]} exitingIds={new Set()} onDismiss={() => {}} />);
    expect(screen.getByRole("alert").textContent).toContain("This service is busy");
    expect(screen.getByText("Order filled")).toBeTruthy();
  });
});
