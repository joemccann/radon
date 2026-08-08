import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";

import {
  isLocalAuthlessTestBypassEnabled,
  isLocalDevAuthBypassEnabled,
  requestHostUrl,
} from "../middleware";

describe("isLocalAuthlessTestBypassEnabled (explicit test flag)", () => {
  it("bypasses auth only when localhost and explicit test flag are enabled", () => {
    expect(
      isLocalAuthlessTestBypassEnabled(new URL("http://localhost:3000/portfolio"), "1"),
    ).toBe(true);
    expect(
      isLocalAuthlessTestBypassEnabled(new URL("http://127.0.0.1:3000/portfolio"), "1"),
    ).toBe(true);
  });

  it("does not bypass auth without the explicit test flag", () => {
    expect(
      isLocalAuthlessTestBypassEnabled(new URL("http://localhost:3000/portfolio"), undefined),
    ).toBe(false);
    expect(
      isLocalAuthlessTestBypassEnabled(new URL("http://localhost:3000/portfolio"), "0"),
    ).toBe(false);
  });

  it("does not bypass auth for non-local hosts even under the flag", () => {
    expect(
      isLocalAuthlessTestBypassEnabled(new URL("https://radon.run/portfolio"), "1"),
    ).toBe(false);
    expect(
      isLocalAuthlessTestBypassEnabled(new URL("http://8.8.8.8/portfolio"), "1"),
    ).toBe(false);
    // A private LAN IP is NOT local — the bypass is loopback-only.
    expect(
      isLocalAuthlessTestBypassEnabled(new URL("http://172.17.0.2:3000/portfolio"), "1"),
    ).toBe(false);
  });
});

describe("requestHostUrl (client host from Host header, not the bind address)", () => {
  it("derives the host from the Host header, not nextUrl (0.0.0.0 bind address)", () => {
    // Mirrors `next start -H 0.0.0.0`: the URL Next builds carries the bind
    // address 0.0.0.0, but the client addressed 127.0.0.1 — the bypass must see
    // the latter.
    const request = new NextRequest("http://0.0.0.0:3000/portfolio", {
      headers: { host: "127.0.0.1:3000" },
    });
    expect(requestHostUrl(request).hostname).toBe("127.0.0.1");
    expect(
      isLocalAuthlessTestBypassEnabled(requestHostUrl(request), "1"),
    ).toBe(true);
  });

  it("falls back to nextUrl when the Host header is absent", () => {
    const request = new NextRequest("http://localhost:3000/portfolio");
    // NextRequest synthesizes a Host header from the URL, so this exercises the
    // normal path; the hostname still resolves to the addressed host.
    expect(requestHostUrl(request).hostname).toBe("localhost");
  });

  it("does not treat a production Host as local even with a spoofed loopback bind", () => {
    const request = new NextRequest("http://0.0.0.0:3000/portfolio", {
      headers: { host: "app.radon.run" },
    });
    expect(requestHostUrl(request).hostname).toBe("app.radon.run");
    expect(
      isLocalAuthlessTestBypassEnabled(requestHostUrl(request), "1"),
    ).toBe(false);
  });
});

describe("isLocalDevAuthBypassEnabled (auto-bypass for `next dev`)", () => {
  it("bypasses auth on localhost / 127.0.0.1 / ::1 in development", () => {
    expect(isLocalDevAuthBypassEnabled(new URL("http://localhost:3000/portfolio"), "development")).toBe(true);
    expect(isLocalDevAuthBypassEnabled(new URL("http://127.0.0.1:3000/portfolio"), "development")).toBe(true);
    expect(isLocalDevAuthBypassEnabled(new URL("http://[::1]:3000/portfolio"), "development")).toBe(true);
  });

  it("does NOT bypass auth in production, even on localhost", () => {
    expect(isLocalDevAuthBypassEnabled(new URL("http://localhost:3000/portfolio"), "production")).toBe(false);
  });

  it("does NOT bypass auth for non-local hosts in development", () => {
    expect(isLocalDevAuthBypassEnabled(new URL("https://radon.run/portfolio"), "development")).toBe(false);
  });

  it("treats undefined NODE_ENV as non-production (Node REPL / vitest default)", () => {
    expect(isLocalDevAuthBypassEnabled(new URL("http://localhost:3000/portfolio"), undefined)).toBe(true);
  });

  it("treats 'test' (vitest) as non-production", () => {
    expect(isLocalDevAuthBypassEnabled(new URL("http://localhost:3000/portfolio"), "test")).toBe(true);
  });
});
