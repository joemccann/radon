// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import path from "node:path";
import SetupWizard from "../components/setup/SetupWizard";

afterEach(cleanup);

describe("/setup token step (plate 65)", () => {
  it("names the step in the heading and labels the token field", () => {
    render(<SetupWizard />);
    expect(screen.getByRole("heading", { name: "Paste the setup token from your terminal" })).toBeTruthy();
    const input = screen.getByLabelText("Setup token");
    expect(input.getAttribute("data-testid")).toBe("setup-token-input");
    expect(input.className).toContain("setup-token-input");
    expect(document.body.textContent).not.toContain("parked");
  });

  it("names the action and arms it once a token is pasted", () => {
    render(<SetupWizard />);
    const submit = screen.getByRole("button", { name: "Begin setup" });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Setup token"), { target: { value: "tok" } });
    expect((submit as HTMLButtonElement).disabled).toBe(false);
  });

  it("gives the token field the full row instead of the 120px numeric width", () => {
    const css = readFileSync(path.join(__dirname, "../app/globals.css"), "utf8");
    const rule = css.match(/\.setup-token-input\s*\{([^}]*)\}/);
    expect(rule?.[1]).toMatch(/width:\s*100%/);
    expect(rule?.[1]).toMatch(/text-align:\s*left/);
  });

  it("keeps Begin setup on one line beside the field", () => {
    render(<SetupWizard />);
    const submit = screen.getByRole("button", { name: "Begin setup" });
    expect(submit.className).toContain("setup-token-submit");
    const css = readFileSync(path.join(__dirname, "../app/globals.css"), "utf8");
    const rule = css.match(/\.setup-token-submit\s*\{([^}]*)\}/);
    expect(rule?.[1]).toMatch(/white-space:\s*nowrap/);
    expect(rule?.[1]).toMatch(/flex-shrink:\s*0/);
  });
});
