export const BREAKPOINTS = {
  mobile: 640,
  tablet: 1024,
} as const;

export type ViewportClass = "mobile" | "tablet" | "desktop";

export function classifyViewport(width: number): ViewportClass {
  if (width <= BREAKPOINTS.mobile) return "mobile";
  if (width < BREAKPOINTS.tablet) return "tablet";
  return "desktop";
}
