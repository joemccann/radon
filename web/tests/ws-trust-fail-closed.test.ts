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
  shouldSkipTicketValidation,
} from "../../scripts/lib/wsTrust.js";

const repositoryRoot = fileURLToPath(new URL("../../", import.meta.url));

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
    const service = readFileSync(
      path.resolve(repositoryRoot, "cloud/services/radon-relay.service"),
      "utf8",
    );
    expect(service).toContain("Environment=WS_BIND_HOST=127.0.0.1");
    expect(service).toContain("Environment=RADON_WS_REQUIRE_CLERK=1");
  });

  it("parses upgrade targets against a fixed base inside the guarded block", () => {
    const source = readFileSync(
      path.resolve(repositoryRoot, "scripts/ib_realtime_server.js"),
      "utf8",
    );
    expect(source).toContain('new URL(req.url || "/", "http://relay.invalid")');
    expect(source).not.toContain("`http://${req.headers.host}`");
  });
});
