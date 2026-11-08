/**
 * Universal Python script runner.
 *
 * Single implementation of spawn-and-parse replacing 5+ duplicated versions
 * across API routes and PI extensions.
 */

import { spawn, type ChildProcess } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { Value } from "@sinclair/typebox/value";
import type { TSchema, Static } from "@sinclair/typebox";

// ── Result types ──────────────────────────────────────────────────────

export type ScriptSuccess<T> = { ok: true; data: T };
export type ScriptFailure = {
  ok: false;
  exitCode: number | null;
  stderr: string;
  /** The runner killed the script at `timeout`; not the script's own failure. */
  timedOut?: true;
  /** stdout passed `maxOutput` and was cut, so it was never parsed. */
  truncated?: true;
};
export type ScriptResult<T> = ScriptSuccess<T> | ScriptFailure;

// ── Options ───────────────────────────────────────────────────────────

export interface RunScriptOptions<S extends TSchema | undefined = undefined> {
  /** CLI arguments passed after the script path. */
  args?: string[];
  /** Working directory (defaults to resolveProjectRoot()). */
  cwd?: string;
  /** Timeout in ms before SIGKILL. Default: 30 000. */
  timeout?: number;
  /** TypeBox schema to validate parsed JSON output against. */
  outputSchema?: S;
  /** If true, spawn in detached mode (fire-and-forget). */
  detached?: boolean;
  /** Max stdout chars to keep. Default: 200 000. */
  maxOutput?: number;
  /** If true, skip JSON parsing of stdout. Use for scripts that write to files instead. */
  rawOutput?: boolean;
}

// ── Project root resolution ───────────────────────────────────────────

let _cachedRoot: string | null = null;

/**
 * Walk up from cwd looking for the project root.
 * Checks for `scripts/` AND `data/` directories.
 * Result is cached for the process lifetime.
 */
export function resolveProjectRoot(): string {
  if (_cachedRoot) return _cachedRoot;

  const candidates = [
    process.cwd(),
    path.resolve(process.cwd(), ".."),
    path.resolve(process.cwd(), "..", ".."),
  ];

  for (const candidate of candidates) {
    if (
      existsSync(path.join(candidate, "scripts")) &&
      existsSync(path.join(candidate, "data"))
    ) {
      _cachedRoot = candidate;
      return candidate;
    }
  }

  // Fallback — best effort
  return process.cwd();
}

/** Reset cached root (useful for tests). */
export function _resetRootCache(): void {
  _cachedRoot = null;
  _cachedPythonBin = null;
}

// ── Python binary resolution ──────────────────────────────────────────

let _cachedPythonBin: string | null = null;

/**
 * Resolve the Python interpreter to spawn.
 *
 * Order:
 *   1. `RADON_PYTHON_BIN` env override (set on Hetzner via
 *      radon-nextjs.service to point at the venv interpreter)
 *   2. `python3.13` on PATH (laptop dev fallback — system Python has deps)
 *
 * Why not look up `<root>/.venv/bin/python3.13` from source? Turbopack's
 * static analyzer follows string literals that look like file paths and
 * attempts to resolve them at build time. On Hetzner the .venv binaries
 * are symlinks to /usr/bin/python3.13, which lives outside the project
 * root and trips a "Symlink .venv/bin/python3 is invalid" build error.
 * Keeping the venv path out of source and in the systemd EnvironmentFile
 * sidesteps the analyzer entirely. The `cwd` param is retained for API
 * stability with `runScript` and tests.
 */
export function resolvePythonBin(_cwd: string): string {
  if (_cachedPythonBin) return _cachedPythonBin;

  const envOverride = process.env.RADON_PYTHON_BIN;
  if (envOverride && existsSync(envOverride)) {
    _cachedPythonBin = envOverride;
    return envOverride;
  }

  _cachedPythonBin = "python3.13";
  return "python3.13";
}

