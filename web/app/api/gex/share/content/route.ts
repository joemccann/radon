import { NextRequest, NextResponse } from "next/server";
import { basename } from "path";
import { isAllowedShareCardPath } from "@/lib/shareReportPath";
import { radonFetchText, RadonApiError } from "@/lib/radonApi";

export const runtime = "nodejs";

export async function GET(req: NextRequest): Promise<Response> {
  const rawPath = req.nextUrl.searchParams.get("path");
  if (!rawPath) {
    return NextResponse.json({ error: "Missing path parameter" }, { status: 400 });
  }

  // Only public share cards for this surface may be served (see lib/shareReportPath).
  if (!isAllowedShareCardPath(rawPath, "gex")) {
    return NextResponse.json({ error: "Access denied" }, { status: 403 });
  }

  let html: string;
  try {
    html = await radonFetchText(`/share/content?type=gex&name=${encodeURIComponent(basename(rawPath))}`);
  } catch (error) {
    const status = error instanceof RadonApiError ? error.status : 502;
    return NextResponse.json({ error: status === 404 ? "File not found" : "Preview unavailable" }, { status });
  }

  return new Response(html, {
    status: 200,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
}
