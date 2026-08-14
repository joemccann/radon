import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const WEB = join(import.meta.dirname, "..");
const src = (rel: string) => readFileSync(join(WEB, rel), "utf8");

describe("ticker book depth wiring", () => {
  it("passes live depths and tape into the memoized ticker workspace", () => {
    const shell = src("components/WorkspaceShell.tsx");
    expect(shell).toMatch(/depths=\{activeSection === "ticker-detail" \? depths : undefined\}/);
    expect(shell).toMatch(/tape=\{activeSection === "ticker-detail" \? tape : undefined\}/);

    const sections = src("components/WorkspaceSections.tsx");
    expect(sections).toMatch(/depths\?: Record<string, DepthBook>/);
    expect(sections).toMatch(/tape\?: Record<string, Trade\[\]>/);
    expect(sections).toMatch(/<TickerWorkspace[^>]*depths=\{depths\}/);
    expect(sections).toMatch(/<TickerWorkspace[^>]*tape=\{tape\}/);

    const workspace = src("components/TickerWorkspace.tsx");
    expect(workspace).toMatch(/depths: depthsProp/);
    expect(workspace).toMatch(/const depths = depthsProp \?\? getDepths\(\)/);
    expect(workspace).toMatch(/const tape = tapeProp \?\? getTape\(\)/);
  });

  it("grows the cockpit book grid so the montage is not a header over empty space", () => {
    const css = src("app/globals.css");
    expect(css).toMatch(/\.book-region \.book-window \.book-body-grid \{[^}]*flex:\s*1 1 auto/s);
  });
});
