/**
 * PNG export for the options-exposure ladder (/options/net-gex).
 *
 * Drawn to a canvas rather than rasterised from the DOM so the export needs no
 * new dependency and stays deterministic. Split in two so the logic is
 * testable without a canvas implementation: `buildExposureChartModel` is pure,
 * `drawExposureChart` only paints a model onto a 2D context.
 *
 * The image is a record of what the operator was looking at, so the header
 * carries the full control state (metric, strike range, frequency, visible
 * control levels, selected expiration). A chart shared without its settings
 * cannot be read back a week later.
 */

import {
  EXPOSURE_LEVEL_OPTIONS,
  EXPOSURE_STRIKE_OPTIONS,
  exposureMetricLabel,
  isAbsoluteExposureMetric,
  nearestStrike,
  type OptionsExposureFrequency,
  type OptionsExposureLevelKey,
  type OptionsExposureMetric,
  type OptionsExposureRow,
  type OptionsExposureStrikeWindow,
} from "@/lib/optionsExposure";

export const EXPORT_WIDTH = 1200;
const ROW_HEIGHT = 26;
const HEADER_HEIGHT = 132;
const TABLE_HEADER_HEIGHT = 30;
const FOOTER_HEIGHT = 44;
const PAD_X = 40;

/** Brand tokens, resolved to literals: a canvas cannot read CSS variables. */
const COLOR = {
  canvas: "#0a0f14",
  panel: "#0f1519",
  grid: "#1e293b",
  textPrimary: "#e2e8f0",
  textSecondary: "#94a3b8",
  textMuted: "#475569",
  signal: "#05AD98",
  signalStrong: "#0FCFB5",
  fault: "#E85D6C",
  warn: "#F5A623",
} as const;

export type ExposureChartRow = {
  strike: number;
  value: number;
  valueLabel: string;
  putOi: number;
  callOi: number;
  /** 0..1 of the half-track, relative to the largest absolute value shown. */
  barFraction: number;
  putFraction: number;
  callFraction: number;
  sign: "positive" | "negative" | "zero";
  isSpot: boolean;
};

export type ExposureChartModel = {
  title: string;
  subtitle: string;
  spotLabel: string;
  settingsLine: string;
  valueHeader: string;
  capturedAt: string;
  symbol: string;
  metric: OptionsExposureMetric;
  rows: ExposureChartRow[];
  width: number;
  height: number;
  absoluteMetric: boolean;
};

export type ExposureChartInput = {
  symbol: string;
  spot: number;
  sourceTime: string;
  metric: OptionsExposureMetric;
  strikeWindow: OptionsExposureStrikeWindow;
  frequency: OptionsExposureFrequency;
  visibleLevels: OptionsExposureLevelKey[];
  expirationLabel: string;
  rows: OptionsExposureRow[];
  complete: boolean;
};

function compact(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1e9) return `${(value / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(value / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${(value / 1e3).toFixed(1)}K`;
  return value.toFixed(2);
}

function formatValue(value: number, metric: OptionsExposureMetric): string {
  if (value === 0) return "$0";
  const sign = value > 0 ? "+" : "-";
  const body = compact(Math.abs(value));
  return isAbsoluteExposureMetric(metric) ? `${body}` : `${sign}$${body}`;
}

function formatStrike(value: number): string {
  return value.toFixed(2);
}

function strikeWindowLabel(w: OptionsExposureStrikeWindow): string {
  return EXPOSURE_STRIKE_OPTIONS.find((o) => o.value === w)?.label ?? String(w);
}

