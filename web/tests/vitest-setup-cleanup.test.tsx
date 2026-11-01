/**
 * @vitest-environment jsdom
 *
 * vitest.setup.ts owns the suite-wide @testing-library cleanup. vitest globals
 * are off, so RTL's built-in auto-cleanup never arms, and every file that does
 * not call cleanup() itself relies on the setup's. web/tests resolve
 * web/node_modules/@testing-library/react, a separate install from the root
 * copy the setup used to import statically, so the second test below failed
 * until the setup cleaned the registry the TEST FILE loaded. This file
 * registers no cleanup of its own: the first test leaves a rendered tree
 * behind, the second must start from an empty body.
 *
 * Order matters: vitest runs the tests of one file sequentially, in source
 * order, unless sequence.shuffle is set (it is not).
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

describe("vitest.setup.ts global cleanup (jsdom)", () => {
  it("leaves a rendered tree mounted at the end of the test", () => {
    render(<div data-testid="setup-cleanup-probe">mounted</div>);
    expect(screen.getByTestId("setup-cleanup-probe").textContent).toBe("mounted");
    expect(document.body.children.length).toBeGreaterThan(0);
  });

  it("starts the next test with an empty document.body", () => {
    expect(screen.queryByTestId("setup-cleanup-probe")).toBeNull();
    expect(document.body.innerHTML).toBe("");
  });
});
