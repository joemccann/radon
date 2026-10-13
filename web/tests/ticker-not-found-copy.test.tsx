// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import path from "node:path";
import TickerNotFound from "@/app/[ticker]/not-found";

afterEach(cleanup);

describe("/[ticker] not-found", () => {
  it("speaks in sentence case and offers search plus a way back", () => {
    render(<TickerNotFound />);
    expect(screen.getByRole("heading", { name: "No instrument at this address" })).toBeTruthy();
    expect(screen.queryByText(/404: Instrument Not Found/i)).toBeNull();
    expect(screen.getByText(/Check the symbol, or search from the dashboard with ⌘K/)).toBeTruthy();
    const back = screen.getByRole("link", { name: "Back to dashboard" });
    expect(back.getAttribute("href")).toBe("/dashboard");
  });

  it("keeps the prerender build constraint: plain <a>, no next/link, no hooks, no globals.css", () => {
    const src = readFileSync(path.join(__dirname, "../app/[ticker]/not-found.tsx"), "utf8");
    expect(src).not.toMatch(/from ["']next\/link["']/);
    expect(src).not.toMatch(/\buse(Effect|State|Context)\(/);
    expect(src).not.toMatch(/import [^;]*globals\.css/);
    expect(src).not.toMatch(/#[0-9A-Fa-f]{6}/);
  });
});
