/**
 * @vitest-environment jsdom
 *
 * Headline text wrapped in fullwidth brackets 【…】 is the wire's lede
 * marker. The tape promotes a LEADING lede to a headline line (brackets
 * stripped) above the body, keeps any mid-body 【…】 span bold, and
 * renders time / Important / impact as a mono meta line above the copy.
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import HeadlinesTape from "../components/dashboard/HeadlinesTape";
import type { Headline, HeadlineImpact } from "../lib/useHeadlines";

function headline(
  content: string,
  overrides: Partial<Pick<Headline, "important" | "impact" | "time">> = {},
): Headline {
  return {
    kind: "headline",
    id: "h1",
    time: null,
    important: false,
    content,
    impact: [],
    ...overrides,
  };
}

function row() {
  return screen.getByTestId("headlines-tape-row");
}

afterEach(cleanup);

describe("HeadlinesTape lede promotion", () => {
  it("promotes a leading 【…】 to a headline with brackets stripped and body below", () => {
    render(
      <HeadlinesTape
        items={[headline("【China coast guard patrols Huangyan Island】On Aug. 30 the coast guard patrolled.")]}
        status="live"
      />,
    );
    const title = row().querySelector(".headlines-tape__headline");
    expect(title?.textContent).toBe("China coast guard patrols Huangyan Island");
    const body = row().querySelector(".headlines-tape__body");
    expect(body?.textContent).toBe("On Aug. 30 the coast guard patrolled.");
    expect(body?.classList.contains("headlines-tape__body--solo")).toBe(false);
  });

  it("keeps a mid-body 【…】 span bold after the leading lede is promoted", () => {
    render(
      <HeadlinesTape
        items={[headline("【lede】body text 【second】 tail")]}
        status="live"
      />,
    );
    const strongs = screen.getAllByRole("strong");
    expect(strongs.map((s) => s.textContent)).toEqual(["【second】"]);
    expect(row().querySelector(".headlines-tape__body")?.textContent).toBe("body text 【second】 tail");
  });

  it("renders a bracketless headline as a solo body with no headline element", () => {
    render(<HeadlinesTape items={[headline("NHC says Karina is expected to become a hurricane.")]} status="live" />);
    expect(row().querySelector(".headlines-tape__headline")).toBeNull();
    const body = row().querySelector(".headlines-tape__body");
    expect(body?.textContent).toBe("NHC says Karina is expected to become a hurricane.");
    expect(body?.classList.contains("headlines-tape__body--solo")).toBe(true);
  });

  it("does not promote or bold an unclosed bracket", () => {
    render(<HeadlinesTape items={[headline("【dangling lede without close")]} status="live" />);
    expect(row().querySelector(".headlines-tape__headline")).toBeNull();
    expect(screen.queryAllByRole("strong")).toHaveLength(0);
    expect(row().querySelector(".headlines-tape__body")?.textContent).toBe("【dangling lede without close");
  });

  it("renders time, Important and impact in the meta line above the copy", () => {
    const impact: HeadlineImpact[] = [{ symbol: "CL", impact: "bullish" }];
    render(
      <HeadlinesTape
        items={[headline("【lede】body", { important: true, impact, time: "2026-08-30T03:02:02Z" })]}
        status="live"
      />,
    );
    const meta = row().querySelector(".headlines-tape__meta");
    expect(meta?.querySelector("time")?.textContent).toBe("23:02:02");
    expect(meta?.querySelector(".headlines-tape__imp")?.textContent).toBe("Important");
    const hit = meta?.querySelector(".headlines-tape__hit");
    expect(hit?.textContent).toBe("▲ CL bullish");
    expect(hit?.classList.contains("headlines-tape__hit--up")).toBe(true);
    expect(row().getAttribute("data-important")).toBe("true");
  });

  it("renders a bearish impact with a down arrow and no up modifier", () => {
    render(
      <HeadlinesTape
        items={[headline("body", { impact: [{ symbol: "FXI", impact: "bearish" }] })]}
        status="live"
      />,
    );
    const hit = row().querySelector(".headlines-tape__hit");
    expect(hit?.textContent).toBe("▼ FXI bearish");
    expect(hit?.classList.contains("headlines-tape__hit--up")).toBe(false);
  });
});
