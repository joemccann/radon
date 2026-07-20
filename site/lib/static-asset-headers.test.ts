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
});
