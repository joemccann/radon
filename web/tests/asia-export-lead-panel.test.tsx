/** @vitest-environment jsdom */
import React from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import AsiaExportLeadPanel, {
  exportChartRows,
} from "@/components/AsiaExportLeadPanel";

const state = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
vi.mock("@/lib/useAsiaExportLead", () => ({
  useAsiaExportLead: () => state.value,
}));
vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, hasMounted: true }),
}));
beforeAll(() => {
  class Observer {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", Observer);
});
function data() {
  const now = new Date();
  const rows = Array.from({ length: 36 }, (_, index) => {
    const stamp = new Date(
      Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 36 + index, 1),
    );
    const lead = new Date(
      Date.UTC(stamp.getUTCFullYear(), stamp.getUTCMonth() + 6, 1),
    );
    return {
      date: stamp.toISOString().slice(0, 7),
      lead_date: lead.toISOString().slice(0, 7),
      taiwan_exports_usd_mn: 40000 + index,
      korea_exports_usd_mn: 50000 + index,
      taiwan_yoy_pct: 10 + index,
      korea_yoy_pct: 20 + index,
      mean_yoy_pct: 15 + index,
      ma3_yoy_pct: 14 + index,
      ma3_change_pp: 1,
    };
  });
  const latest = rows.at(-1)!;
  const end = new Date(
    Date.UTC(
      Number(latest.date.slice(0, 4)),
      Number(latest.date.slice(5, 7)),
      0,
    ),
  );
  return {
    missing: false,
    scan_time: now.toISOString(),
    source_last_modified: null,
    data_date: end.toISOString().slice(0, 10),
    count: rows.length,
    series: rows,
    current: { ...latest, growth: "expanding", momentum: "accelerating" },
    sources: {
      taiwan: { data_date: latest.date },
      korea: { data_date: latest.date },
    },
    comparison: {
      available: false,
      label: "MSCI US forward 12-month EPS",
      reason: "Licensed EPS series not connected",
    },
  };
}
beforeEach(() => {
  state.value = {
    data: data(),
    loading: false,
    syncing: false,
    lastSync: new Date(),
  };
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("Asia export lead", () => {
  it("shows source-specific loading and an honest empty state", () => {
    state.value = { data: null, loading: true };
    const view = render(<AsiaExportLeadPanel />);
    expect(screen.getByText(/Loading.*export/i)).toBeTruthy();
    state.value = { data: { missing: true }, loading: false };
    view.rerender(<AsiaExportLeadPanel />);
    expect(
      screen.getByText("No export data yet", { exact: true }),
    ).toBeTruthy();
  });
  it("renders real chart paths, dated source values and the comparison limit", () => {
    const { container } = render(<AsiaExportLeadPanel />);
    expect(
      screen.getByTestId("asia-export-lead-strip-asof").textContent,
    ).toContain((state.value.data as ReturnType<typeof data>).data_date);
    expect(
      screen.getByText(/Total exports, not semiconductor-only exports/i),
    ).toBeTruthy();
    expect(
      screen.getByText(/MSCI US forward EPS comparison unavailable/i),
    ).toBeTruthy();
    expect(
      container.querySelectorAll("svg path[stroke]").length,
    ).toBeGreaterThan(0);
    expect(screen.getByTestId("asia-export-lead-brush")).toBeTruthy();
  });
  it("switches between observed and six-month alignment without inventing EPS", () => {
    const { container } = render(<AsiaExportLeadPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Shift +6M" }));
    expect(
      screen.getByText(/Six-month alignment is not an earnings forecast/i),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Countries" }));
    expect(container.textContent).toContain("Taiwan");
    expect(container.textContent).toContain("Korea");
    expect(container.textContent).not.toContain("No downturn signal yet");
  });
  it("keeps malformed/null readings out of SVG coordinates", () => {
    const value = data();
    value.series[10].ma3_yoy_pct = Number.NaN;
    value.series[11].taiwan_yoy_pct = Number.POSITIVE_INFINITY;
    state.value.data = value;
    const { container } = render(<AsiaExportLeadPanel />);
    for (const path of container.querySelectorAll("svg path"))
      expect(path.getAttribute("d") ?? "").not.toMatch(/NaN|Infinity/);
  });
  it("shows a ticking next-sample countdown without marking monthly dates overdue", () => {
    const beforeSlot = new Date();
    beforeSlot.setUTCHours(8, 40, 0, 0);
    vi.useFakeTimers();
    vi.setSystemTime(beforeSlot);
    render(<AsiaExportLeadPanel />);
    const rail = screen.getByTestId("asia-export-lead-freshness-rail");
    expect(rail.textContent).toContain("Next check");
    expect(rail.textContent).toContain("1h 00m");
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    expect(rail.textContent).toContain("59m 58s");
    expect(rail.getAttribute("data-state")).not.toBe("overdue");
  });
});

describe("export chart calendar and value integrity", () => {
  it("shifts six calendar months over year rollover and preserves source dates", () => {
    const input = data()
      .series.slice(-2)
      .map((row, index) => ({
        ...row,
        date: `2026-${index === 0 ? "09" : "10"}`,
      }));
    const original = JSON.stringify(input);
    expect(
      exportChartRows(input, true).map((row) => [row.sourceMonth, row.date]),
    ).toEqual([
      ["2026-09", "2027-03"],
      ["2026-10", "2027-04"],
    ]);
    expect(JSON.stringify(input)).toBe(original);
  });
  it("materializes missing months and rejects non-finite chart values", () => {
    const input = data()
      .series.slice(0, 2)
      .map((row, index) => ({
        ...row,
        date: index === 0 ? "2026-01" : "2026-03",
        ma3_yoy_pct: index === 0 ? NaN : 3,
        taiwan_yoy_pct: Infinity,
      }));
    const rows = exportChartRows(input, false);
    expect(rows.map((row) => row.date)).toEqual([
      "2026-01",
      "2026-02",
      "2026-03",
    ]);
    expect(rows[1]).toEqual({
      date: "2026-02",
      sourceMonth: "2026-02",
      composite: null,
      taiwan: null,
      korea: null,
    });
    expect(rows[0].composite).toBeNull();
    expect(rows[2].taiwan).toBeNull();
  });
  it("uses calendar monthly windows and countries always retain observed dates", () => {
    render(<AsiaExportLeadPanel />);
    const chart = screen.getByTestId("asia-export-lead-chart-section");
    fireEvent.click(screen.getByRole("button", { name: "1Y" }));
    const payload = state.value.data as ReturnType<typeof data>;
    expect(chart.getAttribute("data-range-start")).toBe(
      payload.series.at(-12)!.date,
    );
    expect(chart.getAttribute("data-range-end")).toBe(payload.current.date);
    fireEvent.click(screen.getByRole("button", { name: "Shift +6M" }));
    expect(chart.getAttribute("data-alignment")).toBe("shifted");
    fireEvent.click(screen.getByRole("button", { name: "Countries" }));
    expect(chart.getAttribute("data-alignment")).toBe("observed");
    expect(
      screen
        .getByRole("button", { name: "Shift +6M" })
        .hasAttribute("disabled"),
    ).toBe(true);
    expect(screen.getByTestId("asia-export-lead-strip-asof").textContent).toBe(
      payload.data_date,
    );
  });
  it("renders unavailable statistics as dashes with dated states", () => {
    const payload = data();
    Object.assign(payload.current, {
      ma3_yoy_pct: null,
      ma3_change_pp: Infinity,
    });
    state.value.data = payload;
    render(<AsiaExportLeadPanel />);
    expect(
      screen.getByTestId("asia-export-lead-strip-composite").textContent,
    ).toContain("---");
    expect(
      screen.getByTestId("asia-export-lead-strip-composite").textContent,
    ).toContain(`${payload.current.date} growth: unavailable`);
    expect(
      screen.getByTestId("asia-export-lead-strip-change").textContent,
    ).toContain(`${payload.current.date} momentum: unavailable`);
    expect(
      screen.getByTestId("asia-export-lead-sampled").textContent,
    ).toContain("UTC");
  });
});

describe("export request errors", () => {
  it("retains the measured series with retry guidance in the shared toast", () => {
    const retry = vi.fn();
    state.value = {
      ...state.value,
      error: "The data could not be loaded.",
      syncNow: retry,
    };
    render(<AsiaExportLeadPanel />);
    expect(screen.getByRole("alert").textContent).toContain(
      "Showing the last available data.",
    );
    expect(screen.getByTestId("asia-export-lead-chart-section")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledTimes(1);
  });
});

describe("official export attribution", () => {
  it("links the human-readable source landing pages and reuse policies", () => {
    render(<AsiaExportLeadPanel />);
    expect(
      screen
        .getByRole("link", { name: "Taiwan MOF dataset 8378" })
        .getAttribute("href"),
    ).toBe("https://data.gov.tw/en/datasets/8378");
    expect(
      screen
        .getByRole("link", { name: "South Korea KCS" })
        .getAttribute("href"),
    ).toBe("https://www.tradedata.go.kr/cts/index_eng.do");
    expect(
      screen.getByRole("link", { name: "OGDL 1.0" }).getAttribute("href"),
    ).toBe("https://data.gov.tw/license");
    expect(
      screen
        .getByRole("link", { name: "KCS public data policy" })
        .getAttribute("href"),
    ).toBe(
      "https://www.customs.go.kr/kcs/cm/cntntsView.do?cntntsId=2611&mi=7422",
    );
  });
});
