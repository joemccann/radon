/**
 * @vitest-environment jsdom
 */

import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { DenseNumericTable } from "@/components/kit/DenseNumericTable";

afterEach(() => cleanup());

describe("DenseNumericTable sort", () => {
  it("reorders kit rows when Symbol is clicked", () => {
    render(<DenseNumericTable />);
    const first = () => within(screen.getByRole("table")).getAllByRole("row")[1].textContent ?? "";
    expect(first()).toContain("SPX");
    fireEvent.click(screen.getByRole("columnheader", { name: /symbol/i }));
    expect(first()).toContain("IWM");
    expect(screen.getByRole("columnheader", { name: /symbol/i }).getAttribute("aria-sort")).toBe("ascending");
  });
});
