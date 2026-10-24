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

  it("publishes accurate fixed-resource response media types", () => {
    const expected = {
      "/llms.txt": "text/markdown",
      "/openapi.json": "application/json",
      "/sitemap.xml": "application/xml",
      "/robots.txt": "text/plain",
    } as const;

    for (const [route, contentType] of Object.entries(expected)) {
      const operation = publicOpenApi.paths[route].get;
      expect(operation.parameters).toBeUndefined();
      expect(Object.keys(operation.responses)).toEqual(["200"]);
      expect(operation.responses["200"].content).toHaveProperty(contentType);
      expect(
        operation.responses["200"].content?.[contentType]?.schema,
      ).toBeTruthy();
    }
  });

  it("limits Accept negotiation and 406 responses to HTML pages", () => {
    const fixedPaths = new Set([
      "/llms.txt",
      "/openapi.json",
      "/sitemap.xml",
      "/robots.txt",
    ]);

    for (const [route, pathItem] of Object.entries(publicOpenApi.paths)) {
      const operation = pathItem.get;
      if (fixedPaths.has(route)) {
        expect(operation.responses["404"]).toBeUndefined();
        expect(operation.responses["406"]).toBeUndefined();
        continue;
      }

      expect(operation.parameters).toContainEqual(
        expect.objectContaining({ name: "Accept", in: "header" }),
      );
      expect(operation.responses["200"].content).toHaveProperty("text/html");
      expect(operation.responses["200"].content).toHaveProperty("text/markdown");
      expect(operation.responses["406"]).toBeTruthy();
      expect(operation.responses["404"]).toBeUndefined();
    }
  });
});
