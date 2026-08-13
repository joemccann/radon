import { describe, it, expect } from "vitest";
import {
  arrivedViaProxy,
  isBrowserUpgrade,
  isTrustedLocalUpgrade,
  resolveRelaySecurityConfig,
  shouldSkipTicketValidation,
} from "./wsTrust.js";

describe("wsTrust", () => {
  describe("arrivedViaProxy", () => {
    it("detects Caddy / proxy forwarding headers", () => {
      expect(arrivedViaProxy({ "x-forwarded-for": "203.0.113.5" })).toBe(true);
      expect(arrivedViaProxy({ "x-real-ip": "203.0.113.5" })).toBe(true);
      expect(arrivedViaProxy({ forwarded: "for=203.0.113.5" })).toBe(true);
      expect(arrivedViaProxy({ "x-forwarded-host": "app.radon.run" })).toBe(true);
    });

    it("is false for a plain server-to-server request", () => {
      expect(arrivedViaProxy({ host: "127.0.0.1:8765" })).toBe(false);
      expect(arrivedViaProxy({})).toBe(false);
      expect(arrivedViaProxy()).toBe(false);
    });
  });

  describe("isTrustedLocalUpgrade", () => {
    it("trusts loopback without forwarding headers", () => {
      expect(isTrustedLocalUpgrade("127.0.0.1", {})).toBe(true);
      expect(isTrustedLocalUpgrade("::1", {})).toBe(true);
      expect(isTrustedLocalUpgrade("::ffff:127.0.0.1", {})).toBe(true);
    });

    it("does NOT trust loopback that arrived via the reverse proxy", () => {
      // This is the production attack: Caddy proxies internet traffic to
      // localhost:8765, so remoteAddr is 127.0.0.1 but X-Forwarded-For is set.
      expect(
        isTrustedLocalUpgrade("127.0.0.1", { "x-forwarded-for": "203.0.113.5" }),
      ).toBe(false);
    });

    it("does not trust non-loopback peers", () => {
      expect(isTrustedLocalUpgrade("203.0.113.5", {})).toBe(false);
      expect(isTrustedLocalUpgrade("", {})).toBe(false);
    });

    it("does not trust browser handshakes even when the peer is loopback", () => {
      expect(isBrowserUpgrade({ origin: "http://localhost:3000" })).toBe(true);
      expect(isBrowserUpgrade({ "sec-fetch-site": "same-site" })).toBe(true);
      expect(
        isTrustedLocalUpgrade("127.0.0.1", {
          origin: "http://localhost:3000",
          "sec-fetch-site": "same-site",
        }),
      ).toBe(false);
    });
  });

  describe("shouldSkipTicketValidation", () => {
    it("fails closed when Clerk is unconfigured by default", () => {
      expect(
        shouldSkipTicketValidation({ clerkConfigured: false, remoteAddr: "203.0.113.5", headers: { "x-forwarded-for": "203.0.113.5" } }),
      ).toBe(false);
      expect(
        shouldSkipTicketValidation({ clerkConfigured: false, remoteAddr: "127.0.0.1", headers: {} }),
      ).toBe(false);
    });

    it("allows authless development only with explicit opt-in on a non-browser loopback call", () => {
      expect(
        shouldSkipTicketValidation({
          clerkConfigured: false,
          allowUnauthenticatedDev: true,
          remoteAddr: "127.0.0.1",
          headers: {},
        }),
      ).toBe(true);
      expect(
        shouldSkipTicketValidation({
          clerkConfigured: false,
          allowUnauthenticatedDev: true,
          remoteAddr: "127.0.0.1",
          headers: { origin: "http://localhost:3000" },
        }),
      ).toBe(false);
    });

    it("ENFORCES tickets for proxied production connections (the bug)", () => {
      expect(
        shouldSkipTicketValidation({
          clerkConfigured: true,
          remoteAddr: "127.0.0.1",
          headers: { "x-forwarded-for": "203.0.113.5" },
        }),
      ).toBe(false);
    });

    it("skips for genuine loopback server-to-server when Clerk configured", () => {
      expect(
        shouldSkipTicketValidation({ clerkConfigured: true, remoteAddr: "127.0.0.1", headers: {} }),
      ).toBe(true);
    });

    it("enforces tickets for direct remote connections", () => {
      expect(
        shouldSkipTicketValidation({ clerkConfigured: true, remoteAddr: "203.0.113.5", headers: {} }),
      ).toBe(false);
    });
  });

  describe("resolveRelaySecurityConfig", () => {
    it("defaults the relay to loopback and requires Clerk in production", () => {
      expect(resolveRelaySecurityConfig({ NODE_ENV: "production" })).toEqual({
        allowUnauthenticatedDev: false,
        bindHost: "127.0.0.1",
        clerkConfigured: false,
        requireClerk: true,
      });
    });

    it("requires an explicit development opt-in for missing Clerk", () => {
      expect(resolveRelaySecurityConfig({ NODE_ENV: "development" }).allowUnauthenticatedDev).toBe(false);
      expect(resolveRelaySecurityConfig({
        NODE_ENV: "development",
        RADON_WS_ALLOW_UNAUTHENTICATED_DEV: "1",
      }).allowUnauthenticatedDev).toBe(true);
      expect(resolveRelaySecurityConfig({
        NODE_ENV: "production",
        RADON_WS_ALLOW_UNAUTHENTICATED_DEV: "1",
      }).allowUnauthenticatedDev).toBe(false);
    });
  });
});
