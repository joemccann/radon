"use client";

import type { ChartLegendItem } from "@/lib/chartSystem";
import { chartSeriesColor } from "@/lib/chartSystem";

type ChartLegendProps = {
  items: ChartLegendItem[];
  className?: string;
};

export default function ChartLegend({ items, className }: ChartLegendProps) {
  if (items.length === 0) return null;

  return (
    <div className={`chart-legend ${className ?? ""}`.trim()}>
      {items.map((item) => (
        <span key={`${item.label}-${item.role ?? item.color ?? "custom"}`} className="chart-legend-item">
          <span
            className="chart-legend-swatch"
            style={{ background: item.color ?? chartSeriesColor(item.role ?? "primary") }}
          />
          {item.label}
        </span>
      ))}
    </div>
  );
}
