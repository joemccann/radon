import { ImageResponse } from "next/og";
import { readdir, readFile } from "fs/promises";
import { join } from "path";
import type { ReactElement } from "react";
import { loadFonts } from "@/lib/og-fonts";
import { OG, ogFamilyContract, ogSeriesColor } from "@/lib/og-theme";
import type { ChartFamily } from "@/lib/chartSystem";
import { barChartSvg, heatmapSvg, lineChartSvg } from "@/lib/og-charts";
import { requireRouteAccess } from "@/lib/routeAccess";

export const runtime = "nodejs";
// A satori render holding the whole bitmap must not run unbounded (R-310).
export const maxDuration = 30;

const CACHE_DIR = join(process.cwd(), "..", "data", "menthorq_cache");

type DashboardRow = Record<string, unknown>;

export type DashboardCache = {
  date: string;
  command: string;
  title?: string;
  data?: DashboardRow[];
  metadata?: Record<string, unknown>;
};

async function loadLatestCache(
  command: string
): Promise<DashboardCache | null> {
  try {
    const files = await readdir(CACHE_DIR);
    const matching = files
      .filter((f) => f.startsWith(`${command}_`) && f.endsWith(".json"))
      .sort();
    if (matching.length === 0) return null;
    const raw = await readFile(
      join(CACHE_DIR, matching[matching.length - 1]),
      "utf-8"
    );
    return JSON.parse(raw) as DashboardCache;
  } catch {
    return null;
  }
}

/* ─── Renderers ────────────────────────────────────────── */

const WIDTH = 1200;
const CHART_W = 1160;
const CHART_H = 400;
const COMMAND_FAMILY_HINTS: Partial<Record<string, ChartFamily>> = {
  intraday: "analytical-time-series",
  cryptos_technical: "analytical-time-series",
  vol: "distribution-bar",
  eod: "distribution-bar",
  futures: "distribution-bar",
  cryptos_options: "distribution-bar",
  forex: "matrix-heatmap",
};
const FAMILY_FALLBACK_ORDER: ChartFamily[] = [
  "analytical-time-series",
  "distribution-bar",
  "matrix-heatmap",
];
const ANALYTICAL_LABEL_KEYS = [
  "date",
  "time",
  "timestamp",
  "datetime",
  "period",
  "session",
  "label",
];
const DISTRIBUTION_LABEL_KEYS = [
  "metric",
  "strike",
  "label",
  "bucket",
  "level",
  "expiry",
  "name",
  "ticker",
  "symbol",
];
const NUMERIC_VALUE_KEYS = [
  "value",
  "close",
  "last",
  "price",
  "level",
  "gex",
  "dex",
  "delta",
  "gamma",
  "exposure",
  "score",
];
const MATRIX_ROW_KEYS = [
  "row",
  "ticker",
  "symbol",
  "asset",
  "pair",
  "name",
];
const MATRIX_COL_KEYS = [
  "col",
  "metric",
  "bucket",
  "tenor",
  "expiry",
  "strike",
  "label",
  "signal",
];

type ChartRenderer = (props: { data: DashboardCache }) => ReactElement;
type RendererSelection = {
  family: ReturnType<typeof ogFamilyContract>;
  Renderer: ChartRenderer;
  componentName: string;
};

function isDashboardRow(value: unknown): value is DashboardRow {
  return value != null && typeof value === "object" && !Array.isArray(value);
}

function getRows(data: DashboardCache): DashboardRow[] {
  return (data.data ?? []).filter(isDashboardRow);
}

function asFiniteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asLabel(value: unknown): string | null {
  if (typeof value === "string" && value.trim().length > 0) return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return null;
}

function unique<T>(values: T[]): T[] {
  return Array.from(new Set(values));
}

function orderedKeys(rows: DashboardRow[], preferred: string[]): string[] {
  return unique([
    ...preferred,
    ...rows.flatMap((row) => Object.keys(row)),
  ]);
}

