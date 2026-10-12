/**
 * @vitest-environment jsdom
 *
 * Generated charts sit under the body of a text-only research post.
 * A post that already has a source figure keeps that figure.
 */

import React from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DashboardNewsFeed from "../components/DashboardNewsFeed";
import { PlannedCharts } from "../components/PlannedCharts";
import { planCharts } from "../lib/planCharts";

vi.mock("next/image", () => ({
  default: ({ src, alt, className }: { src: string; alt: string; className?: string }) =>
    React.createElement("img", { src, alt, className }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => new URLSearchParams(""),
}));

const GS = [
  "Rates are getting into the AI credit story, GS Johnstone notes. The 10-year is back above 5.10% and the 2-year sits at 4.90% after the hot September PMI, oil and the hawkish Fed tone. The desk says the speed is what matters: the 10-year is up ~25 bp in two weeks and ~35 bp over the past month, which is close to where equities usually start to care, especially with the S&P still trading around ~19x.",
  "The clearest stress test is SoftBank. Its ~$11.1bn HY raise to fund the final $10bn OpenAI tranche and refinance bridge debt cleared with strong demand, but at record dollar yields of 8.6-9.75%. That is the tell for AI-linked credit. Earlier in the week, CoreWeave's Virginia data-centre financing was still marketing in the low- to mid-9% area. The desk's read: markets will still fund AI infrastructure and neocloud project finance, even with pre-revenue construction risk. But they are not doing it cheap. Credit investors only want near-10% coupons.",
  "The desk prices a 71% chance of an October hike and about 36 bp of tightening by year-end.",
].join("\n\n");

const file = "/api/newsfeed/research/files/" + "a".repeat(64);
const source = {
  kind: "dropbox" as const,
  publisher: "Goldman Sachs",
  url: `${file}.pdf`,
  documentDate: "2026-09-24",
  folderDate: "2026-09-24",
  pages: [2],
  figures: [] as { url: string; page: number; caption: string }[],
  fileId: "id",
  revision: "r",
  contentHash: "b".repeat(64),
};

describe("PlannedCharts", () => {
  it("draws the GS plan with a zero yield axis, one printed range, and a separate hike scale", () => {
    render(<PlannedCharts plans={planCharts(GS)} date="2026-09-24" />);

    const yields = screen.getByRole("img", { name: "Dollar yields" });
    expect(yields.getAttribute("data-axis-min")).toBe("0");
    expect(yields.getAttribute("data-axis-max")).toBe("12");

    const band = document.querySelector('[data-mark="coreweave-va"]');
    expect(band?.getAttribute("data-estimated")).toBe("true");
    expect(band?.querySelector("line")?.getAttribute("stroke-dasharray")).toBe("0 6");
    expect(band?.querySelector("line")?.getAttribute("stroke-linecap")).toBe("round");

    const printed = document.querySelector('[data-mark="softbank-hy"]');
    expect(printed?.getAttribute("data-estimated")).toBe("false");
    expect(printed?.querySelectorAll("line")).toHaveLength(1);
    expect(printed?.querySelectorAll("circle")).toHaveLength(2);

    const move = screen.getByRole("img", { name: /10-year change/i });
    expect(move.getAttribute("data-axis-min")).toBe("0");
    expect(move.getAttribute("data-axis-max")).toBe("40");
    expect(move.getAttribute("data-unit")).toBe("bp");
    expect(move.textContent).toMatch(/\+25 bp/);
    expect(move.textContent).toMatch(/\+35 bp/);
    expect(move.textContent).not.toMatch(/71|36/);
    expect(screen.queryByRole("img", { name: /October hike/i })).toBeNull();
    expect(screen.getByText(/Not a printed coupon/)).toBeTruthy();
  });
});

describe("DashboardNewsFeed generated charts", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  async function renderPost(post: Record<string, unknown>) {
    fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => [post] } as Response);
    render(<DashboardNewsFeed />);
    await waitFor(() => {
      expect(screen.queryAllByTestId("news-feed-item").length).toBeGreaterThan(0);
    });
    return screen.getByTestId("news-feed-item");
  }

  it("places generated charts after the body and keeps the text-only line", async () => {
    const item = await renderPost({
      id: "research-gs",
      title: "SoftBank HY AI raise",
      content: GS,
      timestamp: "2026-09-24T16:00:00Z",
      images: [],
      tags: ["CREDIT"],
      source,
    });
    const body = item.querySelector(".news-feed-summary");
    const charts = item.querySelector("[data-generated-charts]");
    expect(body).toBeTruthy();
    expect(charts).toBeTruthy();
    expect(charts!.compareDocumentPosition(body!) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
    expect(charts!.querySelectorAll("figure")).toHaveLength(2);
    expect(item.textContent).toMatch(/Text-only source evidence/);
    expect(item.querySelector(".news-feed-figure")).toBeNull();
  });

  it("does not generate a chart when the post already has a source figure", async () => {
    const chart = `${file}.png`;
    const item = await renderPost({
      id: "research-figured",
      title: "SoftBank HY AI raise",
      content: GS,
      timestamp: "2026-09-24T16:00:00Z",
      images: [chart],
      tags: ["CREDIT"],
      source: { ...source, figures: [{ url: chart, page: 2, caption: "Printed chart" }] },
    });
    expect(item.querySelector("[data-generated-charts]")).toBeNull();
    expect(item.querySelector(".news-feed-figure img")?.getAttribute("src")).toBe(chart);
  });
});

