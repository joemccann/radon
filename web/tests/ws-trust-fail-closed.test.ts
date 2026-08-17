/**
 * @vitest-environment node
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import {
  isTrustedLocalUpgrade,
  resolveRelaySecurityConfig,
  resolveUpgradeTarget,
  shouldSkipTicketValidation,
} from "../../scripts/lib/wsTrust.js";

const repositoryRoot = fileURLToPath(new URL("../../", import.meta.url));

/**
 * systemd semantics, not substring semantics: `#` starts a comment and the LAST
 * assignment of a key wins. A `toContain` check passes on a commented-out line
 * and on a line a later override has already replaced.
 */
function readUnitEnvironment(unitPath: string): Record<string, string> {
  const environment: Record<string, string> = {};
  for (const rawLine of readFileSync(unitPath, "utf8").split("\n")) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || line.startsWith(";")) continue;
    const match = /^Environment=(?:"([^"]*)"|(.*))$/.exec(line);
    if (!match) continue;
    const assignment = match[1] ?? match[2];
    const separator = assignment.indexOf("=");
    if (separator <= 0) continue;
    environment[assignment.slice(0, separator)] = assignment.slice(separator + 1).trim();
  }
  return environment;
}

describe("WebSocket relay fail-closed boundary", () => {
  it("does not grant browser-originated loopback connections local trust", () => {
    expect(isTrustedLocalUpgrade("127.0.0.1", {
      origin: "http://localhost:3000",
      "sec-fetch-site": "same-site",
    })).toBe(false);
  });

  it("enforces tickets when Clerk is absent unless development explicitly opts in", () => {
    expect(shouldSkipTicketValidation({
      clerkConfigured: false,
      remoteAddr: "127.0.0.1",
      headers: {},
    })).toBe(false);
    expect(resolveRelaySecurityConfig({ NODE_ENV: "production" }).requireClerk).toBe(true);
  });

  it("pins the production unit to loopback and required Clerk configuration", () => {
    const environment = readUnitEnvironment(
      path.resolve(repositoryRoot, "cloud/services/radon-relay.service"),
    );
    expect(environment.WS_BIND_HOST).toBe("127.0.0.1");
    expect(environment.RADON_WS_REQUIRE_CLERK).toBe("1");
    expect(resolveRelaySecurityConfig(environment).bindHost).toBe("127.0.0.1");
    expect(resolveRelaySecurityConfig(environment).requireClerk).toBe(true);
  });

  it("resolves upgrade targets against a fixed base, never the attacker-supplied Host", () => {
    const target = resolveUpgradeTarget({
      url: "/ws?ticket=abc123",
      headers: { host: "evil.example" },
    });
    expect(target.origin).toBe("http://relay.invalid");
    expect(target.host).toBe("relay.invalid");
    expect(target.searchParams.get("ticket")).toBe("abc123");
    expect(resolveUpgradeTarget({ headers: { host: "evil.example" } }).origin).toBe(
      "http://relay.invalid",
    );
  });

  it("routes the relay's upgrade handler through the shared parser", () => {
    const source = readFileSync(
      path.resolve(repositoryRoot, "scripts/ib_realtime_server.js"),
      "utf8",
    );
    expect(source).toContain("resolveUpgradeTarget(req)");
    expect(source).not.toMatch(/new URL\([^)]*req\.headers\.host/);
  });
});
