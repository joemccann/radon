import { openApiRouteResult } from "@/lib/machine-routes";

export const dynamic = "force-static";

export function GET() {
  const result = openApiRouteResult();
  return new Response(result.body, {
    status: result.status,
    headers: result.headers,
  });
}
