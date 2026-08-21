import { markdownRouteResult } from "@/lib/machine-routes";

export const dynamic = "force-static";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ slug?: string[] }> },
) {
  const { slug } = await params;
  const result = markdownRouteResult(slug);
  return new Response(result.body, {
    status: result.status,
    headers: result.headers,
  });
}
