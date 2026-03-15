/** Shared theme constants for OG image rendering (Satori).
 *  Uses literal fallbacks from the chart-system spec because OG surfaces cannot resolve CSS variables. */

import chartSystemSpec from "./chart-system-spec.json";
import type { ChartFamily, ChartSeriesRole, SanctionedRenderer } from "./chartSystem";

const BRAND = {
  bg: "#0a0f14",
  panel: "#0f1519",
  panelRaised: "#151c22",
  plot: "#10171d",
  border: "#1e293b",
  text: "#e2e8f0",
  muted: "#94a3b8",
  faint: "#475569",
} as const;

function clampAlpha(alpha: number): number {
  return Math.min(1, Math.max(0, alpha));
}

function hexToRgb(hex: string): [number, number, number] {
  const normalized = hex.replace("#", "");
  const value = normalized.length === 3
    ? normalized
      .split("")
      .map((part) => part + part)
      .join("")
    : normalized;

  const parsed = Number.parseInt(value, 16);
  return [
    (parsed >> 16) & 0xff,
    (parsed >> 8) & 0xff,
    parsed & 0xff,
  ];
}

export function rgba(hex: string, alpha: number): string {
  const [r, g, b] = hexToRgb(hex);
  return `rgba(${r}, ${g}, ${b}, ${clampAlpha(alpha)})`;
}

export function ogSeriesColor(role: ChartSeriesRole): string {
  return chartSystemSpec.seriesRoles[role].fallback;
}

export function ogSeriesFill(role: ChartSeriesRole, alpha = 0.18): string {
  return rgba(ogSeriesColor(role), alpha);
}

export function ogRendererDescription(renderer: SanctionedRenderer): string {
  return chartSystemSpec.sanctionedRenderers[renderer];
}

export function ogFamilyContract(family: ChartFamily) {
  const familySpec = chartSystemSpec.families[family];
  return {
    id: family,
    label: familySpec.label,
    renderer: familySpec.renderer,
    interaction: familySpec.interaction,
    requiresAxes: familySpec.requiresAxes,
    rendererDescription: ogRendererDescription(familySpec.renderer as SanctionedRenderer),
  };
}

export function ogHeatmapColor(value: number, min: number, max: number): string {
  if (!Number.isFinite(value) || !Number.isFinite(min) || !Number.isFinite(max)) {
    return BRAND.panel;
  }

  if (min === max) {
    return ogSeriesFill("neutral", 0.16);
  }

  const midpoint = min + (max - min) / 2;

  if (value === midpoint) {
    return ogSeriesFill("neutral", 0.12);
  }

  if (value > midpoint) {
    const span = max - midpoint || 1;
    const intensity = 0.12 + ((value - midpoint) / span) * 0.26;
    return ogSeriesFill("primary", intensity);
  }

  const span = midpoint - min || 1;
  const intensity = 0.12 + ((midpoint - value) / span) * 0.26;
  return ogSeriesFill("fault", intensity);
}

export const OG = {
  bg: BRAND.bg,
  panel: BRAND.panel,
  panelRaised: BRAND.panelRaised,
  plot: BRAND.plot,
  border: BRAND.border,
  text: BRAND.text,
  muted: BRAND.muted,
  faint: BRAND.faint,
  positive: ogSeriesColor("primary"),
  negative: ogSeriesColor("fault"),
  warning: ogSeriesColor("caution"),
  info: ogSeriesColor("primary"),
  neutral: ogSeriesColor("neutral"),
  comparison: ogSeriesColor("comparison"),
  dislocation: ogSeriesColor("dislocation"),
  extreme: ogSeriesColor("extreme"),
  chart: {
    radius: chartSystemSpec.surface.radiusPx,
    padding: chartSystemSpec.surface.paddingPx,
    headerHeight: chartSystemSpec.surface.headerHeightPx,
    axisFontFamily: chartSystemSpec.axis.fontFamily,
    axisFontSize: chartSystemSpec.axis.fontSizePx,
    axisTracking: chartSystemSpec.axis.trackingEm,
    plotBackground: BRAND.plot,
    grid: rgba(BRAND.border, 0.96),
    axis: rgba(BRAND.muted, 0.64),
    axisLabel: BRAND.muted,
    tooltipBackground: BRAND.panelRaised,
    tooltipBorder: BRAND.border,
  },
} as const;

export function posColor(v: number): string {
  if (v > 0) return OG.positive;
  if (v < 0) return OG.negative;
  return OG.text;
}

export function pctileBg(v: number): string {
  if (v <= 10) return ogSeriesFill("fault", 0.25);
  if (v <= 25) return ogSeriesFill("fault", 0.12);
  if (v <= 40) return ogSeriesFill("caution", 0.12);
  if (v >= 75) return ogSeriesFill("primary", 0.25);
  if (v >= 60) return ogSeriesFill("primary", 0.12);
  return "transparent";
}

export function zColor(z: number): string {
  if (z > 0) return OG.positive;
  if (z < 0) return OG.negative;
  return OG.text;
}

export function zOpacity(z: number): number {
  const abs = Math.abs(z);
  if (abs >= 2) return 1;
  if (abs >= 1) return 0.85;
  if (abs >= 0.5) return 0.7;
  return 0.55;
}

export function fmt(v: number | null | undefined, decimals = 2): string {
  if (v == null || !Number.isFinite(v)) return "---";
  return v.toFixed(decimals);
}
