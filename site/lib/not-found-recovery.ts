import { SITE_NAME, siteUrl } from "./seo";

export type RecoveryLink = {
  href: string;
  label: string;
  note: string;
};

export const NOT_FOUND_RECOVERY_LINKS: RecoveryLink[] = [
  {
    href: `${siteUrl}/sitemap.xml`,
    label: "Sitemap",
    note: "every public URL",
  },
  {
    href: `${siteUrl}/llms.txt`,
    label: "llms.txt",
    note: "agent index and when-to-use guidance",
  },
  {
    href: `${siteUrl}/developers`,
    label: "Radon Terminal developer resources",
    note: "OpenAPI, auth, MCP, webhooks",
  },
  {
    href: `${siteUrl}/agent-instructions`,
    label: "Agent instructions",
    note: "when to use Radon and how to call it",
  },
  {
    href: siteUrl,
    label: "Home",
    note: "the method, models, and strategy registry",
  },
];

export function notFoundMarkdown(): string {
  const links = NOT_FOUND_RECOVERY_LINKS.map(
    (link) => `- [${link.label}](${link.href}): ${link.note}`,
  );
  return [
    "# Page not found",
    "",
    `This path is not published by ${SITE_NAME}.`,
    "",
    "## Where to look next",
    "",
    ...links,
    "",
  ].join("\n");
}
