import { NextResponse } from "next/server";
import { requireRouteAccess } from "@/lib/routeAccess";
import { radonFetch } from "@/lib/radonApi";
import { getRequestId, setNoStoreResponseHeaders } from "@/lib/apiContracts";
export const dynamic = "force-dynamic";
export const radonCapability = "read";
export async function GET(request: Request): Promise<Response> {
  const access = await requireRouteAccess(request, {operatorOnly: true});
  if (!access.ok) return access.response;
  try {
    return setNoStoreResponseHeaders(NextResponse.json(await radonFetch("/uw/capabilities",{token:access.principal.token,timeout:10_000})),getRequestId());
  } catch {
    return setNoStoreResponseHeaders(NextResponse.json({error:"Research capability state could not be loaded."},{status:503}),getRequestId());
  }
}
