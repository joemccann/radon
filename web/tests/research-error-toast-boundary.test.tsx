/** @vitest-environment jsdom */
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import ScannerTickerSearch from "@/components/ScannerTickerSearch";
import StarToggle from "@/components/StarToggle";

afterEach(cleanup);

describe("research error toast boundary", () => {
  it("announces invalid scanner input outside the form and keeps the scan closed", () => {
    const scan = vi.fn();
    const { container } = render(<ScannerTickerSearch id="test-scanner" onTickerScan={scan} />);
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "!!" } });
    fireEvent.submit(input.closest("form")!);
    const toast = screen.getByRole("alert");
    expect(scan).not.toHaveBeenCalled();
    expect(container.contains(toast)).toBe(false);
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(input.getAttribute("aria-describedby")).toBeNull();
    fireEvent.change(input, { target: { value: "META" } });
    fireEvent.submit(input.closest("form")!);
    expect(scan).toHaveBeenCalledWith(["META"]);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("catches rejected bookmark actions and preserves the toggle for retry", async () => {
    const toggle = vi.fn().mockRejectedValueOnce(new Error("fetch failed")).mockResolvedValueOnce(undefined);
    const { container } = render(<StarToggle active={false} onToggle={toggle} />);
    fireEvent.click(screen.getByTestId("star-toggle"));
    const toast = await screen.findByRole("alert");
    expect(toast.textContent).toContain("Unable to connect");
    expect(container.contains(toast)).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    fireEvent.click(screen.getByTestId("star-toggle"));
    await waitFor(() => expect(toggle).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
