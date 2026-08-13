import { afterEach, describe, expect, it } from "vitest";
import net from "node:net";
import { once } from "node:events";
import { spawn } from "node:child_process";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = resolve(fileURLToPath(import.meta.url), "..");
const webDir = resolve(__dirname, "..");
const projectRoot = resolve(webDir, "..");
const serverScript = resolve(projectRoot, "scripts", "ib_realtime_server.js");

const occupiedServers: net.Server[] = [];

afterEach(async () => {
  while (occupiedServers.length > 0) {
    const server = occupiedServers.pop();
    if (!server) continue;
    await new Promise<void>((resolveClose, rejectClose) => {
      server.close((error) => {
        if (error) {
          rejectClose(error);
          return;
        }
        resolveClose();
      });
    });
  }
});

async function occupyPort() {
  const server = net.createServer();
  occupiedServers.push(server);
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (!address || typeof address === "string") {
    throw new Error("Expected TCP address info for occupied test port");
  }
  return address.port;
}

describe("ib realtime server startup", () => {
  it("exits cleanly when the websocket port is already in use", { timeout: 15_000 }, async () => {
    const port = await occupyPort();

    const child = spawn(process.execPath, [serverScript, "--port", String(port)], {
      cwd: projectRoot,
      env: process.env,
      stdio: ["ignore", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
    child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });

    let timeout: ReturnType<typeof setTimeout> | undefined;
    const [code] = await Promise.race([
      once(child, "exit"),
      new Promise((_, reject) => {
        timeout = setTimeout(() => {
          child.kill("SIGTERM");
          reject(new Error("Timed out waiting for ib_realtime_server.js to exit"));
        }, 10_000);
      }),
    ]).finally(() => {
      if (timeout) clearTimeout(timeout);
    });

    expect(code).toBe(0);
    expect(stdout).toContain(`WebSocket port already in use at ws://127.0.0.1:${port}`);
    expect(stdout).toContain("skipping duplicate startup");
    expect(stderr).not.toContain("EADDRINUSE");
  });
});
