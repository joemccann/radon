import { expect } from "vitest";

/** Asserts a `.regime-strip-fit` wrapper declares column counts that divide
 *  its tile count exactly, so no grid row is left with an empty cell. */
export function expectStripFillsGrid(container: HTMLElement): void {
  const wrap = container.querySelector<HTMLElement>(".regime-strip-fit");
  expect(wrap, "panel strip must opt into .regime-strip-fit").toBeTruthy();
  const cells = wrap!.querySelectorAll(".regime-strip > .regime-strip-cell").length;
  const cols = Number(wrap!.style.getPropertyValue("--fit-cols"));
  const colsMd = Number(wrap!.style.getPropertyValue("--fit-cols-md"));
  expect(cells).toBeGreaterThan(0);
  expect(cols).toBeGreaterThan(0);
  expect(colsMd).toBeGreaterThan(0);
  expect(cells % cols).toBe(0);
  expect(cells % colsMd).toBe(0);
}
