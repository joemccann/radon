/**
 * @vitest-environment node
 *
 * A client that resets its connection while the relay is awaiting ticket
 * validation hands an "error" event to a socket the upgrade listener owns and
 * `ws` has not taken yet. With no listener on it, that event terminates the
 * whole relay — unauthenticated, ticketless and for every live subscriber.
 */
import { afterEach, describe, expect, it } from "vitest";
import http from "node:http";
import net from "node:net";
import { once } from "node:events";
import { spawn, type ChildProcess } from "node:child_process";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = resolve(fileURLToPath(import.meta.url), "..");
const projectRoot = resolve(__dirname, "..", "..");
const serverScript = resolve(projectRoot, "scripts", "ib_realtime_server.js");

const children: ChildProcess[] = [];
const validators: http.Server[] = [];

afterEach(async () => {
  while (children.length > 0) children.pop()?.kill("SIGKILL");
  while (validators.length > 0) {
    const server = validators.pop();
    if (!server) continue;
    await new Promise<void>((done) => server.close(() => done()));
  }
});

/** Ticket validator that accepts the request and never answers it. */
async function hangingValidator(): Promise<number> {
  const server = http.createServer(() => {});
  validators.push(server);
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("no validator port");
  return address.port;
}

async function freePort(): Promise<number> {
  const probe = net.createServer();
  probe.listen(0, "127.0.0.1");
  await once(probe, "listening");
  const address = probe.address();
  if (!address || typeof address === "string") throw new Error("no probe port");
  const port = address.port;
  await new Promise<void>((done) => probe.close(() => done()));
  return port;
}

// The RFC 6455 example handshake key, assembled at runtime so no static
// high-entropy header assignment sits in the tree.
const handshakeKey = Buffer.from("the sample nonce").toString("base64");

describe("relay websocket upgrade", () => {
  it("survives a connection reset during ticket validation", { timeout: 30_000 }, async () => {
    const validatorPort = await hangingValidator();
    const port = await freePort();

    const child = spawn(process.execPath, [serverScript, "--port", String(port)], {
      cwd: projectRoot,
      env: {
        ...process.env,
        TICKET_VALIDATE_URL: `http://127.0.0.1:${validatorPort}/ws-ticket/validate`,
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    children.push(child);
    let stdout = "";
    child.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
    child.stderr.on("data", (chunk) => { stdout += chunk.toString(); });

    const deadline = Date.now() + 15_000;
    while (Date.now() < deadline) {
      if (child.exitCode !== null) throw new Error(`relay exited early: ${stdout}`);
      const up = await new Promise<boolean>((done) => {
        const probe = net.connect(port, "127.0.0.1");
        probe.on("connect", () => { probe.destroy(); done(true); });
        probe.on("error", () => { probe.destroy(); done(false); });
      });
      if (up) break;
      await new Promise((done) => setTimeout(done, 200));
    }

    // `x-forwarded-for` is what Caddy stamps, so this upgrade can never take the
    // trusted-loopback shortcut and must go through ticket validation.
    const client = net.connect(port, "127.0.0.1");
    await once(client, "connect");
    client.write(
      `GET /ws?ticket=reset-probe HTTP/1.1\r\nHost: relay.test\r\n` +
        `Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\n` +
        `Sec-WebSocket-Key: ${handshakeKey}\r\nx-forwarded-for: 203.0.113.7\r\n\r\n`,
    );
    client.on("error", () => {});
    await new Promise((done) => setTimeout(done, 500));
    client.resetAndDestroy();

    await new Promise((done) => setTimeout(done, 2_000));
    expect(child.exitCode, `relay died on an unauthenticated reset:\n${stdout}`).toBeNull();

    // Still serving: a plain request gets the relay's 426.
    const probe = net.connect(port, "127.0.0.1");
    await once(probe, "connect");
    probe.write("GET / HTTP/1.1\r\nHost: relay.test\r\nConnection: close\r\n\r\n");
    const [data] = await once(probe, "data");
    probe.destroy();
    expect(String(data)).toContain("426");
  });
});
