import type { jsPDF as JsPdf } from "jspdf";
import { estimateReadMinutes } from "./newsfeedReadTime";
import { formatAbsolute } from "./newsfeedTime";
import { getImageSource, researchSourceHref, researchSourceLabel } from "./newsfeedSource";
import { loadShareImage, sanitizeShareText, type SharePost } from "./newsfeedShare";

/** A chart as it will be placed: JPEG data plus its natural pixel size. */
export type PdfFigureImage = { data: string; width: number; height: number };
export type ArticleFigure = { url: string; caption: string };
export type FigureLoader = (url: string) => Promise<PdfFigureImage>;
export type ArticlePdf = { file: File; missingFigures: number };
/** `compress: false` keeps content streams readable for inspection. */
export type BuildOptions = { loadFigure?: FigureLoader; exportedAt?: Date; compress?: boolean };

// Export documents carry Radon's publication identity on print-safe paper, independent of app theme.
const SIGNAL: Rgb = [5, 173, 152];
const INK: Rgb = [15, 23, 30];
const MUTED: Rgb = [94, 106, 120];
const RULE: Rgb = [214, 220, 226];

const PAGE = { width: 612, height: 792, margin: 54, footer: 36 } as const;
const CONTENT_WIDTH = PAGE.width - PAGE.margin * 2;
const CONTENT_BOTTOM = PAGE.height - PAGE.margin - PAGE.footer;
const FIGURE_MAX_HEIGHT = 380;
const BODY = { size: 11, leading: 16, paragraphGap: 8 } as const;
const JPEG_QUALITY = 0.9;
const FIGURE_MAX_PIXELS = 2000;

type Rgb = [number, number, number];

/** Every image the article shows, in display order, with the caption the feed renders beside it. */
export function articleFigures(post: SharePost): ArticleFigure[] {
  const figures: ArticleFigure[] = (post.source?.figures ?? []).map(figure => ({ url: figure.url, caption: figure.caption }));
  for (const url of post.images ?? []) {
    if (!url || figures.some(figure => figure.url === url)) continue;
    const source = getImageSource(post, url);
    figures.push({ url, caption: source ? `Source: ${source}` : "" });
  }
  return figures;
}

const WIN_ANSI_SUBSTITUTES: Record<string, string> = {
  "≥": ">=", "≤": "<=", "≠": "!=", "≈": "~", "→": "->", "←": "<-", "↑": "up", "↓": "down",
  "−": "-", "‑": "-", "‒": "-", "―": "-", " ": " ", " ": " ", " ": " ", "Δ": "Delta ", "σ": "sigma",
};
// Characters outside cp1252 that the standard PDF fonts cannot draw.
const CP1252_EXTRAS = "€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–˜™š›œžŸ";

/** Text drawable by the standard PDF fonts, with the house punctuation policy applied. */
export function toPdfText(text: string): string {
  return Array.from(sanitizeShareText(text)).map(character => {
    if (character in WIN_ANSI_SUBSTITUTES) return WIN_ANSI_SUBSTITUTES[character];
    const code = character.codePointAt(0) ?? 0;
    if (code === 10 || (code >= 32 && code <= 126) || (code >= 160 && code <= 255) || CP1252_EXTRAS.includes(character)) return character;
    return "";
  }).join("");
}

/** Paragraphs of plain prose; research markdown keeps bullets and drops markup. */
export function articleParagraphs(content: string): string[] {
  return toPdfText(content.replace(/(?:https?:\/\/[^\s]*)?\/api\/newsfeed\/research\/[^\s)]+/gi, ""))
    .split(/\n\s*\n/)
    .map(block => block.split("\n").map(stripMarkdown).filter(Boolean).join("\n"))
    .filter(Boolean);
}

function stripMarkdown(line: string): string {
  return line.trim()
    .replace(/^#{1,6}\s+/, "")
    .replace(/^[-*+]\s+/, "• ")
    .replace(/\*\*(.+?)\*\*|__(.+?)__/g, "$1$2")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1");
}

export function articlePdfFilename(post: SharePost): string {
  const slug = toPdfText(post.title).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60);
  return `radon-${slug || "article"}.pdf`;
}

/** Draws an image on a white field and returns it as JPEG, the format PDF embeds natively. */
export async function loadFigureForPdf(url: string): Promise<PdfFigureImage> {
  const image = await loadShareImage(url);
  const scale = Math.min(1, FIGURE_MAX_PIXELS / Math.max(image.naturalWidth, image.naturalHeight));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(image.naturalWidth * scale);
  canvas.height = Math.round(image.naturalHeight * scale);
  const context = canvas.getContext("2d");
  if (!context) throw new Error("PDF export is unavailable in this browser.");
  context.fillStyle = "#ffffff";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  return { data: canvas.toDataURL("image/jpeg", JPEG_QUALITY), width: canvas.width, height: canvas.height };
}

/** Builds the branded article PDF; an unreadable chart is named in place rather than failing the export. */
export async function buildArticlePdf(post: SharePost, options: BuildOptions = {}): Promise<ArticlePdf> {
  const { loadFigure = loadFigureForPdf, exportedAt = new Date(), compress = true } = options;
  const figures = articleFigures(post);
  const [{ jsPDF }, images] = await Promise.all([
    import("jspdf"),
    Promise.all(figures.map(figure => loadFigure(figure.url).catch(() => null))),
  ]);
  const doc = new jsPDF({ unit: "pt", format: "letter", compress });
  doc.setProperties({ title: toPdfText(post.title), creator: "Radon", subject: "Radon market analysis" });
  new ArticleLayout(doc).render(post, figures.map((figure, index) => ({ ...figure, image: images[index] })), exportedAt);
  const blob = doc.output("blob");
  return {
    file: new File([blob], articlePdfFilename(post), { type: "application/pdf" }),
    missingFigures: images.filter(image => !image).length,
  };
}

