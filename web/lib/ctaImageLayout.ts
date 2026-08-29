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

/**
 * Truncate every table to a SHARED row budget, in place.
 *
 * Lives here rather than inline in the route so the behaviour is testable
 * without booting an ImageResponse. Two things it has to get right:
 *
 *  - a cache file carrying `"tables": {"main": null}` must render, not throw.
 *    The original read `data.tables[key].length` off the ORIGINAL (possibly
 *    null) value even though the line above had substituted []. R-376.
 *  - once the budget is exhausted the remaining tables truncate to EMPTY.
 *    A negative `budget` handed straight to `slice` drops rows off the END
 *    and keeps the rest, so the plate silently grows past MAX_IMAGE_ROWS.
 */
export function truncateCtaTables<T>(
  tables: Record<string, T[] | null | undefined>,
  max: number = MAX_IMAGE_ROWS,
): Record<string, T[]> {
  let budget = max;
  for (const key of Object.keys(tables)) {
    const rows = tables[key] ?? [];
    const kept = rows.slice(0, Math.max(0, budget));
    tables[key] = kept;
    budget -= kept.length;
  }
  return tables as Record<string, T[]>;
}
