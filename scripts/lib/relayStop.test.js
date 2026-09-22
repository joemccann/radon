import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const serverPath = resolve(dirname(fileURLToPath(import.meta.url)), "../ib_realtime_server.js");

function uncommented(source) {
  return source
    .split("\n")
    .filter((line) => !line.trim().startsWith("//") && !line.trim().startsWith("*"))
    .join("\n");
}

describe("relay stop", () => {
  it("does not install a SIGTERM handler that can block process exit", () => {
    const source = uncommented(readFileSync(serverPath, "utf8"));
    expect(source).not.toContain('process.on("SIGTERM"');
    expect(source).not.toContain('process.on("SIGINT"');
    expect(source).not.toContain("process.on('SIGTERM'");
    expect(source).not.toContain("process.on('SIGINT'");
  });
});