type PlacedFigure = ArticleFigure & { image: PdfFigureImage | null };

/** Flows one article down Letter pages, breaking before any block that would cross the footer. */
class ArticleLayout {
  private y: number = PAGE.margin;

  constructor(private readonly doc: JsPdf) {}

  render(post: SharePost, figures: PlacedFigure[], exportedAt: Date): void {
    this.masthead(exportedAt);
    if (post.source) this.line(toPdfText(post.source.publisher).toUpperCase(), { size: 9, color: MUTED, style: "bold", after: 6 });
    this.wrapped(toPdfText(post.title), { size: 20, leading: 25, color: INK, style: "bold", after: 6 });
    this.line(`${formatAbsolute(post.isoTimestamp)}  ·  ${estimateReadMinutes(post.content ?? "")} min read`, { size: 9, color: MUTED, after: 8 });
    if (post.tags?.length) this.wrapped(post.tags.map(toPdfText).join("  ·  "), { size: 8, leading: 11, color: SIGNAL, style: "bold", after: 14 });
    for (const paragraph of articleParagraphs(post.content ?? "")) {
      this.wrapped(paragraph, { size: BODY.size, leading: BODY.leading, color: INK, after: BODY.paragraphGap });
    }
    for (const figure of figures) this.figure(figure);
    this.sourceLink(post);
    this.footers();
  }

  private masthead(exportedAt: Date): void {
    const date = exportedAt.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "America/New_York" }).toUpperCase();
    this.text("RADON", PAGE.margin, this.y + 14, { size: 16, color: SIGNAL, style: "bold" });
    this.text(`MARKET ANALYSIS  ·  ${date}`, PAGE.width - PAGE.margin, this.y + 13, { size: 8, color: MUTED, align: "right" });
    this.y += 24;
    this.rule(SIGNAL, 1.2);
    this.y += 22;
  }

  private figure({ caption, image }: PlacedFigure): void {
    this.y += 6;
    if (!image) {
      this.wrapped(toPdfText(`Chart unavailable${caption ? `: ${caption}` : ""}`), { size: 9, leading: 12, color: MUTED, after: 10 });
      return;
    }
    const scale = Math.min(CONTENT_WIDTH / image.width, FIGURE_MAX_HEIGHT / image.height);
    const width = image.width * scale;
    const height = image.height * scale;
    this.ensureRoom(height + (caption ? 14 : 0));
    this.doc.addImage(image.data, "JPEG", PAGE.margin + (CONTENT_WIDTH - width) / 2, this.y, width, height);
    this.doc.setDrawColor(...RULE);
    this.doc.setLineWidth(0.5);
    this.doc.rect(PAGE.margin + (CONTENT_WIDTH - width) / 2, this.y, width, height);
    this.y += height + 6;
    if (caption) this.wrapped(toPdfText(caption), { size: 8.5, leading: 11, color: MUTED });
    this.y += 10;
  }

  private sourceLink(post: SharePost): void {
    const href = post.source ? researchSourceHref(post.source) : post.href;
    if (!/^https?:\/\//i.test(href)) return;
    const label = post.source ? `${toPdfText(post.source.publisher)} · ${researchSourceLabel(post.source)}` : "Original article";
    this.y += 4;
    this.ensureRoom(14);
    this.font({ size: 9, color: SIGNAL });
    this.doc.textWithLink(`${label}: ${href}`.slice(0, 120), PAGE.margin, this.y + 9, { url: href });
    this.y += 14;
  }

  private footers(): void {
    const pages = this.doc.getNumberOfPages();
    for (let page = 1; page <= pages; page++) {
      this.doc.setPage(page);
      const baseline = PAGE.height - PAGE.margin + 4;
      this.doc.setDrawColor(...RULE);
      this.doc.setLineWidth(0.5);
      this.doc.line(PAGE.margin, baseline - 14, PAGE.width - PAGE.margin, baseline - 14);
      this.text("radon.run", PAGE.margin, baseline, { size: 8, color: SIGNAL, style: "bold" });
      this.text(`Page ${page} of ${pages}`, PAGE.width - PAGE.margin, baseline, { size: 8, color: MUTED, align: "right" });
    }
  }

  private wrapped(text: string, style: TextStyle & { leading: number; after?: number }): void {
    this.font(style);
    const lines: string[] = text.split("\n").flatMap(part => this.doc.splitTextToSize(part, CONTENT_WIDTH) as string[]);
    for (const line of lines) {
      this.ensureRoom(style.leading);
      this.text(line, PAGE.margin, this.y + style.size, style);
      this.y += style.leading;
    }
    this.y += style.after ?? 0;
  }

  private line(text: string, style: TextStyle & { after?: number }): void {
    this.wrapped(text, { ...style, leading: style.size + 4 });
  }

  private rule(color: Rgb, width: number): void {
    this.doc.setDrawColor(...color);
    this.doc.setLineWidth(width);
    this.doc.line(PAGE.margin, this.y, PAGE.width - PAGE.margin, this.y);
  }

  private ensureRoom(height: number): void {
    if (this.y + height <= CONTENT_BOTTOM) return;
    this.doc.addPage();
    this.y = PAGE.margin;
  }

  private text(text: string, x: number, y: number, style: TextStyle & { align?: "left" | "right" }): void {
    this.font(style);
    this.doc.text(text, x, y, { align: style.align ?? "left" });
  }

  private font({ size, color, style = "normal" }: TextStyle): void {
    this.doc.setFont("helvetica", style);
    this.doc.setFontSize(size);
    this.doc.setTextColor(...color);
  }
}

type TextStyle = { size: number; color: Rgb; style?: "normal" | "bold" };