function pickLabelKey(
  rows: DashboardRow[],
  preferred: string[],
  exclude: string[] = [],
): string | null {
  return (
    orderedKeys(rows, preferred).find((key) => {
      if (exclude.includes(key)) return false;
      return rows.some((row) => asLabel(row[key]) != null);
    }) ?? null
  );
}

function pickNumericKey(
  rows: DashboardRow[],
  preferred: string[],
  exclude: string[] = [],
): string | null {
  return (
    orderedKeys(rows, preferred).find((key) => {
      if (exclude.includes(key)) return false;
      return rows.some((row) => asFiniteNumber(row[key]) != null);
    }) ?? null
  );
}

function isTimeLikeLabelKey(key: string, labels: string[]): boolean {
  const normalized = key.toLowerCase();
  if (
    ["date", "time", "timestamp", "datetime", "period", "session"].some(
      (token) => normalized.includes(token),
    )
  ) {
    return true;
  }

  return labels.some(
    (label) =>
      /^\d{4}-\d{2}-\d{2}/.test(label) ||
      /^\d{1,2}:\d{2}/.test(label) ||
      /\b(?:am|pm)\b/i.test(label),
  );
}

function extractAnalyticalTimeSeries(
  data: DashboardCache,
): { label: string; value: number }[] | null {
  const rows = getRows(data);
  if (rows.length === 0) return null;

  const labelKey = pickLabelKey(rows, ANALYTICAL_LABEL_KEYS);
  const valueKey = pickNumericKey(rows, NUMERIC_VALUE_KEYS, labelKey ? [labelKey] : []);
  if (!labelKey || !valueKey) return null;

  const series = rows
    .map((row) => {
      const label = asLabel(row[labelKey]);
      const value = asFiniteNumber(row[valueKey]);
      if (label == null || value == null) return null;
      return { label, value };
    })
    .filter((point): point is { label: string; value: number } => point != null);

  if (series.length < 2) return null;
  if (!isTimeLikeLabelKey(labelKey, series.map((point) => point.label))) {
    return null;
  }

  return series;
}

function extractDistributionBars(
  data: DashboardCache,
): { label: string; value: number }[] | null {
  const rows = getRows(data);
  if (rows.length === 0) return null;

  const labelKey = pickLabelKey(rows, DISTRIBUTION_LABEL_KEYS);
  const valueKey = pickNumericKey(rows, NUMERIC_VALUE_KEYS, labelKey ? [labelKey] : []);
  if (!labelKey || !valueKey) return null;

  const bars = rows
    .map((row) => {
      const label = asLabel(row[labelKey]);
      const value = asFiniteNumber(row[valueKey]);
      if (label == null || value == null) return null;
      return { label, value };
    })
    .filter((bar): bar is { label: string; value: number } => bar != null);

  return bars.length > 0 ? bars : null;
}

function extractMatrixHeatmap(data: DashboardCache): {
  data: { row: string; col: string; value: number }[];
  rows: string[];
  cols: string[];
} | null {
  const rows = getRows(data);
  if (rows.length < 4) return null;

  const directCells = rows
    .map((row) => {
      const rowLabel = asLabel(row.row);
      const colLabel = asLabel(row.col);
      const value = asFiniteNumber(row.value);
      if (rowLabel == null || colLabel == null || value == null) return null;
      return { row: rowLabel, col: colLabel, value };
    })
    .filter(
      (cell): cell is { row: string; col: string; value: number } => cell != null,
    );

  const directRowValues = unique(directCells.map((cell) => cell.row));
  const directColValues = unique(directCells.map((cell) => cell.col));
  if (directCells.length > 0 && directRowValues.length >= 2 && directColValues.length >= 2) {
    return {
      data: directCells,
      rows: directRowValues,
      cols: directColValues,
    };
  }

  const rowKey = pickLabelKey(rows, MATRIX_ROW_KEYS);
  const colKey = pickLabelKey(rows, MATRIX_COL_KEYS, rowKey ? [rowKey] : []);
  const valueKey = pickNumericKey(
    rows,
    NUMERIC_VALUE_KEYS,
    [rowKey, colKey].filter(Boolean) as string[],
  );
  if (!rowKey || !colKey || !valueKey) return null;

  const cells = rows
    .map((row) => {
      const rowLabel = asLabel(row[rowKey]);
      const colLabel = asLabel(row[colKey]);
      const value = asFiniteNumber(row[valueKey]);
      if (rowLabel == null || colLabel == null || value == null) return null;
      return { row: rowLabel, col: colLabel, value };
    })
    .filter(
      (cell): cell is { row: string; col: string; value: number } => cell != null,
    );
  const rowValues = unique(cells.map((cell) => cell.row));
  const colValues = unique(cells.map((cell) => cell.col));
  if (cells.length === 0 || rowValues.length < 2 || colValues.length < 2) {
    return null;
  }

  return {
    data: cells,
    rows: rowValues,
    cols: colValues,
  };
}

