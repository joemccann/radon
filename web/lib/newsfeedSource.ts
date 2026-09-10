import { withoutEmDashes } from "./copyPunctuation";

/** Research media stays on the authenticated same-origin path. */
export type ResearchSource = {
  kind: "dropbox";
  publisher: string;
  url: string;
  documentDate: string;
  folderDate: string;
  pages: number[];
  figures: { url: string; page: number; caption: string }[];
  fileId: string;
  revision: string;
  contentHash: string;
};
export const PRIVATE_RESEARCH_ASSET = /^\/api\/newsfeed\/research\/files\/[a-f0-9]{64}\.(?:png|pdf)(?:#page=[1-9]\d*)?$/;
export function parseResearchSource(value: unknown): ResearchSource | undefined {
  let candidate = value;
  if (typeof candidate === "string") {
    try { candidate = JSON.parse(candidate); } catch { return undefined; }
  }
  if (!candidate || typeof candidate !== "object") return undefined;
  const s = candidate as ResearchSource;
  if (s.kind !== "dropbox" || typeof s.publisher !== "string" || !s.publisher.trim()
    || typeof s.url !== "string" || !PRIVATE_RESEARCH_ASSET.test(s.url)
    || !s.url.includes(".pdf") || typeof s.documentDate !== "string"
    || !/^\d{4}-\d{2}-\d{2}$/.test(s.documentDate)
    || typeof s.folderDate !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(s.folderDate) || typeof s.fileId !== "string"
    || typeof s.revision !== "string" || typeof s.contentHash !== "string"
    || !Array.isArray(s.pages) || !s.pages.every(p => Number.isInteger(p) && p > 0)
    || !Array.isArray(s.figures) || !s.figures.every(f => f && typeof f.url === "string"
      && PRIVATE_RESEARCH_ASSET.test(f.url) && f.url.endsWith(".png")
      && Number.isInteger(f.page) && f.page > 0 && s.pages.includes(f.page) && typeof f.caption === "string")) return undefined;
  return {
    ...s,
    publisher: withoutEmDashes(s.publisher),
    figures: s.figures.map(figure => ({ ...figure, caption: withoutEmDashes(figure.caption) })),
  };
}

/** Credits are keyed by the displayed chart URL, never by array position. */
export function parseImageSources(value: unknown, images: readonly string[]): Record<string, string> {
  let candidate = value;
  if (typeof candidate === "string") {
    try { candidate = JSON.parse(candidate); } catch { return {}; }
  }
  if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) return {};
  const allowed = new Set(images);
  return Object.fromEntries(Object.entries(candidate).flatMap(([url, source]) =>
    allowed.has(url) && typeof source === "string" && source.trim()
      ? [[url, withoutEmDashes(source.trim())]] : []));
}

export function getImageSource(
  post: { images?: string[]; imageSources?: Record<string, string> },
  imageUrl: string | null | undefined,
): string | undefined {
  if (!imageUrl || !post.images?.includes(imageUrl)) return undefined;
  const source = post.imageSources?.[imageUrl];
  return typeof source === "string" && source.trim() ? source.trim() : undefined;
}
