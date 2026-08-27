/**
 * Canvas sizing for the CTA share plate.
 *
 * The height was derived straight from the row count read out of a cache FILE,
 * with no cap on either input: `50 + sections*64 + rows*28 + 20`. A malformed
 * or oversized extraction therefore asked `ImageResponse` for a canvas tens of
 * thousands of pixels tall — 5,000 rows is a 140,000px plate — and the render
 * is a Node-runtime satori pass that has to hold the whole bitmap. R-310.
 *
 * Extracted from the route so the arithmetic is testable without booting an
 * ImageResponse.
 */

const TITLE_H = 50;
const SECTION_H = 36 + 28; // section header + table header
const ROW_H = 28;
const PADDING_H = 20;

/** Rows a single plate will lay out. Beyond this the plate is truncated. */
export const MAX_IMAGE_ROWS = 400;

/** Hard ceiling on the rendered canvas, whatever the arithmetic produces. */
export const MAX_IMAGE_HEIGHT = TITLE_H + 12 * SECTION_H + MAX_IMAGE_ROWS * ROW_H + PADDING_H;

function bounded(value: number, max: number): number {
  if (!Number.isFinite(value) || value < 0) return 0;
  return Math.min(Math.floor(value), max);
}

export function computeCtaImageHeight({
  sectionCount,
  totalRows,
}: {
  sectionCount: number;
  totalRows: number;
}): number {
  const sections = bounded(sectionCount, 12);
  const rows = bounded(totalRows, MAX_IMAGE_ROWS);
  const height = TITLE_H + sections * SECTION_H + rows * ROW_H + PADDING_H;
  return Math.max(1, Math.min(height, MAX_IMAGE_HEIGHT));
}