function AnalyticalTimeSeriesChart({ data }: { data: DashboardCache }) {
  const series = extractAnalyticalTimeSeries(data);
  if (!series) return <UnsupportedChart data={data} />;

  return (
    <div style={{ display: "flex", flexDirection: "column", width: "100%" }}>
      {lineChartSvg({
        data: series,
        width: CHART_W,
        height: CHART_H,
        color: ogSeriesColor("primary"),
        marginLeft: 60,
      })}
    </div>
  );
}

function DistributionBarChart({ data }: { data: DashboardCache }) {
  const bars = extractDistributionBars(data);
  if (!bars) return <UnsupportedChart data={data} />;

  return (
    <div style={{ display: "flex", flexDirection: "column", width: "100%" }}>
      {barChartSvg({
        data: bars,
        width: CHART_W,
        height: CHART_H,
      })}
    </div>
  );
}

function MatrixHeatmapChart({ data }: { data: DashboardCache }) {
  const matrix = extractMatrixHeatmap(data);
  if (!matrix) return <UnsupportedChart data={data} />;

  return (
    <div style={{ display: "flex", flexDirection: "column", width: "100%" }}>
      {heatmapSvg({
        data: matrix.data,
        rows: matrix.rows,
        cols: matrix.cols,
        width: CHART_W,
        height: CHART_H,
      })}
    </div>
  );
}

function NoData({ command }: { command: string }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        width: "100%",
        height: "200px",
        color: OG.muted,
        fontSize: "13px",
      }}
    >
      No data for {command.toUpperCase()}. Run: python3.13 scripts/fetch_menthorq_dashboard.py --command {command}
    </div>
  );
}

function NoDataChart({ data }: { data: DashboardCache }) {
  return <NoData command={data.command} />;
}

function UnsupportedChart({ data }: { data: DashboardCache }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        width: "100%",
        height: "200px",
        color: OG.muted,
        fontSize: "13px",
        textAlign: "center",
        padding: "0 24px",
      }}
    >
      Unsupported data shape for {data.command.toUpperCase()}. Expected a time
      series, distribution bars, or a row/column matrix payload.
    </div>
  );
}

/* ─── Route ────────────────────────────────────────────── */

const FAMILY_RENDERERS: Record<
  ChartFamily,
  {
    componentName: string;
    Renderer: ChartRenderer;
    supports: (data: DashboardCache) => boolean;
  }
> = {
  "live-trace": {
    componentName: "UnsupportedChart",
    Renderer: UnsupportedChart,
    supports: () => false,
  },
  "analytical-time-series": {
    componentName: "AnalyticalTimeSeriesChart",
    Renderer: AnalyticalTimeSeriesChart,
    supports: (data) => extractAnalyticalTimeSeries(data) != null,
  },
  "distribution-bar": {
    componentName: "DistributionBarChart",
    Renderer: DistributionBarChart,
    supports: (data) => extractDistributionBars(data) != null,
  },
  "matrix-heatmap": {
    componentName: "MatrixHeatmapChart",
    Renderer: MatrixHeatmapChart,
    supports: (data) => extractMatrixHeatmap(data) != null,
  },
};

