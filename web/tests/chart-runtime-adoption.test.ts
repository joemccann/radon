import { readFileSync } from "fs";
import { resolve } from "path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, test } from "vitest";
import ChartPanel from "../components/charts/ChartPanel";
import RegimeRelationshipView from "../components/RegimeRelationshipView";

const performanceSource = readFileSync(
  resolve(__dirname, "../components/PerformancePanel.tsx"),
  "utf-8",
);
const criHistorySource = readFileSync(
  resolve(__dirname, "../components/CriHistoryChart.tsx"),
  "utf-8",
);
const relationshipSource = readFileSync(
  resolve(__dirname, "../components/RegimeRelationshipView.tsx"),
  "utf-8",
);
const regimeSource = readFileSync(
  resolve(__dirname, "../components/RegimePanel.tsx"),
  "utf-8",
);

const relationshipHistory = [
  { date: "2026-03-05", realized_vol: 14.1, cor1m: 17.8 },
  { date: "2026-03-06", realized_vol: 13.4, cor1m: 16.9 },
  { date: "2026-03-07", realized_vol: 12.8, cor1m: 18.6 },
  { date: "2026-03-10", realized_vol: 12.5, cor1m: 19.7 },
];

test("[runtime] PerformancePanel uses the shared chart shell for the YTD equity curve", () => {
  expect(performanceSource).toContain("import ChartPanel");
  expect(performanceSource).toContain("<ChartPanel");
  expect(performanceSource).toContain('family="analytical-time-series"');
});

test("[runtime] ChartPanel emits shared family metadata and semantic legend swatches", () => {
  const html = renderToStaticMarkup(
    createElement(
      ChartPanel,
      {
        family: "analytical-time-series",
        title: "Audit Probe",
        legend: [{ label: "Portfolio", role: "primary" }],
      },
      createElement("div", null, "Body"),
    ),
  );

  expect(html).toContain('data-chart-family="Analytical Time Series"');
  expect(html).toContain('data-chart-renderer="svg"');
  expect(html).toContain("Portfolio");
  expect(html).toContain("--signal-core");
});

test("[runtime] CriHistoryChart renders the passed title in the shared chart shell and shared tooltip classes", () => {
  expect(criHistorySource).toContain("<ChartPanel");
  expect(criHistorySource).toContain("title={title}");
  expect(criHistorySource).toContain('className="chart-tooltip"');
  expect(criHistorySource).not.toContain("const legendY = -6");
});

test("[runtime] RegimeRelationshipView uses the shared chart shell and legend primitives", () => {
  expect(relationshipSource).toContain("import ChartPanel");
  expect(relationshipSource).toContain("import ChartLegend");
  expect(relationshipSource).toContain("<ChartPanel");
  expect(relationshipSource).toContain("<ChartLegend");
});

test("[runtime] RegimePanel uses semantic chart-series roles instead of hardcoded history chart hex colors", () => {
  expect(regimeSource).toContain('chartSeriesColor("primary")');
  expect(regimeSource).toContain('chartSeriesColor("extreme")');
  expect(regimeSource).toContain('chartSeriesColor("caution")');
  expect(regimeSource).toContain('chartSeriesColor("dislocation")');
});

test("[runtime] RegimeRelationshipView uses the shared analytical shell metadata and shared legend primitive", () => {
  const html = renderToStaticMarkup(
    createElement(RegimeRelationshipView, {
      history: relationshipHistory,
    }),
  );

  expect(html).toContain('data-testid="regime-relationship-view"');
  expect(html).toContain('data-chart-family="Analytical Time Series"');
  expect(html).toContain('data-chart-renderer="svg"');
  expect(html).toContain("RVOL z-score");
  expect(html).toContain("COR1M z-score");
  expect(html).toContain("chart-legend");
  expect(html).toContain("regime-relationship-shared-legend");
});
