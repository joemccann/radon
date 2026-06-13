/**
 * @vitest-environment jsdom
 *
 * Component-level tests for LocateFeeChip.
 *
 * Verifies:
 *   - "NO LOCATE" renders in negative tone for missing/not-shortable data
 *   - "HTB · {fee}%" renders in warning tone for locate-only data
 *   - "EASY · {shares}" renders in positive tone for easy-to-borrow data
 *   - Source and as_of are included in secondary text
 *   - data-status attribute is set correctly for automation
 */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { LocateFeeChip } from "../lib/order/components/LocateFeeChip";
import type { ShortAvailabilityData } from "../lib/order/hooks/useShortAvailability";

afterEach(() => cleanup());

function makeData(overrides: Partial<ShortAvailabilityData> = {}): ShortAvailabilityData {
  return {
    ticker: "SPY",
    shortable: true,
    difficulty: 3.0,
    shortable_shares: 1_500_000,
    fee_rate: 0.25,
    rebate_rate: 0.10,
    source: "ib",
    as_of: "2026-06-12T14:00:00Z",
    missing: false,
    ...overrides,
  };
}

describe("<LocateFeeChip /> — NO LOCATE state", () => {
  it("renders 'NO LOCATE' when missing:true", () => {
    render(
      <LocateFeeChip
        status="no-locate"
        data={makeData({ missing: true, shortable: null })}
      />,
    );
    const chip = screen.getByTestId("locate-fee-chip");
    expect(chip.getAttribute("data-status")).toBe("no-locate");
    expect(chip.textContent).toContain("NO LOCATE");
  });

  it("renders 'NO LOCATE' when shortable:false", () => {
    render(
      <LocateFeeChip
        status="no-locate"
        data={makeData({ shortable: false, missing: false })}
      />,
    );
    expect(screen.getByTestId("locate-fee-chip").textContent).toContain("NO LOCATE");
  });

  it("applies negative CSS token color for NO LOCATE", () => {
    render(
      <LocateFeeChip
        status="no-locate"
        data={makeData({ missing: true })}
      />,
    );
    // The chip <span> should have color: var(--negative)
    const chip = screen.getByTestId("locate-fee-chip");
    const chipSpan = chip.querySelector("span");
    expect(chipSpan?.style.color).toBe("var(--negative)");
  });
});

describe("<LocateFeeChip /> — HTB state", () => {
  it("renders 'HTB' label with fee when fee_rate is available", () => {
    render(
      <LocateFeeChip
        status="htb"
        data={makeData({ shortable: null, fee_rate: 12.5, difficulty: 2.0, missing: false })}
      />,
    );
    const chip = screen.getByTestId("locate-fee-chip");
    expect(chip.getAttribute("data-status")).toBe("htb");
    expect(chip.textContent).toContain("HTB");
    expect(chip.textContent).toContain("12.50%");
  });

  it("renders 'HTB' without fee when fee_rate is null", () => {
    render(
      <LocateFeeChip
        status="htb"
        data={makeData({ shortable: null, fee_rate: null, difficulty: 2.0, missing: false })}
      />,
    );
    const chip = screen.getByTestId("locate-fee-chip");
    expect(chip.textContent).toContain("HTB");
    expect(chip.textContent).not.toContain("%");
  });

  it("applies warning CSS token color for HTB", () => {
    render(
      <LocateFeeChip
        status="htb"
        data={makeData({ shortable: null, fee_rate: 5.0, missing: false })}
      />,
    );
    const chipSpan = screen.getByTestId("locate-fee-chip").querySelector("span");
    expect(chipSpan?.style.color).toBe("var(--warning)");
  });
});

describe("<LocateFeeChip /> — EASY state", () => {
  it("renders 'EASY' with formatted share count for large pools", () => {
    render(
      <LocateFeeChip
        status="easy"
        data={makeData({ shortable: true, shortable_shares: 2_500_000, missing: false })}
      />,
    );
    const chip = screen.getByTestId("locate-fee-chip");
    expect(chip.getAttribute("data-status")).toBe("easy");
    expect(chip.textContent).toContain("EASY");
    expect(chip.textContent).toContain("2.5M");
  });

  it("renders 'EASY' with K-suffix for thousands", () => {
    render(
      <LocateFeeChip
        status="easy"
        data={makeData({ shortable: true, shortable_shares: 75_000, missing: false })}
      />,
    );
    expect(screen.getByTestId("locate-fee-chip").textContent).toContain("75K");
  });

  it("renders 'EASY' without shares when shortable_shares is null", () => {
    render(
      <LocateFeeChip
        status="easy"
        data={makeData({ shortable: true, shortable_shares: null, missing: false })}
      />,
    );
    const chip = screen.getByTestId("locate-fee-chip");
    // The chip label span is the first child; it should not contain "·"
    const chipLabelSpan = chip.querySelector("span:first-child");
    expect(chipLabelSpan?.textContent).toBe("EASY");
    expect(chipLabelSpan?.textContent).not.toContain("·");
  });

  it("applies positive CSS token color for EASY", () => {
    render(
      <LocateFeeChip
        status="easy"
        data={makeData({ shortable: true, missing: false })}
      />,
    );
    const chipSpan = screen.getByTestId("locate-fee-chip").querySelector("span");
    expect(chipSpan?.style.color).toBe("var(--positive)");
  });
});

