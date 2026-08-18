/**
 * @vitest-environment jsdom
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import DailyDarkPoolHistory, {
  type DailyDarkPoolRow,
} from "@/components/flow-analysis/DailyDarkPoolHistory";

afterEach(() => cleanup());

function makeSessions(n: number): DailyDarkPoolRow[] {
  const rows: DailyDarkPoolRow[] = [];
  for (let i = 0; i < n; i += 1) {
    const day = String(20 - i).padStart(2, "0");
    rows.push({
      date: `2026-07-${day}`,
      flow_direction: i % 3 === 0 ? "ACCUMULATION" : i % 3 === 1 ? "DISTRIBUTION" : "NEUTRAL",
      flow_strength: 20 + i,
      dp_buy_ratio: 0.4 + (i % 5) * 0.05,
      num_prints: 100 + i * 10,
    });
  }
  return rows;
}

describe("DailyDarkPoolHistory", () => {
  it("previews five sessions and discloses the rest with a chart", () => {
    render(<DailyDarkPoolHistory daily={makeSessions(20)} />);

    expect(screen.getByTestId("daily-dp-history")).toBeTruthy();
    expect(screen.getByText("20 SESSIONS")).toBeTruthy();

    // Newest five dates visible; oldest session hidden until expand.
    expect(screen.getByText("2026-07-20")).toBeTruthy();
    expect(screen.queryByText("2026-07-01")).toBeNull();
    expect(screen.queryByTestId("daily-dp-history-chart")).toBeNull();

    fireEvent.click(screen.getByTestId("daily-dp-history-toggle"));

    expect(screen.getByText("2026-07-01")).toBeTruthy();
    expect(screen.getByTestId("daily-dp-history-chart")).toBeTruthy();
    expect(screen.getByLabelText(/buy percentage over sessions/i)).toBeTruthy();
  });

  it("keeps the session toggle in the module header, not between table and chart", () => {
    render(<DailyDarkPoolHistory daily={makeSessions(20)} />);

    const root = screen.getByTestId("daily-dp-history");
    const header = root.querySelector(".section-header");
    const toggle = screen.getByTestId("daily-dp-history-toggle");
    expect(header?.contains(toggle)).toBe(true);
    expect(screen.queryByTestId("daily-dp-history-disclosure")).toBeNull();

    fireEvent.click(toggle);

    expect(header?.contains(screen.getByTestId("daily-dp-history-toggle"))).toBe(true);
    const body = root.querySelector(".section-body");
    const table = body?.querySelector(".ticker-flow-daily");
    const chart = screen.getByTestId("daily-dp-history-chart");
    expect(table?.nextElementSibling).toBe(chart);
  });

  it("renders a chart header with buy-% title and chronological window", () => {
    render(<DailyDarkPoolHistory daily={makeSessions(20)} />);
    fireEvent.click(screen.getByTestId("daily-dp-history-toggle"));

    const header = screen.getByTestId("daily-dp-history-chart-header");
    expect(header.textContent).toMatch(/Buy % by session/i);
    expect(header.textContent).toContain("07-01");
    expect(header.textContent).toContain("07-20");
    expect(header.textContent).toMatch(/oldest to newest/i);
  });

  it("renders chart immediately when history fits the preview", () => {
    render(<DailyDarkPoolHistory daily={makeSessions(3)} />);
    expect(screen.queryByTestId("daily-dp-history-toggle")).toBeNull();
    expect(screen.getByTestId("daily-dp-history-chart")).toBeTruthy();
    expect(screen.getByTestId("daily-dp-history-chart-header").textContent).toContain("07-18");
  });

  it("renders nothing for empty daily", () => {
    const { container } = render(<DailyDarkPoolHistory daily={[]} />);
    expect(container.innerHTML).toBe("");
  });
});
