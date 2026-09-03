import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";

// Drift guard: every `code: "..."` literal emitted by the setup API routes
// (and middleware) must appear in the ErrorCode union source. A new literal
// shipped without extending the union reds this test before tsc/CI does.

const webRoot = path.resolve(__dirname, "..");

function tsFilesUnder(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = path.join(dir, name);
    if (statSync(p).isDirectory()) out.push(...tsFilesUnder(p));
    else if (p.endsWith(".ts") || p.endsWith(".tsx")) out.push(p);
  }
  return out;
}

function codeLiterals(src: string): string[] {
  return [...src.matchAll(/\bcode:\s*"([A-Z0-9_]+)"/g)].map((m) => m[1]);
}

const unionSource = readFileSync(path.join(webRoot, "lib/apiContracts.ts"), "utf8");
// End the capture at `";` (closing quote + semicolon) so semicolons inside
// comments within the union body do not truncate the parse.
const unionMatch = unionSource.match(/export type ErrorCode =([\s\S]*?");/);
const unionCodes = new Set(
  [...(unionMatch?.[1] ?? "").matchAll(/"([A-Z0-9_]+)"/g)].map((m) => m[1]),
);

describe("ErrorCode contract", () => {
  it("parses the ErrorCode union", () => {
    expect(unionCodes.size).toBeGreaterThan(0);
  });

  const files = [
    ...tsFilesUnder(path.join(webRoot, "app/api/setup")),
    path.join(webRoot, "middleware.ts"),
  ];

  it("every emitted code literal under app/api/setup (and middleware) is in the union", () => {
    for (const file of files) {
      for (const code of codeLiterals(readFileSync(file, "utf8"))) {
        expect(unionCodes, `${path.relative(webRoot, file)} emits "${code}"`).toContain(code);
      }
    }
  });
});
