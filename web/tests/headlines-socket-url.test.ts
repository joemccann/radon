import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import {
  HEADLINES_WS_PATH,
  LOCAL_HEADLINES_WS_URL,
  headlinesUrlLeaksUpstream,
  resolveHeadlinesWebSocketUrl,
} from "../lib/headlinesSocket";

describe("resolveHeadlinesWebSocketUrl", () => {
  const originalWindow = globalThis.window;

  afterEach(() => {
    if (originalWindow) {
      // @ts-expect-error restore jsdom window
      globalThis.window = originalWindow;
    }
  });

  it("uses the local Radon hub, never the upstream host", () => {
    expect(resolveHeadlinesWebSocketUrl()).toBe(LOCAL_HEADLINES_WS_URL);
    expect(LOCAL_HEADLINES_WS_URL).toContain(HEADLINES_WS_PATH);
    expect(headlinesUrlLeaksUpstream(LOCAL_HEADLINES_WS_URL)).toBe(false);
    expect(headlinesUrlLeaksUpstream("wss://api.mktnews.net/?lang=en")).toBe(true);
    expect(resolveHeadlinesWebSocketUrl("wss://api.mktnews.net/?lang=en")).toBe(
      LOCAL_HEADLINES_WS_URL,
    );
  });

  it("on a hosted origin proxies through the same host path", () => {
    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: {
        location: { protocol: "https:", host: "app.radon.run", hostname: "app.radon.run" },
      },
    });
    expect(resolveHeadlinesWebSocketUrl(undefined)).toBe("wss://app.radon.run/ws-headlines");
    expect(headlinesUrlLeaksUpstream("wss://app.radon.run/ws-headlines")).toBe(false);
  });
});

describe("Caddy /ws-headlines handle", () => {
  it("is an exact path match, not a prefix", () => {
    const caddy = readFileSync(
      path.resolve(__dirname, "../../cloud/caddy/Caddyfile"),
      "utf8",
    );
    const active = caddy
      .split("\n")
      .filter((line) => !line.trim().startsWith("#"))
      .join("\n");
    expect(active).toMatch(/handle \/ws-headlines\s*\{/);
    expect(active).not.toMatch(/handle \/ws-headlines\*/);
  });
});