export function buildExposureChartModel(input: ExposureChartInput): ExposureChartModel {
  const {
    symbol, spot, sourceTime, metric, strikeWindow, frequency,
    visibleLevels, expirationLabel, rows, complete,
  } = input;

  const maxAbs = rows.reduce((m, r) => Math.max(m, Math.abs(r.value)), 0);
  const maxOi = rows.reduce((m, r) => Math.max(m, r.putOi, r.callOi), 0);
  // Same helper the panel highlights with, so the exported SPOT row can never
  // drift from the one on screen (including its tie-break on equidistance).
  const spotStrike = nearestStrike(rows.map((r) => r.strike), spot);

  const chartRows: ExposureChartRow[] = rows.map((r) => ({
    strike: r.strike,
    value: r.value,
    valueLabel: formatValue(r.value, metric),
    putOi: r.putOi,
    callOi: r.callOi,
    // Guard the all-zero case: dividing by a zero max yields NaN and paints nothing.
    barFraction: maxAbs > 0 ? Math.abs(r.value) / maxAbs : 0,
    putFraction: maxOi > 0 ? r.putOi / maxOi : 0,
    callFraction: maxOi > 0 ? r.callOi / maxOi : 0,
    sign: r.value > 0 ? "positive" : r.value < 0 ? "negative" : "zero",
    isSpot: spotStrike != null && r.strike === spotStrike,
  }));

  const levelCount = visibleLevels.length;
  const settingsParts = [
    strikeWindowLabel(strikeWindow),
    frequency === "eod" ? "EOD" : "Intraday",
    expirationLabel,
    `Levels ${levelCount}/${EXPOSURE_LEVEL_OPTIONS.length}`,
  ];
  if (!complete) settingsParts.push("PARTIAL MEASUREMENT");

  const metricLabel = exposureMetricLabel(metric);
  const capturedAt = sourceTime.slice(0, 10);

  return {
    title: symbol.toUpperCase(),
    subtitle: `Options exposure · ${metricLabel}`,
    spotLabel: `SPOT ${formatStrike(spot)}`,
    settingsLine: settingsParts.join("  ·  "),
    valueHeader: metricLabel,
    capturedAt,
    symbol: symbol.toUpperCase(),
    metric,
    rows: chartRows,
    width: EXPORT_WIDTH,
    height: HEADER_HEIGHT + TABLE_HEADER_HEIGHT + chartRows.length * ROW_HEIGHT + FOOTER_HEIGHT,
    absoluteMetric: isAbsoluteExposureMetric(metric),
  };
}

export function exposureChartFilename(model: ExposureChartModel): string {
  return `radon-${model.symbol}-${model.metric}-${model.capturedAt}.png`;
}

