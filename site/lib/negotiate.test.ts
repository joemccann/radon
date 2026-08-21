import { describe, expect, it } from "vitest";
import {
  markdownApiPath,
  negotiate,
  NOT_ACCEPTABLE_BODY,
  shouldNegotiate,
} from "./negotiate";

describe("markdown path negotiation", () => {
  it("rewrites Accept: text/markdown to the markdown route", () => {
    expect(negotiate("/", "text/markdown")).toEqual({
      action: "rewrite",
      pathname: "/api/markdown",
    });
    expect(negotiate("/developers/auth", "text/markdown")).toEqual({
      action: "rewrite",
      pathname: "/api/markdown/developers/auth",
    });
  });

  it("always rewrites explicit .md URLs", () => {
    expect(negotiate("/crash-risk-index.md", "text/html")).toEqual({
      action: "rewrite",
      pathname: "/api/markdown/crash-risk-index",
    });
  });

  it("returns 406 when the client rejects HTML and markdown", () => {
    expect(negotiate("/", "application/pdf")).toEqual({
      action: "406",
      body: NOT_ACCEPTABLE_BODY,
    });
  });

  it("leaves HTML and static files alone", () => {
    expect(negotiate("/", "text/html")).toEqual({ action: "next" });
    expect(negotiate("/llms.txt", "text/markdown")).toEqual({ action: "next" });
    expect(negotiate("/openapi.json", "text/markdown")).toEqual({
      action: "next",
    });
    expect(negotiate("/og-image.png", "text/markdown")).toEqual({
      action: "next",
    });
  });

  it("skips Next internals", () => {
    expect(shouldNegotiate("/_next/static/chunk.js")).toBe(false);
    expect(shouldNegotiate("/api/markdown/developers")).toBe(false);
    expect(markdownApiPath("/")).toBe("/api/markdown");
    expect(markdownApiPath("/status.md")).toBe("/api/markdown/status");
  });
});
