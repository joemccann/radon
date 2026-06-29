import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = resolve(fileURLToPath(import.meta.url), "..");
const webDir = resolve(__dirname, "..");
const projectRoot = resolve(webDir, "..");
const source = readFileSync(resolve(projectRoot, "scripts", "ib_realtime_server.js"), "utf8");
const wsTrust = readFileSync(resolve(projectRoot, "scripts", "lib", "wsTrust.js"), "utf8");

describe("ib_realtime_server.js WebSocket auth", () => {
  it("delegates the upgrade trust decision to the shared wsTrust helper", () => {
    // Hardened 2026-06-28: the relay no longer trusts socket.remoteAddress
    // alone. Caddy reverse-proxies /ws* to localhost:8765, so every internet
    // client appeared as 127.0.0.1 and ticket auth was silently skipped for all
    // production traffic. The gate now lives in scripts/lib/wsTrust.js.
    expect(source).toContain("shouldSkipTicketValidation");
    expect(source).toContain("./lib/wsTrust.js");
  });

  it("wsTrust requires a ticket for proxied (X-Forwarded-*) connections", () => {
    // The loopback bypass must be denied when forwarding headers are present.
    expect(wsTrust).toContain("x-forwarded-for");
    expect(wsTrust).toContain('"127.0.0.1"');
    expect(wsTrust).toContain('"::1"');
    expect(wsTrust).toContain('"::ffff:127.0.0.1"');
    expect(wsTrust).toContain("arrivedViaProxy");
  });

  it("still validates tickets for non-trusted connections when CLERK_JWKS_URL is set", () => {
    expect(source).toContain('const ticket = url.searchParams.get("ticket")');
    expect(source).toContain("TICKET_VALIDATE_URL");
    expect(source).toContain("401 Unauthorized");
  });
});

describe("ib_realtime_server.js stale-data restart modes", () => {
  it("keeps cloud and docker on reconnect-only recovery", () => {
    const cloudDockerBlock = source.match(
      /if \(GATEWAY_MODE === "cloud" \|\| GATEWAY_MODE === "docker"\) \{[\s\S]*?\n  \} else \{/,
    )?.[0] ?? "";

    expect(cloudDockerBlock).toContain('GATEWAY_MODE === "cloud" || GATEWAY_MODE === "docker"');
    expect(cloudDockerBlock).toContain("disconnecting and scheduling reconnect");
    expect(cloudDockerBlock).not.toContain("restart-secure-ibc-service.sh");
  });

  it("does not restart local launchd IBC on stale ticks", () => {
    const launchdBlock = source.match(/else \{\n    \/\/ Local launchd mode[\s\S]*?\n  \}/)?.[0] ?? "";

    expect(launchdBlock).toContain("manual IBC restart required");
    expect(source).toContain("try { ib.disconnect(); } catch { /* ignore */ }");
    expect(source).toContain("scheduleReconnect();");
    expect(source).not.toContain("restart-secure-ibc-service.sh");
    expect(source).not.toContain("execSync(");
  });
});
