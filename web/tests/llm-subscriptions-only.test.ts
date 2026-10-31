import { readFileSync, existsSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, expect, it, vi } from "vitest";
import { chat } from "@/lib/llm/provider";
import { IGNORED_PREPAID_ENV } from "@/lib/llm/prepaidDenylist";

afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });
it("never sends prepaid or subscription credentials from the web process", async () => {
  vi.stubEnv("ASSISTANT_MOCK", "0");
  const sentinels = [...IGNORED_PREPAID_ENV, "CLAUDE_CODE_OAUTH_TOKEN", "XAI_OAUTH_TOKEN", "CODEX_OAUTH_TOKEN"];
  for (const name of sentinels) vi.stubEnv(name, `secret-${name}`);
  const fetch = vi.fn(async () => new Response(JSON.stringify({ provider: "antigravity", model: "default", text: "ok" })));
  vi.stubGlobal("fetch", fetch);
  await chat({ messages: [{ role: "user", content: "hi" }] });
  expect(fetch).toHaveBeenCalledTimes(1);
  const wire = JSON.stringify(fetch.mock.calls);
  for (const name of sentinels) expect(wire).not.toContain(`secret-${name}`);
});
it("retires local credential readers and vendor request builders", () => {
  const source = readFileSync(resolve(__dirname, "../lib/llm/provider.ts"), "utf8");
  expect(source).not.toMatch(/subscriptionAuth|api\.anthropic|api\.x\.ai|chatgpt\.com|readFile/);
  expect(existsSync(resolve(__dirname, "../lib/llm/subscriptionAuth.ts"))).toBe(false);
});
