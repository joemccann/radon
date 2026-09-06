/** @vitest-environment jsdom */
import { createElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import Sidebar from "../components/Sidebar";

vi.mock("@/lib/useProfile", () => ({ useProfile: () => ({ profile: { username: "Operator", avatar_url: null } }) }));
afterEach(cleanup);
const css = readFileSync(join(__dirname, "../components/ClearShell.module.css"), "utf8");

describe("Clear workspace chrome", () => {
  it("uses one horizontal header rather than a competing fixed-height sidebar", () => {
    expect(css).toContain("grid-template-rows: 84px auto");
    expect(css).toContain("grid-template-columns: minmax(0, 1fr) auto");
    expect(css).toContain("@media (max-width: 640px)");
  });

  it("keeps the brand compact and links home and profile without a full-height rail", () => {
    const { container } = render(createElement(Sidebar, { activeSection: "portfolio", actionTone: "currentColor" }));
    const monogram = screen.getByRole("link", { name: "Radon home" }).querySelector("svg");
    expect(monogram?.getAttribute("width")).toBe("28");
    expect(monogram?.getAttribute("height")).toBe("28");
    expect(screen.getByRole("link", { name: "Profile" }).getAttribute("href")).toBe("/profile");
    expect(container.querySelector("aside.sidebar")).toBeNull();
  });
});
