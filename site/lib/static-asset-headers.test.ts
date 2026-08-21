import { describe, expect, it } from "vitest";
import nextConfig from "../next.config";

describe("static asset headers", () => {
  it("marks /_next/static assets noindex so Googlebot drops them from indexing", async () => {
    expect(nextConfig.headers).toBeTypeOf("function");
    const rules = await nextConfig.headers!();
    const staticRule = rules.find((rule) => rule.source === "/_next/static/:path*");
    expect(staticRule).toBeDefined();
    expect(staticRule!.headers).toContainEqual({
      key: "X-Robots-Tag",
      value: "noindex",
    });
  });

  it("publishes Vary: Accept so HTML and markdown variants cache separately", async () => {
    const rules = await nextConfig.headers!();
    const varyRules = rules.filter((rule) =>
      rule.headers.some((header) => header.key === "Vary"),
    );
    expect(varyRules.map((rule) => rule.source)).toEqual(
      expect.arrayContaining(["/", "/:path*"]),
    );
    for (const rule of varyRules) {
      expect(rule.headers).toContainEqual({
        key: "Vary",
        value: "Accept, Accept-Encoding",
      });
    }
  });
});
