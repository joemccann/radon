/**
 * @vitest-environment jsdom
 *
 * The yield chart does not show the rates move. This figure does: the
 * 10-year change on a basis-point axis from zero, and the October hike
 * odds on a 0-100 probability axis. Year-end tightening stays off that axis.
 * Both figures sit under the article body.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AiCreditArticle } from "../components/AiCreditArticle";
import { RatesMoveChart } from "../components/RatesMoveChart";

describe("RatesMoveChart", () => {
  it("keeps the yield change and the hike odds on separate scales", () => {
    render(<RatesMoveChart />);

    const move = screen.getByRole("img", { name: /10-year change/i });
    expect(move.getAttribute("data-axis-min")).toBe("0");
    expect(move.getAttribute("data-axis-max")).toBe("40");
    expect(screen.getByText("+25 bp")).toBeTruthy();
    expect(screen.getByText("+35 bp")).toBeTruthy();
    expect(screen.getByText(/Not additive/)).toBeTruthy();

    const odds = screen.getByRole("img", { name: /October hike/i });
    expect(odds.getAttribute("data-axis-min")).toBe("0");
    expect(odds.getAttribute("data-axis-max")).toBe("100");
    expect(odds.textContent).not.toMatch(/36/);
    expect(screen.getByText("71%")).toBeTruthy();
    expect(screen.getByText("+36 bp").closest("[data-unit]")?.getAttribute("data-unit")).toBe("bp");
  });
});

describe("AiCreditArticle", () => {
  it("places both charts under the body", () => {
    render(<AiCreditArticle />);

    const body = document.querySelector("[data-slot=body]");
    const slot = document.querySelector("[data-slot=under-body]");
    expect(body).toBeTruthy();
    expect(slot).toBeTruthy();
    expect(slot!.compareDocumentPosition(body!) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
    expect(slot!.querySelectorAll("figure")).toHaveLength(2);
    expect(document.querySelector("article img")).toBeNull();
    expect(slot!.querySelectorAll('[data-mark="softbank"] line')).toHaveLength(1);
  });
});
