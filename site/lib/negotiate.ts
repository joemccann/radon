import { preferredType } from "./accept";

export const NOT_ACCEPTABLE_BODY =
  "Not Acceptable\n\nAvailable: text/html, text/markdown\n";

export type NegotiateResult =
  | { action: "rewrite"; pathname: string }
  | { action: "406"; body: string }
  | { action: "next" };

function lastSegment(pathname: string): string {
  const trimmed = pathname.endsWith("/") && pathname !== "/"
    ? pathname.slice(0, -1)
    : pathname;
  return trimmed.split("/").pop() ?? "";
}

export function shouldNegotiate(pathname: string): boolean {
  if (pathname.startsWith("/api/")) return false;
  if (pathname.startsWith("/_next/")) return false;
  if (pathname.startsWith("/_vercel/")) return false;
  if (pathname.endsWith(".md")) return true;
  const segment = lastSegment(pathname);
  return !segment.includes(".");
}

export function markdownApiPath(pathname: string): string {
  const withoutMd = pathname.endsWith(".md")
    ? pathname.slice(0, -3) || "/"
    : pathname;
  const normalized = withoutMd === "/" ? "" : withoutMd.replace(/\/$/, "");
  return `/api/markdown${normalized}`;
}

export function negotiate(
  pathname: string,
  acceptHeader: string | null,
): NegotiateResult {
  if (pathname.endsWith(".md")) {
    return { action: "rewrite", pathname: markdownApiPath(pathname) };
  }
  if (!shouldNegotiate(pathname)) {
    return { action: "next" };
  }

  const chosen = preferredType(acceptHeader);
  if (chosen === "text/markdown") {
    return { action: "rewrite", pathname: markdownApiPath(pathname) };
  }
  if (chosen === null && acceptHeader) {
    return { action: "406", body: NOT_ACCEPTABLE_BODY };
  }
  return { action: "next" };
}
