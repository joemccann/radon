"use client";

import { useEffect, useRef } from "react";
import * as d3 from "d3";

/**
 * Reusable range-brush minimap: a context sparkline of the full series with a
 * draggable window + two resize handles, mapping pointer drags to inclusive
 * [start, end] indices. Hand-built pointer events (no d3.brushX) — the same
 * machinery the CRI "Correlation Risk Premium" chart pioneered, lifted out so
 * any history chart (VCG, future regime variants) gets the identical zoom UX.
 *
 * Data-agnostic: it only needs the series values (for the context line) and the
 * current [start, end] range; the parent owns range/preset state.
 */

type BrushDragMode = "left" | "right" | "window";

interface BrushDragState {
  mode: BrushDragMode;
  pointerId: number;
  originX: number;
  originStart: number;
  originEnd: number;
}

const HANDLE_WIDTH = 8;
const CONTEXT_VBW = 1000; // context viewBox width; preserveAspectRatio="none" stretches it

export interface BrushMinimapProps {
  /** One value per session over the FULL history — drives the context sparkline. */
  values: Array<number | null>;
  /** Inclusive [start, end] indices into `values` currently visible in the chart. */
  range: [number, number];
  /** Fired with the new [start, end] as the user drags. */
  onRangeChange: (range: [number, number]) => void;
  /** Fired on any drag so the parent can flag the active range as "custom". */
  onCustom?: () => void;
  height?: number;
  /** Prefix for data-testid attrs (default "brush-minimap"). */
  testIdPrefix?: string;
  ariaLabel?: string;
}

export default function BrushMinimap({
  values,
  range,
  onRangeChange,
  onCustom,
  height = 40,
  testIdPrefix = "brush-minimap",
  ariaLabel = "Range brush minimap",
}: BrushMinimapProps) {
  const brushRef = useRef<HTMLDivElement>(null);
  const dragStateRef = useRef<BrushDragState | null>(null);
  // Latest values read by the global listeners (stable across re-renders).
  const rangeRef = useRef(range);
  rangeRef.current = range;
  const onRangeChangeRef = useRef(onRangeChange);
  onRangeChangeRef.current = onRangeChange;
  const onCustomRef = useRef(onCustom);
  onCustomRef.current = onCustom;

  const total = values.length;

  useEffect(() => {
    function indexFromClientX(clientX: number): number {
      const rect = brushRef.current?.getBoundingClientRect();
      if (!rect || rect.width <= 0) return 0;
      const ratio = (clientX - rect.left) / rect.width;
      const clamped = Math.max(0, Math.min(1, ratio));
      return Math.round(clamped * Math.max(total - 1, 0));
    }

    function handleMove(event: PointerEvent) {
      const drag = dragStateRef.current;
      if (!drag || drag.pointerId !== event.pointerId) return;
      if (total === 0) return;
      const maxIdx = total - 1;
      const idx = indexFromClientX(event.clientX);

      let nextStart = drag.originStart;
      let nextEnd = drag.originEnd;
      if (drag.mode === "left") {
        nextStart = Math.max(0, Math.min(idx, drag.originEnd - 1));
      } else if (drag.mode === "right") {
        nextEnd = Math.max(drag.originStart + 1, Math.min(idx, maxIdx));
      } else {
        const originIdx = indexFromClientX(drag.originX);
        const deltaIdx = idx - originIdx;
        const windowSize = drag.originEnd - drag.originStart;
        nextStart = Math.max(0, Math.min(maxIdx - windowSize, drag.originStart + deltaIdx));
        nextEnd = nextStart + windowSize;
      }

      const prev = rangeRef.current;
      if (prev[0] !== nextStart || prev[1] !== nextEnd) {
        onRangeChangeRef.current([nextStart, nextEnd]);
      }
      onCustomRef.current?.();
    }

    function handleEnd(event: PointerEvent) {
      const drag = dragStateRef.current;
      if (!drag || drag.pointerId !== event.pointerId) return;
      dragStateRef.current = null;
    }

    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleEnd);
    window.addEventListener("pointercancel", handleEnd);
    return () => {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", handleEnd);
      window.removeEventListener("pointercancel", handleEnd);
    };
  }, [total]);

  function handlePointerDown(mode: BrushDragMode) {
    return (event: React.PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      try {
        (event.target as HTMLElement).setPointerCapture?.(event.pointerId);
      } catch {
        // jsdom may not implement setPointerCapture
      }
      dragStateRef.current = {
        mode,
        pointerId: event.pointerId,
        originX: event.clientX,
        originStart: rangeRef.current[0],
        originEnd: rangeRef.current[1],
      };
    };
  }

  if (total < 2) return null;

  const visibleStart = Math.max(0, Math.min(range[0], total - 1));
  const visibleEnd = Math.max(visibleStart, Math.min(range[1], total - 1));
  const totalSpan = Math.max(total - 1, 1);
  const leftPct = (visibleStart / totalSpan) * 100;
  const widthPct = Math.max(((visibleEnd - visibleStart) / totalSpan) * 100, 0.5);

  const finiteValues = values.filter((value): value is number => value != null && Number.isFinite(value));
  const max = Math.max(...finiteValues.map((value) => Math.abs(value)), 1);
  const contextPath =
    d3
      .line<number | null>()
      .defined((value) => value != null && Number.isFinite(value))
      .x((_v, index) => (index / Math.max(total - 1, 1)) * CONTEXT_VBW)
      .y((value) => height / 2 - ((value ?? 0) / max) * (height / 2 - 4))
      .curve(d3.curveMonotoneX)(values) ?? "";

  return (
    <div
      ref={brushRef}
      className="brush-minimap"
      data-testid={testIdPrefix}
      style={{ height: `${height}px` }}
      aria-label={ariaLabel}
      role="group"
    >
      <svg
        className="brush-minimap-context"
        viewBox={`0 0 ${CONTEXT_VBW} ${height}`}
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <path d={contextPath} className="brush-minimap-line" />
      </svg>

      <div
        className="brush-minimap-window"
        data-testid={`${testIdPrefix}-window`}
        style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
        onPointerDown={handlePointerDown("window")}
      />
      <div
        className="brush-minimap-handle brush-minimap-handle-left"
        data-testid={`${testIdPrefix}-handle-left`}
        style={{ left: `calc(${leftPct}% - ${HANDLE_WIDTH / 2}px)`, width: `${HANDLE_WIDTH}px` }}
        onPointerDown={handlePointerDown("left")}
        role="slider"
        aria-label="Start of visible range"
        aria-valuemin={0}
        aria-valuemax={total - 1}
        aria-valuenow={visibleStart}
        tabIndex={0}
      />
      <div
        className="brush-minimap-handle brush-minimap-handle-right"
        data-testid={`${testIdPrefix}-handle-right`}
        style={{ left: `calc(${leftPct + widthPct}% - ${HANDLE_WIDTH / 2}px)`, width: `${HANDLE_WIDTH}px` }}
        onPointerDown={handlePointerDown("right")}
        role="slider"
        aria-label="End of visible range"
        aria-valuemin={0}
        aria-valuemax={total - 1}
        aria-valuenow={visibleEnd}
        tabIndex={0}
      />
    </div>
  );
}
