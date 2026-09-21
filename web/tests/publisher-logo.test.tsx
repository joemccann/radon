/**
 * @vitest-environment jsdom
 */
import React from "react";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { describe, it, expect, afterEach } from "vitest";
import PublisherLogo from "../components/PublisherLogo";

describe("<PublisherLogo />", () => {
  afterEach(() => {
    cleanup();
  });

  it("renders Goldman Sachs logo for Goldman Sachs publisher", () => {
    render(<PublisherLogo publisher="Goldman Sachs" />);
    const logo = screen.getByTestId("publisher-logo");
    expect(logo).toBeDefined();
    expect(logo.getAttribute("data-publisher-id")).toBe("goldman-sachs");
    expect(logo.getAttribute("data-is-fallback")).toBe("false");

    const img = screen.getByRole("img", { hidden: true });
    expect(img.getAttribute("src")).toBe("/icons/publishers/goldman-sachs.svg");
    expect(img.getAttribute("alt")).toBe("Goldman Sachs");
  });

  it("renders Bank of America logo for BofA variations", () => {
    render(<PublisherLogo publisher="BofA Global Research" />);
    const logo = screen.getByTestId("publisher-logo");
    expect(logo.getAttribute("data-publisher-id")).toBe("bank-of-america");
    expect(logo.getAttribute("data-is-fallback")).toBe("false");
    const img = screen.getByRole("img", { hidden: true });
    expect(img.getAttribute("src")).toBe("/icons/publishers/bank-of-america.svg");
  });

  it("renders Mizuho logo for Mizuho", () => {
    render(<PublisherLogo publisher="Mizuho" />);
    const logo = screen.getByTestId("publisher-logo");
    expect(logo.getAttribute("data-publisher-id")).toBe("mizuho");
    expect(logo.getAttribute("data-is-fallback")).toBe("false");
    const img = screen.getByRole("img", { hidden: true });
    expect(img.getAttribute("src")).toBe("/icons/publishers/mizuho.svg");
  });

  it("renders default fallback icon for unknown or unmapped publishers", () => {
    render(<PublisherLogo publisher="TS Lombard" />);
    const logo = screen.getByTestId("publisher-logo");
    expect(logo).toBeDefined();
    expect(logo.getAttribute("data-publisher-id")).toBe("default");
    expect(logo.getAttribute("data-is-fallback")).toBe("true");
    expect(screen.queryByRole("img", { hidden: true })).toBeNull();
  });

  it("renders default fallback icon when publisher is null or empty", () => {
    render(<PublisherLogo publisher={null} />);
    const logo = screen.getByTestId("publisher-logo");
    expect(logo).toBeDefined();
    expect(logo.getAttribute("data-publisher-id")).toBe("default");
    expect(logo.getAttribute("data-is-fallback")).toBe("true");
    expect(screen.queryByRole("img", { hidden: true })).toBeNull();
  });

  it("renders label and type badge when showLabel is true", () => {
    render(<PublisherLogo publisher="Goldman Sachs" showLabel showType />);
    const badge = screen.getByTestId("publisher-badge");
    expect(badge).toBeDefined();
    expect(screen.getByText("Goldman Sachs")).toBeDefined();
    expect(screen.getByText("Research")).toBeDefined();
  });

  it("falls back to fallback SVG on image load error", () => {
    render(<PublisherLogo publisher="Goldman Sachs" />);
    const img = screen.getByRole("img", { hidden: true });
    fireEvent.error(img);
    const logo = screen.getByTestId("publisher-logo");
    expect(logo.getAttribute("data-is-fallback")).toBe("true");
  });

  it("applies custom size attribute", () => {
    render(<PublisherLogo publisher="Goldman Sachs" size={24} />);
    const img = screen.getByRole("img", { hidden: true });
    expect(img.getAttribute("width")).toBe("24");
    expect(img.getAttribute("height")).toBe("24");
  });

  it("uses canonical tokens and does not use low-contrast text-base fallback in module CSS", async () => {
    const fs = await import("node:fs");
    const path = await import("node:path");
    const css = fs.readFileSync(path.resolve(__dirname, "../components/PublisherLogo.module.css"), "utf8");
    expect(css).toContain("var(--text-primary)");
    expect(css).toContain("var(--bg-subtle)");
    expect(css).toContain("var(--line-grid)");
    expect(css).toContain("var(--img-outline)");
    expect(css).not.toContain("--text-base");
    expect(css).not.toContain("#e2e8f0");
  });
});
