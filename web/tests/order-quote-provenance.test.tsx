/**
 * @vitest-environment jsdom
 *
 * R-254 / R-255 — a nine-field quote panel must say whose book it is showing.
 *
 * R-254: the futures ticket's only quote block is the CASH INDEX, not the
 * contract being traded. OrderTab passes `prices[ticker]` (spot),
 * FuturesOrderForm forwards it as `${symbol} Index`, and
 * ListedContractOrderForm renders it as a full BID/MID/ASK/SPREAD/LAST/VOLUME/
 * HIGH/LOW/DAY panel directly above the contract selector and the Limit Price
 * input whose value is multiplied into the Notional row. web/CLAUDE.md is
 * explicit that a VIX derivative prices off the forward curve for its own
 * expiry, not spot — a front-month VIX future at 19 against a cash index at 15
 * is normal. The `Index` suffix sits inside the panel the numbers are in.
 *
 * R-255: the modify modal's panel is fed `applyRestingLimitToQuote(...)`, which
 * clamps `ask` down to the resting limit on a SELL. Commit 8ef78f81 gave that
 * doctored quote the same nine-field weight as a genuine market panel. A SELL
 * resting at 5.00 against a true market of 7.00/8.00 printed ASK 5.00,
 * MID 6.00, SPREAD 2.00 / 33%.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { ListedContractOrderForm } from "@/components/ticker-detail/ListedContractOrderForm";

afterEach(cleanup);

describe("ListedContractOrderForm quote provenance", () => {
  it("discloses, outside the panel, that the quote is not the traded contract's book", () => {
    const { container } = render(
      <ListedContractOrderForm
        eyebrow="VIX FUTURES"
        quoteLabel="VIX Index"
        quoteProvenance="Cash index, not the book for the contract selected below."
        priceData={null}
        contractSelector={<div />}
        multiplier={1000}
        multiplierDisplay="1,000"
        notionalLabel="Notional"
        limitPriceLabel="Limit Price"
        limitPriceStep={0.05}
        buildRiskInput={() => null}
        portfolio={null}
        surface="futures-form"
        buildSubmit={() => null}
        submitLabel={(action: string) => action}
      />,
    );
    const note = container.querySelector(".order-quote-provenance");
    expect(note).not.toBeNull();
    expect(note!.textContent).toMatch(/not the book/i);
  });
});
