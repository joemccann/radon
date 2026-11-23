import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("model catalog does not pin a snapshot id", () => {
  it("reads the gateway catalog and does not assign a provider snapshot", () => {
    const src = readFileSync(new URL("../lib/llm/catalog.ts", import.meta.url), "utf8");
    expect(src).toContain("/model-gateway/models");
    expect(src).not.toMatch(/["'](claude-|grok-\d|gpt-\d|nvidia\/nemotron|qwen-)/);
  });
});
