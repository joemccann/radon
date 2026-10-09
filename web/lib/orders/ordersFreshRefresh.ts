/** Place, cancel, and modify. Page-load POST /api/orders must not send this. */
export const ORDERS_FRESH_HEADER = "X-Radon-Orders-Fresh";

export function ordersFreshRefreshInit(): {
  method: "POST";
  timeout: number;
  headers: Record<string, string>;
} {
  return {
    method: "POST",
    timeout: 10_000,
    headers: { [ORDERS_FRESH_HEADER]: "1" },
  };
}
