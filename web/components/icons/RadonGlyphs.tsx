"use client";

/**
 * Radon glyph set — replaces lucide-react in the workspace nav (MOVE 7).
 * Each glyph is composed from the brand's geometric primitives — rings,
 * projection lines, spectra, lattices — so the nav reads as one cohesive
 * instrument language rather than a generic icon library.
 *
 * Conventions:
 *   - 24x24 viewBox, scaled via `size` prop (default 14 to match the
 *     previous lucide stroke weight in the sidebar).
 *   - 1.5px stroke via `currentColor` so callers can theme without
 *     touching the SVG. Sidebar passes `actionTone`.
 *   - All glyphs are decorative — `aria-hidden`; the nav label provides
 *     the accessible name.
 */

import type { ReactNode } from "react";

type GlyphProps = {
  size?: number;
  color?: string;
  strokeWidth?: number;
};

function Glyph({
  size = 14,
  color = "currentColor",
  strokeWidth = 1.5,
  children,
}: GlyphProps & { children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke={color}
      strokeWidth={strokeWidth}
      strokeLinecap="square"
      strokeLinejoin="miter"
      aria-hidden
      focusable={false}
    >
      {children}
    </svg>
  );
}

/* Dashboard — four-quadrant instrument readout (mirrors the brand
   terminal mockup's four-panel hero). */
export function DashboardGlyph(p: GlyphProps) {
  return (
    <Glyph {...p}>
      <rect x="3" y="3" width="8" height="8" />
      <rect x="13" y="3" width="8" height="8" />
      <rect x="3" y="13" width="8" height="8" />
      <rect x="13" y="13" width="8" height="8" />
    </Glyph>
  );
}

/* Portfolio — concentric scanning rings (the brand monogram, simplified). */
export function PortfolioGlyph(p: GlyphProps) {
  return (
    <Glyph {...p}>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="5" />
      <circle cx="12" cy="12" r="1.5" fill={p.color ?? "currentColor"} stroke="none" />
    </Glyph>
  );
}

/* Orders — three layered order traces with terminal arrows. */
export function OrdersGlyph(p: GlyphProps) {
  return (
    <Glyph {...p}>
      <path d="M3 6 L18 6" />
      <path d="M15 3 L18 6 L15 9" />
      <path d="M3 12 L21 12" />
      <path d="M18 9 L21 12 L18 15" />
      <path d="M3 18 L13 18" />
      <path d="M10 15 L13 18 L10 21" />
    </Glyph>
  );
}

/* Scanner — full radar arc with sweep line. Echoes brand kit's
   CircularScan motif. */
export function ScannerGlyph(p: GlyphProps) {
  return (
    <Glyph {...p}>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="5" />
      <path d="M12 12 L12 3" />
      <circle cx="12" cy="12" r="1.2" fill={p.color ?? "currentColor"} stroke="none" />
    </Glyph>
  );
}

/* Watchlist - saved instrument ledger with a fixed signal latch. */
export function WatchlistGlyph(p: GlyphProps) {
  return (
    <Glyph {...p}>
      <path d="M5 4 L19 4 L19 20 L12 16.5 L5 20 Z" />
      <path d="M8 8 L16 8" />
      <path d="M8 12 L14 12" />
      <circle cx="15.5" cy="15.5" r="1.4" fill={p.color ?? "currentColor"} stroke="none" />
    </Glyph>
  );
}

/* Discover — radar quadrant arc with a fixed detector dot. */
export function DiscoverGlyph(p: GlyphProps) {
  return (
    <Glyph {...p}>
      <path d="M21 12 A 9 9 0 0 0 12 3" />
      <path d="M18 12 A 6 6 0 0 0 12 6" />
      <path d="M15 12 A 3 3 0 0 0 12 9" />
      <circle cx="12" cy="12" r="1.4" fill={p.color ?? "currentColor"} stroke="none" />
      <path d="M12 12 L21 21" />
    </Glyph>
  );
}

/* Flow Analysis — projection geometry: two parallel diagonal traces
   with measurement nodes. */
export function FlowGlyph(p: GlyphProps) {
  return (
    <Glyph {...p}>
      <path d="M3 21 L21 3" />
      <path d="M3 16 L16 3" opacity="0.55" />
      <path d="M8 21 L21 8" opacity="0.55" />
      <circle cx="12" cy="12" r="1.4" fill={p.color ?? "currentColor"} stroke="none" />
      <circle cx="6" cy="18" r="1" fill={p.color ?? "currentColor"} stroke="none" />
      <circle cx="18" cy="6" r="1" fill={p.color ?? "currentColor"} stroke="none" />
    </Glyph>
  );
}

/* Journal — stacked sample ledger (rows of measurement entries). */
export function JournalGlyph(p: GlyphProps) {
  return (
    <Glyph {...p}>
      <rect x="4" y="3" width="16" height="18" />
      <path d="M8 7 L16 7" />
      <path d="M8 11 L16 11" />
      <path d="M8 15 L13 15" />
      <path d="M8 19 L11 19" />
    </Glyph>
  );
}

/* Regime — state lattice: four regime nodes connected by transitions. */
export function RegimeGlyph(p: GlyphProps) {
  return (
    <Glyph {...p}>
      <circle cx="5" cy="5" r="2" />
      <circle cx="19" cy="5" r="2" />
      <circle cx="5" cy="19" r="2" />
      <circle cx="19" cy="19" r="2" />
      <path d="M7 5 L17 5" />
      <path d="M7 19 L17 19" />
      <path d="M5 7 L5 17" />
      <path d="M19 7 L19 17" />
      <path d="M6.5 6.5 L17.5 17.5" opacity="0.55" />
    </Glyph>
  );
}

/* CTA — vol-targeting exposure curve plotted over a measurement axis. */
export function CTAGlyph(p: GlyphProps) {
  return (
    <Glyph {...p}>
      <path d="M3 21 L21 21" />
      <path d="M3 21 L3 3" />
      <path d="M3 19 L7 17 L11 13 L15 8 L21 4" />
      <circle cx="11" cy="13" r="1.2" fill={p.color ?? "currentColor"} stroke="none" />
    </Glyph>
  );
}

/* Operator — calibration crosshair with reference rings. */
export function OperatorGlyph(p: GlyphProps) {
  return (
    <Glyph {...p}>
      <circle cx="12" cy="12" r="8" />
      <circle cx="12" cy="12" r="4" />
      <path d="M12 1 L12 6" />
      <path d="M12 18 L12 23" />
      <path d="M1 12 L6 12" />
      <path d="M18 12 L23 12" />
      <circle cx="12" cy="12" r="1.2" fill={p.color ?? "currentColor"} stroke="none" />
    </Glyph>
  );
}

/* Profile — operator ring with a centred figure, matching the instrument
   language of the rest of the nav glyph set. */
export function ProfileGlyph(p: GlyphProps) {
  return (
    <Glyph {...p}>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="9.5" r="3" />
      <path d="M6.5 18.5 C7.5 15.5 10 14.5 12 14.5 C14 14.5 16.5 15.5 17.5 18.5" />
    </Glyph>
  );
}

/* Performance — spectral bars stacked vertically. Kept available for
   when the Performance route is unhidden in lib/data.ts. */
export function PerformanceGlyph(p: GlyphProps) {
  return (
    <Glyph {...p}>
      <path d="M3 21 L21 21" />
      <rect x="5" y="13" width="3" height="8" />
      <rect x="10.5" y="9" width="3" height="12" />
      <rect x="16" y="5" width="3" height="16" />
    </Glyph>
  );
}
