import { NextResponse, type NextRequest } from "next/server";
import { appendVaryAccept } from "./lib/accept";
import { negotiate } from "./lib/negotiate";

export function middleware(req: NextRequest) {
  const pathname = req.nextUrl.pathname;
  const decision = negotiate(pathname, req.headers.get("accept"));

  if (decision.action === "rewrite") {
    const url = req.nextUrl.clone();
    url.pathname = decision.pathname;
    const rewritten = NextResponse.rewrite(url);
    appendVaryAccept(rewritten.headers);
    return rewritten;
  }

  if (decision.action === "406") {
    return new NextResponse(decision.body, {
      status: 406,
      headers: {
        "Content-Type": "text/plain; charset=utf-8",
        Vary: "Accept, Accept-Encoding",
      },
    });
  }

  const res = NextResponse.next();
  appendVaryAccept(res.headers);
  return res;
}

export const config = {
  matcher: ["/((?!api/|_next/|_vercel/).*)"],
};
