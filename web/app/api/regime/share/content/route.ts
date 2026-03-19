import { NextRequest, NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { resolve } from "path";

export const runtime = "nodejs";

const PROJECT_ROOT = resolve(process.cwd(), "..");
const REPORTS_DIR = resolve(PROJECT_ROOT, "reports");

export async function GET(req: NextRequest): Promise<Response> {
  const rawPath = req.nextUrl.searchParams.get("path");
  if (!rawPath) {
    return NextResponse.json({ error: "Missing path parameter" }, { status: 400 });
  }

  // Security: resolve and ensure it's within the reports directory
  const resolved = resolve(rawPath);
  if (!resolved.startsWith(REPORTS_DIR)) {
    return NextResponse.json({ error: "Access denied" }, { status: 403 });
  }

  try {
    const html = await readFile(resolved, "utf-8");
    return new Response(html, {
      status: 200,
      headers: { "Content-Type": "text/html; charset=utf-8" },
    });
  } catch {
    return NextResponse.json({ error: "File not found" }, { status: 404 });
  }
}
