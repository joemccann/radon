// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AiInfrastructureView } from "@/components/AiInfrastructurePanel";
import { seriesCadence } from "@/lib/aiIndustryPresentation";
import type { AiHistoryCoverage, AiIndicator } from "@/lib/aiInfrastructure";
import { aiFixture } from "./fixtures/aiInfrastructure";

vi.mock("@/components/AiIndustryHistoryChart", () => ({
  default: ({ cadence, points }: { cadence?: string; points: { series_id: string }[] }) => <p data-testid="chart-cadence" data-series={points[0]?.series_id}>{cadence ?? "none"}</p>,
}));
vi.mock("@/components/LlmTokenIndexCard", () => ({ default: () => <div>Legacy chart fixture</div> }));
afterEach(() => cleanup());

const ramp = aiFixture.sources.find(source => source.id === "ramp")!;
const coverage = (series_id: string, cadence?: string): AiHistoryCoverage => ({ source_id: "ramp", series_id, unit: "USD/Mtok", observation_count: 3, displayed_count: 3, observed_from: "2026-09-01", observed_through: "2026-09-03", ...(cadence ? { cadence } : {}) });

describe("per-series cadence", () => {
  it("prefers the series cadence and falls back to the source cadence", () => {
    expect(seriesCadence(coverage("tokens.price.blended.anthropic:m:c", "daily"), ramp)).toBe("daily");
    expect(seriesCadence(coverage("tokens.volume.all:m:c", "weekly"), ramp)).toBe("weekly");
    expect(seriesCadence(coverage("spend.median_pepm:m:c"), ramp)).toBe("monthly");
    expect(seriesCadence(undefined, ramp)).toBe("monthly");
    expect(seriesCadence(undefined, undefined)).toBeUndefined();
  });

  it("captions D7 price charts daily and volume/spend charts weekly while D5 stays monthly", () => {
    const spend = aiFixture.indicators.find(item => item.id === "D5")!;
    const base = spend.history[0];
    const series = [
      ["tokens.price.blended.anthropic:m:c", "USD/Mtok", "daily"],
      ["tokens.volume.all:m:c", "tokens", "weekly"],
      ["tokens.spend.all:m:c", "USD", "weekly"],
    ] as const;
    const token: AiIndicator = {
      ...spend, id: "D7", title: "Ramp token economics",
      metrics: [{ ...spend.metrics[0], id: "tokens.price.blended.anthropic", unit: "USD/Mtok", cadence: "daily" }],
      history: series.flatMap(([series_id, unit]) => ["2026-09-01", "2026-09-02", "2026-09-03"].map(date => ({ ...base, date, series_id, unit, value: 1, source_id: "ramp" }))),
      history_coverage: series.map(([series_id, unit, cadence]) => ({ ...coverage(series_id, cadence), unit })),
    };
    render(<AiInfrastructureView data={{ ...aiFixture, indicators: [...aiFixture.indicators, token] }} error={null} loading={false} refresh={() => {}} />);
    fireEvent.change(screen.getByLabelText("Measure"), { target: { value: "D5" } });
    expect(screen.getByTestId("chart-cadence").textContent).toBe("monthly");
    fireEvent.change(screen.getByLabelText("Measure"), { target: { value: "D7" } });
    expect(screen.getByTestId("chart-cadence").textContent).toBe("daily");
    fireEvent.change(screen.getByLabelText("Observation series"), { target: { value: "1" } });
    expect(screen.getByTestId("chart-cadence").getAttribute("data-series")).toBe("tokens.volume.all:m:c");
    expect(screen.getByTestId("chart-cadence").textContent).toBe("weekly");
    fireEvent.change(screen.getByLabelText("Observation series"), { target: { value: "2" } });
    expect(screen.getByTestId("chart-cadence").textContent).toBe("weekly");
  });
});