export function resolveMenthorqRenderer(
  command: string,
  data: DashboardCache,
): RendererSelection {
  const hintedFamily = COMMAND_FAMILY_HINTS[command];
  const candidates = unique<ChartFamily>(
    [
      hintedFamily,
      extractAnalyticalTimeSeries(data) ? "analytical-time-series" : null,
      extractMatrixHeatmap(data) ? "matrix-heatmap" : null,
      extractDistributionBars(data) ? "distribution-bar" : null,
      ...FAMILY_FALLBACK_ORDER,
    ].filter((family): family is ChartFamily => family != null),
  );

  for (const family of candidates) {
    const renderer = FAMILY_RENDERERS[family];
    if (renderer.supports(data)) {
      return {
        family: ogFamilyContract(family),
        Renderer: renderer.Renderer,
        componentName: renderer.componentName,
      };
    }
  }

  const fallbackFamily = hintedFamily ?? "distribution-bar";
  const hasRows = getRows(data).length > 0;
  return {
    family: ogFamilyContract(fallbackFamily),
    Renderer: hasRows ? UnsupportedChart : NoDataChart,
    componentName: hasRows ? "UnsupportedChart" : "NoData",
  };
}

export const radonCapability = "read";

export async function GET(
  request: Request,
  { params }: { params: Promise<{ command: string }> }
) {
  const access = await requireRouteAccess(request, {
    rate: { key: "menthorq/image", limit: 20, windowMs: 60_000 },
  });
  if (!access.ok) return access.response;
  const { command } = await params;

  // Don't match the CTA route (it has its own image handler)
  if (command === "cta") {
    return new Response("Use /api/menthorq/cta/image instead", {
      status: 308,
      headers: { Location: "/api/menthorq/cta/image" },
    });
  }

  const data = await loadLatestCache(command);
  if (!data) {
    return new Response(`No ${command} data available`, { status: 404 });
  }

  const fonts = await loadFonts();
  const selection = resolveMenthorqRenderer(command, data);
  const Renderer = selection.Renderer;

  return new ImageResponse(
    (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          width: "100%",
          height: "100%",
          background: OG.bg,
          fontFamily: OG.chart.axisFontFamily,
          color: OG.text,
          padding: `${OG.chart.padding}px`,
        }}
      >
        {/* Title bar */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: "16px",
            paddingBottom: "10px",
            minHeight: `${OG.chart.headerHeight}px`,
            borderBottom: `1px solid ${OG.border}`,
          }}
        >
          <div style={{ display: "flex", alignItems: "center" }}>
            <span
              style={{
                fontSize: "14px",
                fontWeight: 700,
                letterSpacing: "0.1em",
              }}
            >
              {command.toUpperCase()}
            </span>
            <span
              style={{
                fontSize: "11px",
                color: OG.muted,
                marginLeft: "12px",
              }}
            >
              {data.date ?? "---"}
            </span>
            {data.title && (
              <span
                style={{
                  fontSize: "11px",
                  color: OG.muted,
                  marginLeft: "12px",
                }}
              >
                {data.title}
              </span>
            )}
          </div>
          <div
            style={{
              display: "flex",
              gap: "8px",
              alignItems: "center",
            }}
          >
            <span
              style={{
                fontSize: "11px",
                color: OG.text,
                border: `1px solid ${OG.border}`,
                padding: "4px 8px",
              }}
            >
              {selection.family.label.toUpperCase()}
            </span>
            <span
              style={{
                fontSize: "11px",
                color: OG.muted,
                border: `1px solid ${OG.border}`,
                padding: "4px 8px",
              }}
            >
              {selection.family.renderer.toUpperCase()}
            </span>
          </div>
        </div>

        {/* Chart */}
        <Renderer data={data} />
      </div>
    ),
    {
      width: WIDTH,
      height: 500,
      fonts: fonts as any,
    }
  );
}
