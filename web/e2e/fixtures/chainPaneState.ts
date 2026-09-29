export function readChainPaneState(element: HTMLElement) {
  const viewport = element.getBoundingClientRect();
  const rows = Array.from(element.querySelectorAll("[data-strike], [data-testid^='mobile-chain-row-']"));
  const strike = (row: Element) => row.getAttribute("data-strike")
    ?? row.getAttribute("data-testid")!.slice("mobile-chain-row-".length);
  const visible = rows.filter((row) => {
    const bounds = row.getBoundingClientRect();
    return bounds.top >= viewport.top && bounds.bottom <= viewport.bottom;
  });
  return {
    scrollTop: element.scrollTop,
    partition: rows.map(strike),
    visible: visible.map((row) => ({
      strike: strike(row),
      offset: Math.round(row.getBoundingClientRect().top - viewport.top),
    })),
  };
}
