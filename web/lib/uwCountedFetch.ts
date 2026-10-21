/** Every web UW request is admitted by the same account-wide Python governor. */
import { radonFetch } from "@/lib/radonApi";

export async function countedUwFetch(input: string | URL, init?: RequestInit): Promise<Response> {
  const url = new URL(input.toString());
  if (url.origin !== "https://api.unusualwhales.com" || !url.pathname.startsWith("/api/") || url.username || url.password || url.hash) {
    throw new Error("Unsupported research provider URL");
  }
  if (init?.method && init.method !== "GET") throw new Error("Research requests are read-only");
  const result = await radonFetch<{status: number; data: unknown}>("/uw/request", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ endpoint: url.pathname.slice(5), params: Object.fromEntries(url.searchParams) }),
    signal: init?.signal ?? undefined, timeout: 20_000,
  });
  return Response.json(result.data, { status: result.status });
}
