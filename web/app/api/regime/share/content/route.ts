import { NextRequest, NextResponse } from "next/server";
import { isAllowedShareCardPath, readShareCard } from "@/lib/shareReportPath";

export const runtime = "nodejs";

export async function GET(req: NextRequest): Promise<Response> {
  const rawPath = req.nextUrl.searchParams.get("path");
  if (!rawPath) {
    return NextResponse.json({ error: "Missing path parameter" }, { status: 400 });
  }

  // Only public share cards for this surface may be served (see lib/shareReportPath).
  if (!isAllowedShareCardPath(rawPath, "regime")) {
    return NextResponse.json({ error: "Access denied" }, { status: 403 });
  }

  const html = await readShareCard(rawPath, "regime");
  if (html === null) {
    return NextResponse.json({ error: "File not found" }, { status: 404 });
  }

  return new Response(html, {
    status: 200,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
}
