import { afterEach, describe, expect, it } from "vitest";
import { mkdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { Type } from "@sinclair/typebox";
import { readDataFile } from "../data-reader";
import { resolveProjectRoot } from "../runner";

// readDataFile resolves paths against resolveProjectRoot(), so fixtures MUST be
// written under that root — NOT a `process.cwd()`-relative path. When the suite
// runs from a non-root cwd (e.g. `web/`) the two diverge and every read 404s.
const TEST_DIR = "tmp/data-reader-tests";
const ABS_TEST_DIR = join(resolveProjectRoot(), TEST_DIR);

async function writeJsonFixture(name: string, data: unknown): Promise<string> {
  await mkdir(ABS_TEST_DIR, { recursive: true });
  await writeFile(join(ABS_TEST_DIR, name), JSON.stringify(data), "utf8");
  return `${TEST_DIR}/${name}`; // root-relative path, as readDataFile expects
}

afterEach(async () => {
  await rm(ABS_TEST_DIR, { recursive: true, force: true });
});

describe("readDataFile", () => {
  it("reads an existing JSON file", async () => {
    const path = await writeJsonFixture("valid.json", { tickers: ["AAPL"] });

    const result = await readDataFile(path);

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data).toEqual({ tickers: ["AAPL"] });
    }
  });

  it("returns error for missing file", async () => {
    const result = await readDataFile(`${TEST_DIR}/missing.json`);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toContain("not found");
    }
  });

  it("validates against schema when provided", async () => {
    const path = await writeJsonFixture("portfolio-shaped.json", {
      bankroll: 100000,
      positions: [],
    });
    const Schema = Type.Object({
      bankroll: Type.Number(),
      positions: Type.Array(Type.Unknown()),
    });

    const result = await readDataFile(path, Schema);

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data.bankroll).toBe(100000);
    }
  });

  it("returns validation error when data does not match schema", async () => {
    const path = await writeJsonFixture("invalid-shape.json", { tickers: ["AAPL"] });
    const StrictSchema = Type.Object({
      bankroll: Type.Number(),
      impossible_field_xyz: Type.String(),
    });

    const result = await readDataFile(path, StrictSchema);

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toContain("Validation failed");
    }
  });

  it("returns error for file with invalid JSON", async () => {
    await mkdir(ABS_TEST_DIR, { recursive: true });
    await writeFile(join(ABS_TEST_DIR, "invalid-json.json"), "{not-json", "utf8");

    const result = await readDataFile(`${TEST_DIR}/invalid-json.json`);

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toBeTruthy();
    }
  });
});
