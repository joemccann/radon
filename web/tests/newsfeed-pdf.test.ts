/** @vitest-environment jsdom */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { articleFigures, articleParagraphs, articlePdfFilename, buildArticlePdf, toPdfText, type PdfFigureImage } from "@/lib/newsfeedPdf";
import type { SharePost } from "@/lib/newsfeedShare";

const JPEG = `data:image/jpeg;base64,${readFileSync(join(__dirname, "fixtures/newsfeed-pdf-chart.jpg")).toString("base64")}`;
const chart: PdfFigureImage = { data: JPEG, width: 40, height: 20 };

const marketPost: SharePost = {
  id: "dc-power", title: "Data-center power ≥ supply: financing tightens", timestamp: "2026-11-07T15:00:00Z",
  isoTimestamp: "2026-11-07T15:00:00Z", href: "https://example.com/dc-power",
  content: "Financing is getting tighter too.\n\nThe CDS AI basket trades >60bp wider than CDX IG.",
  images: ["/chart-a.png", "/chart-b.png", "/chart-c.png"],
  imageSources: { "/chart-b.png": "Morgan Stanley" },
  tags: ["MARKETDESK", "DATA-CENTERS", "CREDIT"],
};

const researchPost: SharePost = {
  ...marketPost, id: "research-1", images: ["/api/newsfeed/research/files/a.png", "/extra.png"],
  content: "## Key points\n\n- **Capex** keeps rising\n- Power is the [constraint](https://x.test)",
  source: { kind: "marketdesk", publisher: "Morgan Stanley", articleId: "a1", articleUrl: "https://ms.test/a1", contentHash: "h",
    documentDate: "2026-11-06", folderDate: "2026-11-07", pages: [], url: "https://ms.test/a1", evidenceKind: "article",
    figures: [{ url: "/api/newsfeed/research/files/a.png", caption: "Exhibit 1: AI debt issuance" }] },
};

async function pdfText(file: File): Promise<string> {
  const bytes = await new Promise<ArrayBuffer>(resolve => { const reader = new FileReader(); reader.onload = () => resolve(reader.result as ArrayBuffer); reader.readAsArrayBuffer(file); });
  return new TextDecoder("latin1").decode(new Uint8Array(bytes));
}

describe("article figures", () => {
  it("keeps every feed image in display order with its attribution", () => {
    expect(articleFigures(marketPost)).toEqual([
      { url: "/chart-a.png", caption: "" },
      { url: "/chart-b.png", caption: "Source: Morgan Stanley" },
      { url: "/chart-c.png", caption: "" },
    ]);
  });

  it("puts research figures first with their captions and does not repeat them", () => {
    expect(articleFigures(researchPost)).toEqual([
      { url: "/api/newsfeed/research/files/a.png", caption: "Exhibit 1: AI debt issuance" },
      { url: "/extra.png", caption: "" },
    ]);
  });
});

describe("PDF text", () => {
  it("replaces glyphs the standard fonts cannot draw and removes em dashes", () => {
    expect(toPdfText("VIX ≥ 30 → hedge — now ✅")).not.toMatch(/[≥→—✅]/);
    expect(toPdfText("VIX ≥ 30 → hedge")).toBe("VIX >= 30 -> hedge");
    expect(toPdfText("Café · 5% • €")).toBe("Café · 5% • €");
  });

  it("flattens research markdown into readable paragraphs", () => {
    expect(articleParagraphs(researchPost.content!)).toEqual(["Key points", "• Capex keeps rising\n• Power is the constraint"]);
  });

  it("names the file after the headline", () => {
    expect(articlePdfFilename(marketPost)).toBe("radon-data-center-power-supply-financing-tightens.pdf");
  });
});

describe("buildArticlePdf", () => {
  it("embeds every article image in a branded PDF", async () => {
    const loadFigure = vi.fn().mockResolvedValue(chart);
    const { file, missingFigures } = await buildArticlePdf(marketPost, { loadFigure, compress: false, exportedAt: new Date("2026-11-08T15:00:00Z") });
    expect(loadFigure.mock.calls.map(([url]) => url)).toEqual(["/chart-a.png", "/chart-b.png", "/chart-c.png"]);
    expect(file.type).toBe("application/pdf");
    expect(file.name).toBe("radon-data-center-power-supply-financing-tightens.pdf");
    expect(missingFigures).toBe(0);
    const pdf = await pdfText(file);
    expect(pdf.startsWith("%PDF-")).toBe(true);
    expect(pdf.match(/\/I\d+ Do/g)).toHaveLength(3);
    for (const text of ["RADON", "MARKET ANALYSIS", "NOV 8, 2026", "Data-center power >= supply", "MARKETDESK", "60bp wider than CDX IG", "Source: Morgan Stanley", "radon.run", "Page 1 of"]) {
      expect(pdf).toContain(text);
    }
    expect(pdf).toContain("/URI (https://example.com/dc-power)");
  });

  it("marks an unreadable chart in place instead of failing the export", async () => {
    const loadFigure = vi.fn().mockImplementation(async (url: string) => {
      if (url === "/chart-b.png") throw new Error("404");
      return chart;
    });
    const { file, missingFigures } = await buildArticlePdf(marketPost, { loadFigure, compress: false });
    const pdf = await pdfText(file);
    expect(missingFigures).toBe(1);
    expect(pdf.match(/\/I\d+ Do/g)).toHaveLength(2);
    expect(pdf).toContain("Chart unavailable: Source: Morgan Stanley");
  });

  it("flows long articles onto additional pages", async () => {
    const long = { ...marketPost, content: Array.from({ length: 40 }, (_, i) => `Paragraph ${i} ${"words ".repeat(40)}`).join("\n\n") };
    const { file } = await buildArticlePdf(long, { loadFigure: async () => chart, compress: false });
    const pdf = await pdfText(file);
    const pages = Number(/Page 1 of (\d+)/.exec(pdf)?.[1]);
    expect(pages).toBeGreaterThan(1);
    expect(pdf.match(/\/Type \/Page\b/g)).toHaveLength(pages);
  });
});
