import { readFile } from "fs/promises";
import { join } from "path";

let cached: { regular: Buffer; bold: Buffer } | null = null;

/**
 * Both faces, or neither.
 *
 * `fontRegular` used to be assigned BEFORE the Bold read. When the Bold read
 * rejected, the regular was already cached, so every later call skipped the
 * load block entirely and returned a Bold entry with `data: null` — for the
 * lifetime of the process, from one transient failure. Reading both first and
 * assigning the pair only on full success makes the failure retryable. R-311.
 */
export async function loadFonts() {
  if (!cached) {
    const dir = join(process.cwd(), "public", "fonts");
    const [regular, bold] = await Promise.all([
      readFile(join(dir, "IBMPlexMono-Regular.woff")),
      readFile(join(dir, "IBMPlexMono-Bold.woff")),
    ]);
    cached = { regular, bold };
  }
  return [
    {
      name: "IBM Plex Mono",
      data: cached.regular,
      weight: 400 as const,
      style: "normal" as const,
    },
    {
      name: "IBM Plex Mono",
      data: cached.bold,
      weight: 700 as const,
      style: "normal" as const,
    },
  ];
}
