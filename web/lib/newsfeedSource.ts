import { withoutEmDashes } from "./copyPunctuation";
import { parseChartPlans, type ChartPlan } from "./planCharts";

/** Research media stays on the authenticated same-origin path. */
export type ResearchSource = DropboxResearchSource | MarketDeskResearchSource;

/** MarketDesk article provenance (SPEC 4.6). Figures carry no page number. */
export type MarketDeskResearchSource = {
  kind: "marketdesk";
  publisher: string;
  articleId: string;
  articleUrl: string;
  contentHash: string;
  documentDate: string;
  folderDate: string;
  pages: number[];
  url: string;
  figures: { url: string; caption: string }[];
  summary?: string[];
  extraImages?: string[];
};

export type DropboxResearchSource = {
  kind: "dropbox";
  publisher: string;
  url: string;
  /** Original-page extracted passages and tables; unavailable for legacy PDFs. */
  evidenceUrl?: string;
  documentDate: string;
  folderDate: string;
  pages: number[];
  figures: { url: string; page: number; caption: string; generated?: boolean; pages?: number[] }[];
  fileId: string;
  revision: string;
  contentHash: string;
  /** Chart plan for a text-only finding. Absent when the post has source figures. */
  charts?: ChartPlan[];
  /** Presence disables unreviewed client synthesis, including failed attempts. */
  chartGeneration?: {
    version: number;
    status: "passed" | "rejected" | "unavailable" | "no_comparable_data";
    charts: unknown[];
    attempts: unknown[];
  };
};
export const PRIVATE_RESEARCH_ASSET = /^\/api\/newsfeed\/research\/files\/[a-f0-9]{64}\.(?:png|pdf)(?:#page=[1-9]\d*)?$/;
export function parseResearchSource(value: unknown): ResearchSource | undefined {
  let candidate = value;
  if (typeof candidate === "string") {
    try { candidate = JSON.parse(candidate); } catch { return undefined; }
  }
  if (!candidate || typeof candidate !== "object") return undefined;
  if ((candidate as { kind?: unknown }).kind === "marketdesk") return parseMarketDeskSource(candidate as MarketDeskResearchSource);
  const s = candidate as DropboxResearchSource;
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
  if (s.evidenceUrl !== undefined && (typeof s.evidenceUrl !== "string"
    || !/^\/api\/newsfeed\/research\/files\/[a-f0-9]{64}\.json$/.test(s.evidenceUrl))) return undefined;
  const charts = parseChartPlans(s.charts);
  const rest = { ...s };
  delete rest.charts;
  return {
    ...rest,
    publisher: withoutEmDashes(s.publisher),
    figures: s.figures.map(figure => ({ ...figure, caption: withoutEmDashes(figure.caption) })),
    ...(charts ? { charts } : {}),
  };
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const isPrivatePng = (url: unknown): url is string =>
  typeof url === "string" && PRIVATE_RESEARCH_ASSET.test(url) && url.endsWith(".png");

/** Python's len(): the publisher validates bullets in code points, so astral characters count once. */
const codePoints = (text: string): number => Array.from(text).length;

function parseMarketDeskSource(s: MarketDeskResearchSource): MarketDeskResearchSource | undefined {
  if (typeof s.publisher !== "string" || !s.publisher.trim()
    || typeof s.articleId !== "string" || !/^[A-Za-z0-9]{8,16}$/.test(s.articleId)
    || s.articleUrl !== `https://marketdesk.ai/library/browse/${s.articleId}`
    || typeof s.contentHash !== "string" || !/^[a-f0-9]{64}$/.test(s.contentHash)
    || typeof s.documentDate !== "string" || !ISO_DATE.test(s.documentDate)
    || typeof s.folderDate !== "string" || !ISO_DATE.test(s.folderDate)
    || !Array.isArray(s.pages) || !s.pages.every(p => Number.isInteger(p) && p > 0)
    || typeof s.url !== "string" || !PRIVATE_RESEARCH_ASSET.test(s.url) || !s.url.includes(".pdf")
    || !Array.isArray(s.figures) || s.figures.length > 12
    || !s.figures.every(f => f && isPrivatePng(f.url) && typeof f.caption === "string")
    || (s.summary !== undefined && (!Array.isArray(s.summary) || s.summary.length > 20
      || !s.summary.every(b => typeof b === "string" && codePoints(b) <= 500)))
    || (s.extraImages !== undefined && (!Array.isArray(s.extraImages) || s.extraImages.length > 40
      || !s.extraImages.every(isPrivatePng)))) return undefined;
  return {
    kind: "marketdesk",
    publisher: withoutEmDashes(s.publisher),
    articleId: s.articleId,
    articleUrl: s.articleUrl,
    contentHash: s.contentHash,
    documentDate: s.documentDate,
    folderDate: s.folderDate,
    pages: s.pages,
    url: s.url,
    figures: s.figures.map(f => ({ url: f.url, caption: withoutEmDashes(f.caption) })),
    ...(s.summary ? { summary: s.summary.map(b => withoutEmDashes(b)) } : {}),
    ...(s.extraImages ? { extraImages: s.extraImages } : {}),
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