describe("<LocateFeeChip /> — SPX-03 AAPL live repro", () => {
  it("renders EASY (not NO LOCATE) for shortable:true + 190M shares + fee_rate:null", () => {
    // This is the AAPL live case: tick 89 (shortable_shares) arrived but tick 46
    // (difficulty) did not. After the server fix, shortable=true is derived from
    // shares > 0, so the chip must render green EASY, never red NO LOCATE.
    render(
      <LocateFeeChip
        status="easy"
        data={makeData({ shortable: true, shortable_shares: 190_797_965, fee_rate: null, difficulty: null, missing: false })}
      />,
    );
    const chip = screen.getByTestId("locate-fee-chip");
    expect(chip.getAttribute("data-status")).toBe("easy");
    expect(chip.textContent).toContain("EASY");
    expect(chip.textContent).not.toContain("NO LOCATE");
    // Positive green token for easy-to-borrow
    const chipSpan = chip.querySelector("span");
    expect(chipSpan?.style.color).toBe("var(--positive)");
  });

  it("renders EASY with 190.8M share count formatted correctly", () => {
    render(
      <LocateFeeChip
        status="easy"
        data={makeData({ shortable: true, shortable_shares: 190_797_965, fee_rate: null, missing: false })}
      />,
    );
    const chip = screen.getByTestId("locate-fee-chip");
    // 190_797_965 / 1_000_000 = 190.8M
    expect(chip.textContent).toContain("190.8M");
  });

  it("renders HTB with fee for locate-only (difficulty 1.5-2.5 range)", () => {
    render(
      <LocateFeeChip
        status="htb"
        data={makeData({ shortable: null, difficulty: 2.0, shortable_shares: 50_000, fee_rate: 4.75, missing: false })}
      />,
    );
    const chip = screen.getByTestId("locate-fee-chip");
    expect(chip.getAttribute("data-status")).toBe("htb");
    expect(chip.textContent).toContain("HTB");
    expect(chip.textContent).toContain("4.75%");
    const chipSpan = chip.querySelector("span");
    expect(chipSpan?.style.color).toBe("var(--warning)");
  });

  it("renders NO LOCATE only for shortable:false", () => {
    render(
      <LocateFeeChip
        status="no-locate"
        data={makeData({ shortable: false, difficulty: 1.0, shortable_shares: 0, fee_rate: null, missing: false })}
      />,
    );
    const chip = screen.getByTestId("locate-fee-chip");
    expect(chip.getAttribute("data-status")).toBe("no-locate");
    expect(chip.textContent).toContain("NO LOCATE");
    const chipSpan = chip.querySelector("span");
    expect(chipSpan?.style.color).toBe("var(--negative)");
  });

  it("renders NO LOCATE for fully missing data (both difficulty and shares absent)", () => {
    render(
      <LocateFeeChip
        status="no-locate"
        data={makeData({ shortable: null, difficulty: null, shortable_shares: null, fee_rate: null, missing: true })}
      />,
    );
    const chip = screen.getByTestId("locate-fee-chip");
    expect(chip.getAttribute("data-status")).toBe("no-locate");
    expect(chip.textContent).toContain("NO LOCATE");
  });
});

describe("<LocateFeeChip /> — secondary metadata", () => {
  it("includes IB source in secondary text", () => {
    render(
      <LocateFeeChip
        status="easy"
        data={makeData({ source: "ib" })}
      />,
    );
    expect(screen.getByTestId("locate-fee-chip").textContent).toContain("IB");
  });

  it("includes UW source in secondary text", () => {
    render(
      <LocateFeeChip
        status="htb"
        data={makeData({ source: "uw", shortable: null, fee_rate: 8.0 })}
      />,
    );
    expect(screen.getByTestId("locate-fee-chip").textContent).toContain("UW");
  });

  it("omits source label when source is 'none'", () => {
    render(
      <LocateFeeChip
        status="no-locate"
        data={makeData({ source: "none", missing: true })}
      />,
    );
    const secondary = screen.getByTestId("locate-fee-chip").querySelector("span:last-child");
    expect(secondary?.textContent).not.toContain("NONE");
  });

  it("renders 4px border-radius on the chip span (brand token compliance)", () => {
    render(
      <LocateFeeChip
        status="easy"
        data={makeData()}
      />,
    );
    const chipSpan = screen.getByTestId("locate-fee-chip").querySelector("span");
    expect(chipSpan?.style.borderRadius).toBe("4px");
  });
});
