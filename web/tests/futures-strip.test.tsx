/**
 * @vitest-environment jsdom
 */
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import FuturesStrip from "@/components/FuturesStrip";

const cellText = (container: HTMLElement, label: string): string =>
  container.querySelector(`[data-testid="futures-${label}"]`)?.textContent ?? "";

describe("FuturesStrip", () => {
  it("renders nothing when there are no quotes", () => {
    const { container } = render(<FuturesStrip quotes={[]} />);
    expect(container.querySelector(".futures-strip")).toBeNull();
  });

  it("shows last price and signed % change from prior close", () => {
    const { container } = render(
      <FuturesStrip quotes={[{ label: "ES", last: 5250, close: 5200 }]} />,
    );
    const text = cellText(container, "ES");
    // +50 on 5200 = +0.96%
    expect(text).toContain("ES");
    expect(text).toContain("5250.00");
    expect(text).toContain("+0.96%");
  });

  it("renders a negative change without a plus sign", () => {
    const { container } = render(
      <FuturesStrip quotes={[{ label: "NQ", last: 18900, close: 19000 }]} />,
    );
    const text = cellText(container, "NQ");
    expect(text).toContain("-0.53%");
    expect(text).not.toContain("+");
  });

  it("shows --- and no % when last is missing", () => {
    const { container } = render(
      <FuturesStrip quotes={[{ label: "RTY", last: null, close: 2300 }]} />,
    );
    const text = cellText(container, "RTY");
    expect(text).toContain("---");
    expect(text).not.toContain("%");
  });

  it("shows price but no % when prior close is missing", () => {
    const { container } = render(
      <FuturesStrip quotes={[{ label: "ES", last: 5250, close: null }]} />,
    );
    const text = cellText(container, "ES");
    expect(text).toContain("5250.00");
    expect(text).not.toContain("%");
  });
});
