import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";


describe("production font build", () => {
  it("bundles fonts locally without a Google Fonts build-time fetch", () => {
    const layout = readFileSync(resolve(__dirname, "../app/layout.tsx"), "utf8");

    expect(layout).not.toContain("next/font/google");
    expect(layout).toContain("next/font/local");
    expect(layout).toContain("../public/fonts/Inter-Variable.woff2");
    expect(layout).toContain("../public/fonts/IBMPlexMono-Regular.woff");
    expect(layout).toContain('"--font-sans": inter.style.fontFamily');
    expect(layout).toContain('"--font-mono": plexMono.style.fontFamily');
    expect(layout).toContain("style={fontVariables}");
  });
});
