export const X_SHARE_FOOTER = "@grok explain this for normies";

/** Keep the requested footer as a separate final paragraph, including on re-export. */
export function withXShareFooter(text: string): string {
  const body = text.trimEnd();
  return body === X_SHARE_FOOTER || body.endsWith(`\n${X_SHARE_FOOTER}`)
    ? body
    : [body, X_SHARE_FOOTER].filter(Boolean).join("\n\n");
}
