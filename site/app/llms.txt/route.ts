import { llmsTxtRouteResult } from "@/lib/machine-routes";

export const dynamic = "force-static";

export function GET() {
  const result = llmsTxtRouteResult();
  return new Response(result.body, {
    status: result.status,
    headers: result.headers,
  });
}
