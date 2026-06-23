import type { ScriptResult } from "../runner";
import { postRadonJson } from "../api-client";
import { PortfolioData, type IBSyncInput } from "../schemas/ib-sync";
import type { Static } from "@sinclair/typebox";
import { Value } from "@sinclair/typebox/value";

/**
 * Sync IB portfolio through FastAPI, then return the latest Turso snapshot.
 */
export async function ibSync(
  input: IBSyncInput = {},
): Promise<ScriptResult<Static<typeof PortfolioData>>> {
  void input;
  try {
    const data = await postRadonJson("/portfolio/sync", 35_000);
    if (!Value.Check(PortfolioData, data)) {
      const summary = [...Value.Errors(PortfolioData, data)]
        .slice(0, 5)
        .map((e) => `${e.path}: ${e.message}`)
        .join("; ");
      return { ok: false, exitCode: 0, stderr: `Schema validation failed: ${summary}` };
    }
    return { ok: true, data: data as Static<typeof PortfolioData> };
  } catch (error) {
    const stderr = error instanceof Error ? error.message : String(error);
    return { ok: false, exitCode: null, stderr };
  }
}
