/**
 * @vitest-environment jsdom
 */
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, waitFor } from "@testing-library/react";
import SingleLegOrderTicket from "../components/SingleLegOrderTicket";

function renderTicket(overrides: Partial<React.ComponentProps<typeof SingleLegOrderTicket>> = {}) {
  const props: React.ComponentProps<typeof SingleLegOrderTicket> = {
    defaultAction: "SELL",
    defaultTif: "DAY",
    quantity: "100",
    onQuantityChange: () => {},
    quantityPlaceholder: "Shares",
    bid: 170,
    mid: 171,
    ask: 172,
    isValid: true,
    limitPrice: "171.00",
    onLimitPriceChange: () => {},
    riskGate: null,
    buildPayload: ({ action, quantity, limitPrice, tif, orderType, stopPrice }) => ({
      type: "stock",
      symbol: "AAPL",
      action,
      quantity,
      tif,
      ...(orderType === "STP"
        ? { orderType, stopPrice }
        : orderType === "STP LMT"
          ? { orderType, stopPrice, limitPrice }
          : { limitPrice }),
    }),
    buildSuccessMessage: () => "ok",
    ...overrides,
  };
  return render(<SingleLegOrderTicket {...props} />);
}

describe("SingleLegOrderTicket stop types", () => {
  it("STP submit posts orderType and stopPrice", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: "ok" }),
    });
    global.fetch = fetchMock;

    const { getByTestId, getByRole } = renderTicket();
    fireEvent.click(getByTestId("order-type-stp"));
    fireEvent.change(getByTestId("order-stop-price"), { target: { value: "170" } });
    fireEvent.click(getByRole("button", { name: "Place Order" }));
    fireEvent.click(getByRole("button", { name: "Confirm Order" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.orderType).toBe("STP");
    expect(body.stopPrice).toBe(170);
    expect(body.limitPrice).toBeUndefined();
  });
});
