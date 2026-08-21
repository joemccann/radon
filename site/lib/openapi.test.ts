import { describe, expect, it } from "vitest";
import { LEGAL_CONTACT_EMAIL } from "./legal";
import { publicOpenApi } from "./openapi";
import { siteUrl } from "./seo";

describe("public OpenAPI document", () => {
  it("is OpenAPI 3.1 named for Radon Terminal", () => {
    expect(publicOpenApi.openapi).toBe("3.1.0");
    expect(publicOpenApi.info.title).toBe(
      "Radon Terminal public developer API",
    );
    expect(publicOpenApi.info.contact).toMatchObject({
      email: LEGAL_CONTACT_EMAIL,
      url: `${siteUrl}/developers`,
    });
    expect(publicOpenApi.servers).toEqual([{ url: siteUrl }]);
  });

  it("documents the named developer resources", () => {
    const paths = Object.keys(publicOpenApi.paths);
    for (const route of [
      "/llms.txt",
      "/openapi.json",
      "/developers",
      "/developers/openapi",
      "/developers/auth",
      "/developers/mcp",
      "/developers/webhooks",
      "/agent-instructions",
    ]) {
      expect(paths).toContain(route);
      expect(publicOpenApi.paths[route].get.summary).toMatch(/Radon|sitemap|Robots/i);
    }
  });
});
