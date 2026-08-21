import { describe, expect, it } from "vitest";
import { appendVaryAccept, preferredType, VARY_ACCEPT } from "./accept";

describe("Accept markdown negotiation", () => {
  it("prefers text/markdown when it is the first usable type", () => {
    expect(preferredType("text/markdown")).toBe("text/markdown");
    expect(preferredType("text/markdown, text/html;q=0.8")).toBe(
      "text/markdown",
    );
  });

  it("prefers text/html for browser-like headers", () => {
    expect(
      preferredType(
        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      ),
    ).toBe("text/html");
  });

  it("honors q-values and q=0 rejections", () => {
    expect(preferredType("text/html;q=0.2, text/markdown;q=0.9")).toBe(
      "text/markdown",
    );
    expect(preferredType("text/markdown;q=0, text/html;q=1")).toBe("text/html");
    expect(preferredType("text/html;q=0, text/markdown;q=0")).toBeNull();
    expect(preferredType("text/html;q=0, */*;q=1")).toBe("text/markdown");
  });

  it("defaults to HTML when Accept is missing", () => {
    expect(preferredType(null)).toBe("text/html");
    expect(preferredType("")).toBe("text/html");
  });

  it("appends Accept and Accept-Encoding to Vary", () => {
    const headers = new Headers();
    appendVaryAccept(headers);
    expect(headers.get("Vary")).toBe(VARY_ACCEPT);

    const existing = new Headers({
      Vary: "rsc, next-router-state-tree",
    });
    appendVaryAccept(existing);
    expect(existing.get("Vary")).toMatch(/Accept/i);
    expect(existing.get("Vary")).toMatch(/Accept-Encoding/i);
  });
});
