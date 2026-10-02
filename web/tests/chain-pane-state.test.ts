/** @vitest-environment jsdom */
import { describe, expect, it } from "vitest";
import { readChainPaneState } from "../e2e/fixtures/chainPaneState";

describe("anchored-chain browser state measurement", () => {
  it.each(["desktop", "mobile"])("retains %s strikes and geometry after presentational classes change", (layout) => {
    const pane = document.createElement("div");
    const marker = layout === "desktop" ? 'data-strike="110"' : 'data-testid="mobile-chain-row-110"';
    pane.innerHTML = `<div ${marker} class="renamed-row"><span class="renamed-strike">$110.00</span></div>`;
    pane.scrollTop = 120;
    pane.getBoundingClientRect = () => ({ top: 10, bottom: 110 }) as DOMRect;
    const row = pane.firstElementChild!;
    row.getBoundingClientRect = () => ({ top: 30, bottom: 50 }) as DOMRect;
    expect(readChainPaneState(pane)).toEqual({
      scrollTop: 120, partition: ["110"], visible: [{ strike: "110", offset: 20 }],
    });
    // A real reflow must remain observable even with the same strike labels.
    row.getBoundingClientRect = () => ({ top: 45, bottom: 65 }) as DOMRect;
    expect(readChainPaneState(pane).visible).toEqual([{ strike: "110", offset: 35 }]);
    row.getBoundingClientRect = () => ({ top: 100, bottom: 120 }) as DOMRect;
    expect(readChainPaneState(pane).visible).toEqual([]);
    expect(readChainPaneState(pane).partition).toEqual(["110"]);
  });
});
