import { describe, expect, it } from "vitest";
import {
  llmsTxtRouteResult,
  markdownRouteResult,
  openApiRouteResult,
} from "./machine-routes";
import { MARKDOWN_CONTENT_TYPE, MARKDOWN_VARY, notFoundMarkdown } from "./markdown-pages";
import { llmsTxt } from "./llms-txt";
import { publicOpenApi } from "./openapi";

describe("public machine-readable routes", () => {
  it("serves markdown for known pages and 404 markdown for unknown ones", () => {
    const home = markdownRouteResult();
    expect(home.status).toBe(200);
    expect(home.headers["Content-Type"]).toBe(MARKDOWN_CONTENT_TYPE);
    expect(home.headers.Vary).toBe(MARKDOWN_VARY);
    expect(home.body).toContain("# Radon Terminal");

    const missing = markdownRouteResult(["some-path-that-does-not-exist"]);
    expect(missing.status).toBe(404);
    expect(missing.headers["Content-Type"]).toBe(MARKDOWN_CONTENT_TYPE);
    expect(missing.body).toBe(notFoundMarkdown());
  });

  it("serves llms.txt as markdown", () => {
    const response = llmsTxtRouteResult();
    expect(response.status).toBe(200);
    expect(response.headers["Content-Type"]).toBe("text/markdown; charset=utf-8");
    expect(response.headers.Vary).toMatch(/Accept/);
    expect(response.body).toBe(llmsTxt);
  });

  it("serves the public OpenAPI document", () => {
    const response = openApiRouteResult();
    expect(response.status).toBe(200);
    expect(response.headers["Content-Type"]).toContain("application/json");
    expect(JSON.parse(response.body).info.title).toBe(publicOpenApi.info.title);
  });
});
