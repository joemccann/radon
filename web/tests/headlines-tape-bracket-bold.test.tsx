/**
 * @vitest-environment jsdom
 *
 * Headline text wrapped in fullwidth brackets 【…】 is the wire's lede
 * marker; the tape renders each such span as bold, brackets included,
 * and leaves the surrounding body as plain text.
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import HeadlinesTape from "../components/dashboard/HeadlinesTape";
import type { Headline } from "../lib/useHeadlines";

function headline(content: string): Headline {
  return { kind: "headline", id: "h1", time: null, important: false, content, impact: [] };
}

afterEach(cleanup);

describe("HeadlinesTape bracket lede", () => {
  it("bolds every 【…】 span and keeps the rest plain", () => {
    render(
      <HeadlinesTape
        items={[headline("【China coast guard patrols Huangyan Island】 On Aug. 30 the coast guard 【second】 tail")]}
        status="live"
      />,
    );
    const strongs = screen.getAllByRole("strong");
    expect(strongs.map((s) => s.textContent)).toEqual([
      "【China coast guard patrols Huangyan Island】",
      "【second】",
    ]);
    const body = screen.getByTestId("headlines-tape-row").querySelector(".headlines-tape__body");
    expect(body?.textContent).toBe(
      "【China coast guard patrols Huangyan Island】 On Aug. 30 the coast guard 【second】 tail",
    );
  });

  it("renders a headline without brackets as plain text with no strong", () => {
    render(<HeadlinesTape items={[headline("NHC says Karina is expected to become a hurricane.")]} status="live" />);
    expect(screen.queryAllByRole("strong")).toHaveLength(0);
    expect(screen.getByText("NHC says Karina is expected to become a hurricane.")).toBeTruthy();
  });

  it("does not bold an unclosed bracket", () => {
    render(<HeadlinesTape items={[headline("【dangling lede without close")]} status="live" />);
    expect(screen.queryAllByRole("strong")).toHaveLength(0);
  });
});
