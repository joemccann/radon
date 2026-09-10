import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { existsSync, mkdirSync, writeFileSync, chmodSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { runScript, resolveProjectRoot, resolvePythonBin, _resetRootCache } from "../runner";
import { hasPython313, python313Label } from "@/tests/helpers/python313";

describe("resolveProjectRoot", () => {
  beforeEach(() => _resetRootCache());

  it("returns a path containing scripts/ and data/", () => {
    const root = resolveProjectRoot();
    expect(root).toBeTruthy();
    expect(existsSync(join(root, "scripts"))).toBe(true);
    expect(existsSync(join(root, "data"))).toBe(true);
  });

  it("caches the result", () => {
    const first = resolveProjectRoot();
    const second = resolveProjectRoot();
    expect(first).toBe(second);
  });
});

describe("runScript", () => {
  it.skipIf(!hasPython313())(python313Label("returns ok: true with parsed JSON for a valid script"), async () => {
    const result = await runScript("scripts/kelly.py", {
      args: ["--prob", "0.6", "--odds", "2.0"],
      timeout: 10_000,
    });

    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.data).toHaveProperty("full_kelly_pct");
      expect(result.data).toHaveProperty("edge_exists");
      expect(result.data).toHaveProperty("recommendation");
    }
  });

  it("returns ok: false for a non-existent script", async () => {
    const result = await runScript("scripts/nonexistent.py");

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.stderr).toContain("not found");
    }
  });

  it.skipIf(!hasPython313())(python313Label("returns ok: false when script exits non-zero"), async () => {
    const result = await runScript("scripts/kelly.py", {
      args: ["--prob", "not-a-number"],
      timeout: 10_000,
    });

    expect(result.ok).toBe(false);
    if (!result.ok) {
      // A NUMBER, not null. `runner.ts` reports exitCode: null when the spawn
      // itself fails (no interpreter on PATH), so a bare `ok === false` was
      // satisfied by ENOENT and passed on a machine with no Python at all —
      // never once proving the non-zero-EXIT path it names. T-276.
      expect(typeof result.exitCode).toBe("number");
      expect(result.exitCode).not.toBe(0);
    }
  });

  it.skipIf(!hasPython313())(python313Label("supports rawOutput mode (no JSON parsing)"), async () => {
    const result = await runScript("scripts/kelly.py", {
      args: ["--prob", "0.6", "--odds", "2.0"],
      rawOutput: true,
      timeout: 10_000,
    });

    expect(result.ok).toBe(true);
    if (result.ok) {
      // rawOutput returns the string, not parsed JSON
      expect(typeof result.data).toBe("string");
      expect((result.data as string)).toContain("full_kelly_pct");
    }
  });

  it.skipIf(!hasPython313())(python313Label("returns schema validation error when output doesn't match schema"), async () => {
    const { Type } = await import("@sinclair/typebox");
    const WrongSchema = Type.Object({
      nonexistent_field: Type.String(),
    });

    const result = await runScript("scripts/kelly.py", {
      args: ["--prob", "0.6", "--odds", "2.0"],
      outputSchema: WrongSchema,
      timeout: 10_000,
    });

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.stderr).toContain("Schema validation failed");
    }
  });
});

// Regression coverage for the 2026-05-22 production outage where
// /api/ticker/ratings returned 502 because `runScript` spawned bare
// `python3.13`, which on Hetzner is the system interpreter and lacks
// every Radon dep (dotenv, ib_insync, ...). The venv at <root>/.venv
// is the only Python with deps installed. `resolvePythonBin` must
// pick it up.
describe("resolvePythonBin", () => {
  let scratchRoot: string;

  beforeEach(() => {
    _resetRootCache();
    scratchRoot = join(tmpdir(), `radon-runner-test-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    mkdirSync(scratchRoot, { recursive: true });
    delete process.env.RADON_PYTHON_BIN;
  });

  afterEach(() => {
    _resetRootCache();
    delete process.env.RADON_PYTHON_BIN;
    if (scratchRoot && existsSync(scratchRoot)) {
      rmSync(scratchRoot, { recursive: true, force: true });
    }
  });

  it("falls back to 'python3.13' on PATH when no env override is set", () => {
    expect(resolvePythonBin(scratchRoot)).toBe("python3.13");
  });

  it("honors RADON_PYTHON_BIN env override when the file exists", () => {
    const override = join(scratchRoot, "custom-python");
    writeFileSync(override, "#!/bin/sh\nexit 0\n");
    chmodSync(override, 0o755);
    process.env.RADON_PYTHON_BIN = override;

    expect(resolvePythonBin(scratchRoot)).toBe(override);
  });

  it("ignores RADON_PYTHON_BIN when target file is missing", () => {
    process.env.RADON_PYTHON_BIN = "/nonexistent/python";

    expect(resolvePythonBin(scratchRoot)).toBe("python3.13");
  });
});

// Sanity test: the live repo root resolves to "python3.13" unless
// RADON_PYTHON_BIN is set to an existing file. Tests run without that
// env so the result must be the bare interpreter name.
describe("resolvePythonBin (live tree)", () => {
  beforeEach(() => {
    _resetRootCache();
    delete process.env.RADON_PYTHON_BIN;
  });

  it("returns 'python3.13' by default (RADON_PYTHON_BIN unset)", () => {
    expect(resolvePythonBin(resolveProjectRoot())).toBe("python3.13");
  });
});
