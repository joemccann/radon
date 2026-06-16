/**
 * @vitest-environment jsdom
 */
import { describe, it, expect } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { MetricCell } from "../components/mobile/MetricCell";
import { BuySellRow } from "../components/mobile/BuySellRow";

describe("MetricCell", () => {
  it("renders label above value in the DOM", () => {
    const { container } = render(<MetricCell label="NET LIQ" value="$12,345" />);
    const label = container.querySelector(".m-metric__label");
    const value = container.querySelector(".m-metric__value");
    expect(label).not.toBeNull();
    expect(value).not.toBeNull();
    expect(label!.textContent).toBe("NET LIQ");
    expect(value!.textContent).toBe("$12,345");
    // Label must come before value in DOM order
    const children = Array.from(container.querySelector(".m-metric")!.children);
    expect(children.indexOf(label!)).toBeLessThan(children.indexOf(value!));
    cleanup();
  });

  it("applies hero size class", () => {
    const { container } = render(<MetricCell label="P&L" value="+$500" size="hero" />);
    const value = container.querySelector(".m-metric__value");
    expect(value?.classList.contains("m-metric__value--hero")).toBe(true);
    cleanup();
  });

  it("applies secondary size class", () => {
    const { container } = render(<MetricCell label="DELTA" value="0.45" size="secondary" />);
    const value = container.querySelector(".m-metric__value");
    expect(value?.classList.contains("m-metric__value--secondary")).toBe(true);
    cleanup();
  });

  it("applies pos tone class", () => {
    const { container } = render(<MetricCell label="GAIN" value="+12%" tone="pos" />);
    const value = container.querySelector(".m-metric__value");
    expect(value?.classList.contains("m-metric__value--pos")).toBe(true);
    cleanup();
  });

  it("applies neg tone class", () => {
    const { container } = render(<MetricCell label="LOSS" value="-8%" tone="neg" />);
    const value = container.querySelector(".m-metric__value");
    expect(value?.classList.contains("m-metric__value--neg")).toBe(true);
    cleanup();
  });

  it("applies warn tone class", () => {
    const { container } = render(<MetricCell label="IV" value="38%" tone="warn" />);
    const value = container.querySelector(".m-metric__value");
    expect(value?.classList.contains("m-metric__value--warn")).toBe(true);
    cleanup();
  });

  it("applies no tone class when tone is omitted", () => {
    const { container } = render(<MetricCell label="VOL" value="1.2M" />);
    const value = container.querySelector(".m-metric__value");
    expect(value?.className).not.toContain("--pos");
    expect(value?.className).not.toContain("--neg");
    expect(value?.className).not.toContain("--warn");
    expect(value?.className).not.toContain("--mut");
    cleanup();
  });

  it("uses primary size by default", () => {
    const { container } = render(<MetricCell label="SIZE" value="10" />);
    const value = container.querySelector(".m-metric__value");
    expect(value?.classList.contains("m-metric__value--primary")).toBe(true);
    cleanup();
  });
});

describe("BuySellRow", () => {
  it("renders BUY side with correct class and direction label above price", () => {
    const { container } = render(
      <BuySellRow side="BUY" label="AAPL 200 CALL" price="$3.50" />
    );
    const leg = container.querySelector(".m-leg");
    expect(leg).not.toBeNull();
    expect(leg?.classList.contains("m-leg--buy")).toBe(true);
    expect(leg?.classList.contains("m-leg--sell")).toBe(false);

    const direction = container.querySelector(".m-leg__direction");
    const price = container.querySelector(".m-leg__price");
    expect(direction?.textContent).toBe("BUY");
    expect(price?.textContent).toBe("$3.50");

    // Direction must appear before price in DOM order
    const children = Array.from(leg!.children);
    expect(children.indexOf(direction!)).toBeLessThan(children.indexOf(price!));
    cleanup();
  });

  it("renders SELL side with correct class and direction label above price", () => {
    const { container } = render(
      <BuySellRow side="SELL" label="TSLA 250 PUT" price="$8.20" />
    );
    const leg = container.querySelector(".m-leg");
    expect(leg?.classList.contains("m-leg--sell")).toBe(true);
    expect(leg?.classList.contains("m-leg--buy")).toBe(false);

    const direction = container.querySelector(".m-leg__direction");
    expect(direction?.textContent).toBe("SELL");
    cleanup();
  });

  it("renders label text in .m-leg__sub", () => {
    const { container } = render(
      <BuySellRow side="BUY" label="AAPL 200 CALL" price="$3.50" />
    );
    const sub = container.querySelector(".m-leg__sub");
    expect(sub?.textContent).toBe("AAPL 200 CALL");
    cleanup();
  });

  it("renders optional sub prop in additional .m-leg__sub element", () => {
    const { container } = render(
      <BuySellRow side="BUY" label="AAPL 200 CALL" price="$3.50" sub="10 contracts" />
    );
    const subs = container.querySelectorAll(".m-leg__sub");
    expect(subs.length).toBe(2);
    expect(subs[1].textContent).toBe("10 contracts");
    cleanup();
  });

  it("renders no extra sub element when sub is omitted", () => {
    const { container } = render(
      <BuySellRow side="SELL" label="PUT" price="$1.00" />
    );
    const subs = container.querySelectorAll(".m-leg__sub");
    expect(subs.length).toBe(1);
    cleanup();
  });
});