describe('semantic chart rendering', () => {
  afterEach(cleanup);
  it('places the Radon URL inside every generated SVG chart', () => {
    render(<PlannedCharts plans={planCharts(GS)} date="2026-09-24" />);
    const charts = screen.getAllByRole('img');
    expect(charts.length).toBeGreaterThan(1);
    for (const chart of charts) {
      expect(chart.textContent).toContain('https://radon.run');
      expect(chart.querySelector('a')?.getAttribute('href')).toBe('https://radon.run');
    }
  });
  it('labels percentages with the observed metric and unit', () => {
    render(<PlannedCharts plans={planCharts('Japan September Consumer Inflation Expectations Rose to 4.35% From 4.15% in August, First Increase in Five Months')} date="2026-09-30" />);
    const chart = screen.getByRole('img', {name: 'Japan consumer inflation expectations'});
    expect(chart.textContent).toContain('Consumer inflation expectations (%)');
    expect(chart.textContent).toContain('4.15%');
    expect(chart.textContent).toContain('4.35%');
    expect(chart.textContent).not.toContain('Yield');
    expect(chart.querySelectorAll('rect')).toHaveLength(2);
  });
  it('spaces dated line observations by elapsed time', () => {
    render(<PlannedCharts date="2026-09-30" plans={[{kind:'line', title:'Assets', unit:'$bn', axis:[0, 40], ticks:[0, 20, 40], points:[
      {id:'jan', label:'Jan 2025', value:10, timestamp:Date.UTC(2025,0,1)},
      {id:'feb', label:'Feb 2025', value:12, timestamp:Date.UTC(2025,1,1)},
      {id:'sep', label:'Sep 2026', value:39, timestamp:Date.UTC(2026,8,1)},
    ]}]} />);
    const points = [...screen.getByRole('img', {name:'Assets'}).querySelectorAll('circle')].map(p => Number(p.getAttribute('cx')));
    expect((points[1]-points[0])/(points[2]-points[0])).toBeCloseTo(31/608, 4);
    expect(screen.getByRole('img', {name:'Assets'}).textContent).toContain('https://radon.run');
  });
  it('keeps attribution inside scatter chart exports', () => {
    render(<PlannedCharts date="2026-10-09" plans={[{kind:'scatter',title:'Duration and yield',
      xUnit:'years',yUnit:'%',xAxis:[0,10],yAxis:[0,6],xTicks:[0,5,10],yTicks:[0,2,4,6],
      points:[{id:'a',label:'Issuer A',x:2,y:3},{id:'b',label:'Issuer B',x:6,y:4}]}]} />);
    const chart = screen.getByRole('img', {name:'Duration and yield'});
    expect(chart.textContent).toContain('https://radon.run');
    expect(chart.querySelector('a')?.getAttribute('href')).toBe('https://radon.run');
  });
});
