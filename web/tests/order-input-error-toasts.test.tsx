// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { OrderQuantityInput } from "@/lib/order/components/OrderQuantityInput";
import { OrderPriceInput } from "@/lib/order/components/OrderPriceInput";

afterEach(cleanup);

describe("order field error presentation", () => {
  it("keeps quantity invalid while the operator dismisses its toast", () => {
    const { container, rerender } = render(<OrderQuantityInput value="0" onChange={vi.fn()} error="Enter at least one contract." />);
    const alert = screen.getByRole("alert");
    expect(container.contains(alert)).toBe(false);
    expect(alert.textContent).toContain("Enter at least one contract.");
    expect(screen.getByRole("spinbutton").getAttribute("aria-invalid")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("spinbutton").getAttribute("aria-invalid")).toBe("true");
    rerender(<OrderQuantityInput value="1" onChange={vi.fn()} />);
    expect(screen.getByRole("spinbutton").getAttribute("aria-invalid")).toBe("false");
  });

  it("presents price errors outside the ticket and preserves editing", () => {
    const onChange = vi.fn();
    const { container } = render(<OrderPriceInput value="0" onChange={onChange} prices={{ bid: 1, ask: 2, mid: 1.5, spread: 1, spreadPct: 66.67, available: true }} error="Enter a positive limit price." />);
    expect(container.contains(screen.getByRole("alert"))).toBe(false);
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "1.5" } });
    expect(onChange).toHaveBeenCalledWith("1.5");
  });
});