export function drawExposureChart(ctx: CanvasRenderingContext2D, model: ExposureChartModel): void {
  const { width, height, rows } = model;

  // Opaque background: a transparent PNG renders black-on-black in most clients.
  ctx.fillStyle = COLOR.canvas;
  ctx.fillRect(0, 0, width, height);

  // ── header ──
  ctx.fillStyle = COLOR.textMuted;
  ctx.font = "600 12px 'IBM Plex Mono', ui-monospace, monospace";
  ctx.textBaseline = "top";
  ctx.textAlign = "left";
  ctx.fillText("OPTIONS / EXPOSURE", PAD_X, 28);

  ctx.fillStyle = COLOR.textPrimary;
  ctx.font = "600 30px 'IBM Plex Sans', system-ui, sans-serif";
  ctx.fillText(model.title, PAD_X, 48);

  ctx.fillStyle = COLOR.textSecondary;
  ctx.font = "400 15px 'IBM Plex Sans', system-ui, sans-serif";
  ctx.fillText(model.subtitle, PAD_X + ctx.measureText(model.title).width + 90, 56);

  ctx.textAlign = "right";
  ctx.fillStyle = COLOR.textSecondary;
  ctx.font = "500 14px 'IBM Plex Mono', ui-monospace, monospace";
  ctx.fillText(model.spotLabel, width - PAD_X, 56);
  ctx.textAlign = "left";

  ctx.fillStyle = COLOR.textMuted;
  ctx.font = "400 12px 'IBM Plex Mono', ui-monospace, monospace";
  ctx.fillText(model.settingsLine, PAD_X, 92);

  ctx.fillStyle = COLOR.grid;
  ctx.fillRect(PAD_X, HEADER_HEIGHT - 14, width - PAD_X * 2, 1);

  // ── column headers ──
  // The value gets its OWN column rather than sitting on the track: drawn over
  // the bar it was half-occluded, and a teal label on a teal bar is unreadable.
  const strikeX = PAD_X;
  const valueRightX = PAD_X + 260;
  const trackX = valueRightX + 24;
  const trackW = width - PAD_X - trackX - 240;
  const oiX = trackX + trackW + 40;

  let y = HEADER_HEIGHT;
  ctx.fillStyle = COLOR.textMuted;
  ctx.font = "600 11px 'IBM Plex Mono', ui-monospace, monospace";
  ctx.fillText("STRIKE", strikeX, y + 8);
  ctx.textAlign = "right";
  ctx.fillText(model.valueHeader.toUpperCase(), valueRightX, y + 8);
  ctx.fillText("PUT OI / CALL OI", width - PAD_X, y + 8);
  ctx.textAlign = "left";

  y += TABLE_HEADER_HEIGHT;
  ctx.fillStyle = COLOR.grid;
  ctx.fillRect(PAD_X, y - 6, width - PAD_X * 2, 1);

  // ── rows ──
  const centerX = trackX + trackW / 2;
  for (const row of rows) {
    if (row.isSpot) {
      ctx.fillStyle = COLOR.panel;
      ctx.fillRect(PAD_X - 8, y - 3, width - PAD_X * 2 + 16, ROW_HEIGHT - 2);
    }

    ctx.fillStyle = row.isSpot ? COLOR.textPrimary : COLOR.textSecondary;
    ctx.font = `${row.isSpot ? "600" : "400"} 13px 'IBM Plex Mono', ui-monospace, monospace`;
    ctx.textAlign = "left";
    ctx.fillText(formatStrike(row.strike), strikeX, y + 3);
    if (row.isSpot) {
      ctx.fillStyle = COLOR.signal;
      ctx.font = "600 9px 'IBM Plex Mono', ui-monospace, monospace";
      ctx.fillText("SPOT", strikeX + 78, y + 5);
    }

    // value, right-aligned in its own column
    ctx.fillStyle = row.sign === "negative"
      ? COLOR.fault
      : row.sign === "positive" ? COLOR.signalStrong : COLOR.textMuted;
    ctx.font = "500 12px 'IBM Plex Mono', ui-monospace, monospace";
    ctx.textAlign = "right";
    ctx.fillText(row.valueLabel, valueRightX, y + 3);

    // zero line
    ctx.fillStyle = COLOR.grid;
    ctx.fillRect(centerX, y, 1, ROW_HEIGHT - 6);

    // exposure bar, growing from centre
    if (row.barFraction > 0) {
      const barW = Math.max(1, row.barFraction * (trackW / 2));
      ctx.fillStyle = row.sign === "negative" ? COLOR.fault : COLOR.signal;
      const barX = row.sign === "negative" ? centerX - barW : centerX;
      ctx.fillRect(barX, y + 2, barW, ROW_HEIGHT - 10);
    }

    // OI pair
    ctx.textAlign = "right";
    ctx.fillStyle = COLOR.fault;
    ctx.font = "400 11px 'IBM Plex Mono', ui-monospace, monospace";
    ctx.fillText(compact(row.putOi), oiX + 70, y + 4);
    ctx.textAlign = "left";
    ctx.fillStyle = COLOR.signal;
    ctx.fillText(compact(row.callOi), width - PAD_X - 46, y + 4);

    y += ROW_HEIGHT;
  }

  // ── footer ──
  ctx.fillStyle = COLOR.grid;
  ctx.fillRect(PAD_X, height - FOOTER_HEIGHT + 6, width - PAD_X * 2, 1);
  ctx.fillStyle = COLOR.signal;
  ctx.font = "600 12px 'IBM Plex Mono', ui-monospace, monospace";
  ctx.textAlign = "left";
  ctx.fillText("radon.run", PAD_X, height - FOOTER_HEIGHT + 18);
  ctx.fillStyle = COLOR.textMuted;
  ctx.font = "400 11px 'IBM Plex Mono', ui-monospace, monospace";
  ctx.textAlign = "right";
  ctx.fillText(`Captured ${model.capturedAt}`, width - PAD_X, height - FOOTER_HEIGHT + 19);
  ctx.textAlign = "left";
}

/** Renders the model to a PNG blob. Browser only. */
export async function exposureChartToBlob(model: ExposureChartModel): Promise<Blob> {
  const scale = 2; // retina-crisp on X and in Slack
  const canvas = document.createElement("canvas");
  canvas.width = model.width * scale;
  canvas.height = model.height * scale;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas 2D context unavailable");
  ctx.scale(scale, scale);
  drawExposureChart(ctx, model);
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
  if (!blob) throw new Error("PNG encoding failed");
  return blob;
}

/** Triggers the browser download. Browser only. */
export async function downloadExposureChart(model: ExposureChartModel): Promise<void> {
  const blob = await exposureChartToBlob(model);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = exposureChartFilename(model);
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
