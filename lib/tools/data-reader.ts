/**
 * Typed data file reader for JSON files in the data/ directory.
 */

import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { Value } from "@sinclair/typebox/value";
import type { TSchema, Static } from "@sinclair/typebox";
import { resolveProjectRoot } from "./runner";

export type ReadResult<T> = { ok: true; data: T } | { ok: false; error: string };

/**
 * Read and parse a JSON data file, optionally validating against a TypeBox schema.
 *
 * @param relativePath  Path relative to project root.
 * @param schema        Optional TypeBox schema for validation.
 */
export async function readDataFile<S extends TSchema>(
  relativePath: string,
  schema?: S,
): Promise<ReadResult<Static<S>>> {
  const root = resolveProjectRoot();
  const filePath = path.join(root, relativePath);

  if (!existsSync(filePath)) {
    return { ok: false, error: `File not found: ${relativePath}` };
  }

  try {
    const content = await readFile(filePath, "utf8");
    const parsed = JSON.parse(content);

    if (schema && !Value.Check(schema, parsed)) {
      const errors = [...Value.Errors(schema, parsed)];
      const summary = errors
        .slice(0, 5)
        .map((e) => `${e.path}: ${e.message}`)
        .join("; ");
      return { ok: false, error: `Validation failed: ${summary}` };
    }

    return { ok: true, data: parsed };
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    return { ok: false, error: message };
  }
}
