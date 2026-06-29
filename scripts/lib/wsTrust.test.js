import { describe, it, expect } from "vitest";
import {
  arrivedViaProxy,
  isTrustedLocalUpgrade,
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
  });

  describe("shouldSkipTicketValidation", () => {
    it("skips when Clerk is unconfigured (local dev)", () => {
      expect(
        shouldSkipTicketValidation({ clerkConfigured: false, remoteAddr: "203.0.113.5", headers: { "x-forwarded-for": "203.0.113.5" } }),
      ).toBe(true);
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
});
