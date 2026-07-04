"use client";

import { useState } from "react";

type Ratio = { date: string; buy_ratio: number | null };

const BAR_WIDTH = 5;
const BAR_PITCH = 7;
const MAX_HEIGHT = 24;

function barFill(ratio: number | null): string {
  if (ratio == null) return "var(--text-muted)";
  if (ratio >= 0.55) return "var(--positive)";
  if (ratio <= 0.45) return "var(--negative)";
  return "var(--warn)";
}

function captionFor(ratio: Ratio): string {
  const pct = ratio.buy_ratio == null ? "---" : `${Math.round(ratio.buy_ratio * 100)}%`;
  return `${ratio.date.slice(5)} · ${pct}`;
}

/**
 * Daily buy-ratio trend for mobile flow cards. Hover tooltips don't exist on
 * touch, so tapping a bar reveals that day's value in the caption instead.
 */
export default function MobileFlowSparkline({ ratios }: { ratios?: Ratio[] }) {
  const [picked, setPicked] = useState<Ratio | null>(null);
  if (!ratios || ratios.length === 0) return null;

  function pick(event: React.PointerEvent<SVGSVGElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const index = Math.max(
      0,
      Math.min(ratios!.length - 1, Math.floor((event.clientX - rect.left) / BAR_PITCH)),
    );
    setPicked(ratios![index]);
  }

  return (
    <div className="m-flow-spark" data-testid="mobile-flow-sparkline">
      <svg
        width={ratios.length * BAR_PITCH}
        height={MAX_HEIGHT + 2}
        role="img"
        aria-label="Daily buy ratio trend, tap a bar for the value"
        data-testid="mobile-flow-sparkline-svg"
        onPointerDown={pick}
        style={{ touchAction: "manipulation" }}
      >
        {ratios.map((d, i) => {
          const h = d.buy_ratio == null ? 2 : Math.max(2, Math.round(d.buy_ratio * MAX_HEIGHT));
          return (
            <rect
              key={i}
              x={i * BAR_PITCH}
              y={MAX_HEIGHT + 2 - h}
              width={BAR_WIDTH}
              height={h}
              rx={1}
              fill={barFill(d.buy_ratio)}
            />
          );
        })}
      </svg>
      <span className="m-flow-spark__caption" data-testid="mobile-flow-sparkline-caption">
        {picked ? captionFor(picked) : "5D BUY RATIO"}
      </span>
    </div>
  );
}
