/**
 * Subscriptions only (operator policy 2026-10-26, absolute): with the retired
 * escape-hatch flag and every prepaid console key set, the web LLM layer reads
 * none of them and sends none of them. Each set variable earns one warning
 * naming it (never its value) per process.
 */
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { chat, hasAnthropicAuth, hasOpenAiAuth, resolveXaiAuth } from "@/lib/llm/provider";
import * as subscriptionAuth from "@/lib/llm/subscriptionAuth";
import { IGNORED_PREPAID_ENV, resetPrepaidWarnings } from "@/lib/llm/prepaidDenylist";

const NAMES = [
  "ANTHROPIC_API_KEY",
  "CLAUDE_CODE_API_KEY",
  "CLAUDE_API_KEY",
  "XAI_API_KEY",
  "GROK_API_KEY",
  "OPENAI_API_KEY",
  "RADON_LADDER_ALLOW_PREPAID",
];
const GRANT_ENV = [
  "CLAUDE_CODE_OAUTH_TOKEN",
  "CLAUDE_CONFIG_DIR",
  "XAI_OAUTH_TOKEN",
  "GROK_OAUTH_TOKEN",
  "GROK_AUTH_FILE",
  "CODEX_OAUTH_TOKEN",
  "CODEX_HOME",
  "LLM_PROVIDER",
  "LLM_FALLBACK_PROVIDER",
  "ASSISTANT_MOCK",
];

const valueOf = (name: string) => `value-of-${name.toLowerCase()}`;

describe("web LLM layer is subscriptions only", () => {
  const saved: Record<string, string | undefined> = {};

  beforeEach(() => {
    for (const key of [...NAMES, ...GRANT_ENV]) {
      saved[key] = process.env[key];
      delete process.env[key];
    }
    for (const name of NAMES) process.env[name] = valueOf(name);
    process.env.RADON_LADDER_ALLOW_PREPAID = "1";
    process.env.GROK_AUTH_FILE = path.join(os.tmpdir(), "radon-no-grok-auth.json");
    process.env.CLAUDE_CONFIG_DIR = path.join(os.tmpdir(), "radon-no-claude-config");
    process.env.CODEX_HOME = path.join(os.tmpdir(), "radon-no-codex-home");
    process.env.ASSISTANT_MOCK = "0";
    subscriptionAuth.resetSubscriptionAuthCache();
    resetPrepaidWarnings();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    for (const key of [...NAMES, ...GRANT_ENV]) {
      if (saved[key] === undefined) delete process.env[key];
      else process.env[key] = saved[key];
    }
  });

  it("deny list is exactly the six prepaid keys plus the retired flag", () => {
    expect([...IGNORED_PREPAID_ENV]).toEqual(NAMES);
  });

  it("the escape hatch is gone from subscriptionAuth", () => {
    expect("allowPrepaid" in subscriptionAuth).toBe(false);
  });

  it("no provider is available from prepaid keys, even with the flag set", () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(hasAnthropicAuth()).toBe(false);
    expect(hasOpenAiAuth()).toBe(false);
    expect(resolveXaiAuth()).toBeUndefined();
    expect(subscriptionAuth.resolveAnthropicSubscription()).toBeUndefined();
    expect(subscriptionAuth.resolveXaiSubscription()).toBeUndefined();
    expect(subscriptionAuth.resolveCodexSubscription()).toBeUndefined();
  });

  it.each(["anthropic", "xai", "openai"] as const)(
    "%s chat fails as a missing subscription and sends nothing",
    async (provider) => {
      vi.spyOn(console, "warn").mockImplementation(() => {});
      const fetchSpy = vi.fn(async () => new Response("{}", { status: 200 }));
      vi.stubGlobal("fetch", fetchSpy as unknown as typeof fetch);
      await expect(
        chat({ provider, messages: [{ role: "user", content: "hi" }] }),
      ).rejects.toThrow(/subscription/i);
      expect(fetchSpy).not.toHaveBeenCalled();
    },
  );

  it("with a grant, the request carries the grant and no prepaid value", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    process.env.CLAUDE_CODE_OAUTH_TOKEN = "oauth-grant";
    const calls: Array<{ url: string; init: RequestInit }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: unknown, init?: RequestInit) => {
        calls.push({ url: String(url), init: init ?? {} });
        return new Response(
          JSON.stringify({ content: [{ type: "text", text: "ok" }], stop_reason: "end_turn" }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }) as unknown as typeof fetch,
    );
    await chat({ provider: "anthropic", messages: [{ role: "user", content: "hi" }] });
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("https://api.anthropic.com/v1/messages");
    const headers = calls[0].init.headers as Record<string, string>;
    expect(headers.authorization).toBe("Bearer oauth-grant");
    expect(headers["x-api-key"]).toBeUndefined();
    const wire = JSON.stringify(headers) + String(calls[0].init.body);
    for (const name of NAMES) expect(wire).not.toContain(valueOf(name));
  });

  it("warns once per set variable per process, naming it and never its value", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    for (let i = 0; i < 3; i += 1) {
      hasAnthropicAuth();
      hasOpenAiAuth();
      resolveXaiAuth();
    }
    const lines = warn.mock.calls.map((args) => args.map(String).join(" "));
    expect(lines).toHaveLength(NAMES.length);
    for (const name of NAMES) {
      expect(lines.filter((line) => line.includes(`${name}:`))).toHaveLength(1);
    }
    for (const name of NAMES) expect(lines.join("\n")).not.toContain(valueOf(name));
  });

  it("does not warn when nothing prepaid is set", () => {
    for (const name of NAMES) delete process.env[name];
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    hasAnthropicAuth();
    hasOpenAiAuth();
    resolveXaiAuth();
    expect(warn).not.toHaveBeenCalled();
  });
});
