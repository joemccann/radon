import type { ScriptResult } from "../runner";
import { postRadonJson } from "../api-client";
import { OrdersData, type IBOrdersInput } from "../schemas/ib-orders";
import type { Static } from "@sinclair/typebox";
import { Value } from "@sinclair/typebox/value";

/**
 * Sync IB orders through FastAPI, then return the Turso orders snapshot.
 */
export async function ibOrders(
  input: IBOrdersInput = {},
): Promise<ScriptResult<Static<typeof OrdersData>>> {
  void input;
  try {
    const data = await postRadonJson("/orders/refresh", 35_000);
    if (!Value.Check(OrdersData, data)) {
      const summary = [...Value.Errors(OrdersData, data)]
        .slice(0, 5)
        .map((e) => `${e.path}: ${e.message}`)
        .join("; ");
      return { ok: false, exitCode: 0, stderr: `Schema validation failed: ${summary}` };
    }
    return { ok: true, data: data as Static<typeof OrdersData> };
  } catch (error) {
    const stderr = error instanceof Error ? error.message : String(error);
    return { ok: false, exitCode: null, stderr };
  }
}
