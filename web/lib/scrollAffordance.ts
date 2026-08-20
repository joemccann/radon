export type ScrollAffordance = { left: boolean; right: boolean };

/** Sub-pixel scroll residue that still counts as "at the edge". */
const EDGE_EPSILON_PX = 2;

/**
 * Which edges of a horizontal scroller have off-screen content. Drives the
 * edge-fade affordance so overflow is visible without a scrollbar — a strip
 * whose last visible item ends flush at the viewport edge otherwise reads
 * as complete.
 */
export function computeScrollAffordance(
  scrollLeft: number,
  scrollWidth: number,
  clientWidth: number,
): ScrollAffordance {
  const maxScroll = scrollWidth - clientWidth;
  if (maxScroll <= EDGE_EPSILON_PX) return { left: false, right: false };
  return {
    left: scrollLeft > EDGE_EPSILON_PX,
    right: scrollLeft < maxScroll - EDGE_EPSILON_PX,
  };
}
