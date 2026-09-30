import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = resolve(fileURLToPath(import.meta.url), "..");
const webDir = resolve(__dirname, "..");
const server = readFileSync(resolve(webDir, "..", "scripts", "ib_realtime_server.js"), "utf8");
const css = readFileSync(resolve(webDir, "app", "globals.css"), "utf8");

// The cockpit book stretches the montage to the full panel height, level with
// the Time & Sales tape. A 10-row SMART request left the bottom half empty.
describe("book montage fills the panel height", () => {
  it("requests enough equity SMART depth rows to fill a desktop cockpit", () => {
    const match = server.match(/const DEPTH_NUM_ROWS_EQUITY = (\d+);/);
    expect(match).not.toBeNull();
    expect(Number(match![1])).toBeGreaterThanOrEqual(40);
    // The same budget bounds the position-shift reducer, so rows past the
    // request can never be applied.
    expect(server).toContain("const maxRows = state.isFutures ? DEPTH_NUM_ROWS_FUTURES : DEPTH_NUM_ROWS_EQUITY;");
  });

  it("caps the row enter stagger so deep levels do not trail in", () => {
    expect(css).toContain("animation-delay: calc(min(var(--i, 0), 12) * 20ms);");
  });

  it("pins the montage column heads while the deeper book scrolls", () => {
    expect(css).toMatch(
      /\.book-region \.book-window \.book-montage \.book-colhead \{[^}]*position: sticky;[^}]*top: 0;/,
    );
  });
});
