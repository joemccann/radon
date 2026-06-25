/**
 * @vitest-environment jsdom
 */

import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import SortTh from "../components/SortTh";

afterEach(() => {
  cleanup();
});

describe("SortTh help tooltip", () => {
  it("does not toggle sorting when the help bubble is clicked or focused", () => {
    const onToggle = vi.fn();

    render(
      <table>
        <thead>
          <tr>
            <SortTh
              label="Score"
              sortKey="score"
              activeKey={null}
              direction="asc"
              onToggle={onToggle}
              helpText="Composite score details"
              helpAriaLabel="Score details"
              helpTriggerTestId="sort-help-trigger"
              helpContentTestId="sort-help-content"
            />
          </tr>
        </thead>
      </table>,
    );

    const trigger = screen.getByTestId("sort-help-trigger");
    fireEvent.click(within(trigger).getByRole("button"));
    fireEvent.keyDown(trigger, { key: "Enter" });
    fireEvent.keyDown(trigger, { key: " " });

    expect(onToggle).not.toHaveBeenCalled();

    fireEvent.mouseEnter(trigger);
    expect(screen.getByTestId("sort-help-content").textContent).toContain("Composite score details");

    fireEvent.click(screen.getByRole("columnheader"));
    expect(onToggle).toHaveBeenCalledWith("score");
  });
});