// ── Runner ────────────────────────────────────────────────────────────

/**
 * Spawn a Python script, collect stdout/stderr, parse JSON, optionally validate.
 *
 * @param scriptPath  Relative path from project root (e.g. "scripts/kelly.py").
 * @param opts        See {@link RunScriptOptions}.
 * @returns           Discriminated union: `{ ok, data }` or `{ ok, exitCode, stderr }`.
 */
export function runScript<T = unknown, S extends TSchema | undefined = undefined>(
  scriptPath: string,
  opts: RunScriptOptions<S> = {},
): Promise<ScriptResult<S extends TSchema ? Static<S> : T>> {
  const {
    args = [],
    cwd = resolveProjectRoot(),
    timeout = 30_000,
    outputSchema,
    detached = false,
    maxOutput = 200_000,
    rawOutput = false,
  } = opts;

  return new Promise((resolve) => {
    const fullPath = path.join(cwd, scriptPath);

    // Fail fast if script doesn't exist
    if (!existsSync(fullPath)) {
      resolve({
        ok: false,
        exitCode: null,
        stderr: `Script not found: ${fullPath}`,
      });
      return;
    }

    const pythonBin = resolvePythonBin(cwd);
    const proc: ChildProcess = spawn(pythonBin, [scriptPath, ...args], {
      cwd,
      env: process.env,
      detached,
      stdio: ["ignore", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    let truncated = false;
    let timedOut = false;

    proc.stdout?.on("data", (chunk: Buffer) => {
      if (truncated) return;
      const text = chunk.toString();
      if (stdout.length + text.length <= maxOutput) {
        stdout += text;
      } else {
        // Keep nothing past the cap: a later small chunk must not be spliced
        // onto a prefix with a hole in it.
        truncated = true;
      }
    });

    proc.stderr?.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
    });

    const timer = setTimeout(() => {
      timedOut = true;
      proc.kill("SIGKILL");
    }, timeout);

    proc.on("error", (err: Error) => {
      clearTimeout(timer);
      resolve({ ok: false, exitCode: null, stderr: err.message });
    });

    proc.on("close", (code: number | null) => {
      clearTimeout(timer);

      if (timedOut) {
        resolve({
          ok: false,
          exitCode: code,
          stderr: `Script timed out after ${timeout}ms${stderr ? `: ${stderr}` : ""}`,
          timedOut: true,
        });
        return;
      }

      if (code !== 0) {
        resolve({ ok: false, exitCode: code, stderr });
        return;
      }

      // Skip JSON parsing for scripts that write to files instead of stdout
      if (rawOutput) {
        resolve({ ok: true, data: stdout as S extends TSchema ? Static<S> : T });
        return;
      }

      // A cut document is not malformed JSON; say what actually happened.
      if (truncated) {
        resolve({
          ok: false,
          exitCode: code,
          stderr: `Output exceeded ${maxOutput} chars and was truncated: ${stdout.slice(0, 500)}`,
          truncated: true,
        });
        return;
      }

      // Parse JSON from stdout
      let parsed: unknown;
      try {
        parsed = JSON.parse(stdout);
      } catch {
        resolve({
          ok: false,
          exitCode: code,
          stderr: `Invalid JSON output: ${stdout.slice(0, 500)}`,
        });
        return;
      }

      // Optional schema validation
      if (outputSchema) {
        if (!Value.Check(outputSchema, parsed)) {
          const errors = [...Value.Errors(outputSchema, parsed)];
          const summary = errors
            .slice(0, 5)
            .map((e) => `${e.path}: ${e.message}`)
            .join("; ");
          resolve({
            ok: false,
            exitCode: code,
            stderr: `Schema validation failed: ${summary}`,
          });
          return;
        }
      }

      resolve({ ok: true, data: parsed as S extends TSchema ? Static<S> : T });
    });

    if (detached) {
      proc.unref();
    }
  });
}
