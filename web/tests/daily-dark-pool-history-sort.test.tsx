/**
 * @vitest-environment jsdom
 */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import DailyDarkPoolHistory, {
  type DailyDarkPoolRow,
} from "@/components/flow-analysis/DailyDarkPoolHistory";

afterEach(() => cleanup());

const rows: DailyDarkPoolRow[] = [
  { date: "2026-07-20", flow_direction: "NEUTRAL", flow_strength: 10, dp_buy_ratio: 0.4, num_prints: 100 },
  { date: "2026-07-19", flow_direction: "ACCUMULATION", flow_strength: 90, dp_buy_ratio: 0.8, num_prints: 300 },
  { date: "2026-07-18", flow_direction: "DISTRIBUTION", flow_strength: 40, dp_buy_ratio: 0.2, num_prints: 200 },
];

describe("DailyDarkPoolHistory sort", () => {
  it("defaults to date desc and reorders on Strength", () => {
    render(<DailyDarkPoolHistory daily={rows} />);
    const table = screen.getByRole("table");
    const firstDate = () => within(table).getAllByRole("row")[1].textContent ?? "";
    expect(firstDate()).toContain("2026-07-20");
    expect(screen.getByRole("columnheader", { name: /date/i }).getAttribute("aria-sort")).toBe("descending");

    fireEvent.click(screen.getByRole("columnheader", { name: /strength/i }));
    expect(firstDate()).toContain("2026-07-20");
    fireEvent.click(screen.getByRole("columnheader", { name: /strength/i }));
    expect(firstDate()).toContain("2026-07-19");
    expect(screen.getByRole("columnheader", { name: /strength/i }).getAttribute("aria-sort")).toBe("descending");
  });
});
