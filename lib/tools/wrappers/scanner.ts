import { runScript, type ScriptResult } from "../runner";
import { ScannerOutput, type ScannerInput } from "../schemas/scanner";
import type { Static } from "@sinclair/typebox";

export async function scanner(
  input: ScannerInput = {},
): Promise<ScriptResult<Static<typeof ScannerOutput>>> {
  const args: string[] = [];

  if (input.top != null) {
    if (!Number.isInteger(input.top) || input.top < 1 || input.top > 500) {
      throw new RangeError("top must be an integer between 1 and 500");
    }
    args.push("--top", String(input.top));
  }
  if (input.minScore != null) {
    args.push("--min-score", String(input.minScore));
  }

  return runScript("scripts/scanner.py", {
    args,
    timeout: 120_000, // Scanner iterates the full watchlist
    outputSchema: ScannerOutput,
  });
}
