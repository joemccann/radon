import { runScript, type ScriptResult } from "../runner";
import { KellyOutput, type KellyInput } from "../schemas/kelly";
import type { Static } from "@sinclair/typebox";

export async function kelly(input: KellyInput): Promise<ScriptResult<Static<typeof KellyOutput>>> {
  if (!Number.isFinite(input.prob) || input.prob < 0 || input.prob > 1) {
    throw new RangeError("prob must be between 0 and 1");
  }
  if (!Number.isFinite(input.odds) || input.odds <= 0 || input.odds > 1_000) {
    throw new RangeError("odds must be greater than 0 and at most 1000");
  }
  if (input.fraction != null && (!Number.isFinite(input.fraction) || input.fraction <= 0 || input.fraction > 1)) {
    throw new RangeError("fraction must be greater than 0 and at most 1");
  }
  if (input.bankroll != null && (!Number.isFinite(input.bankroll) || input.bankroll < 0 || input.bankroll > 1_000_000_000_000)) {
    throw new RangeError("bankroll must be between 0 and 1000000000000");
  }
  const args = ["--prob", String(input.prob), "--odds", String(input.odds)];

  if (input.fraction != null) {
    args.push("--fraction", String(input.fraction));
  }
  if (input.bankroll != null) {
    args.push("--bankroll", String(input.bankroll));
  }

  return runScript("scripts/kelly.py", {
    args,
    outputSchema: KellyOutput,
  });
}
