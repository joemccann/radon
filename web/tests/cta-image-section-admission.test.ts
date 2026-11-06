/** REL-108 / R-315: unknown image sections refuse before expensive rendering. */
import {beforeEach, describe, expect, it, vi} from "vitest";
const mocks = vi.hoisted(() => ({fonts: vi.fn(), render: vi.fn()}));
vi.mock("@/lib/db", () => ({resetDb: () => {}, getDb: () => ({execute: async () => ({rows: [{
  date: "mock",
  fetched_at: "2026-05-08T20:00:00Z",
  payload: JSON.stringify({date: "mock", tables: {main: []}}),
}]})})}));
vi.mock("@/lib/routeAccess", () => ({requireRouteAccess: vi.fn(async () => ({ok: true}))}));
vi.mock("@/lib/og-fonts", () => ({loadFonts: mocks.fonts}));
vi.mock("next/og", () => ({ImageResponse: class extends Response {
  constructor() {super("mock image"); mocks.render();}
}}));
import {GET} from "../app/api/menthorq/cta/image/route";

describe("CTA image section admission", () => {
  beforeEach(() => {vi.clearAllMocks(); mocks.fonts.mockResolvedValue([]);});
  it.each(["missing", "__proto__", "constructor"])("404s unknown section %s", async section => {
    const response = await GET(new Request(`http://mock/api/menthorq/cta/image?section=${section}`));
    expect(response.status).toBe(404);
    expect(mocks.fonts).not.toHaveBeenCalled();
    expect(mocks.render).not.toHaveBeenCalled();
  });
  it.each(["", "?section=main"])("keeps known reports admitted %s", async query => {
    const response = await GET(new Request(`http://mock/api/menthorq/cta/image${query}`));
    expect(response.status).toBe(200);
    expect(mocks.fonts).toHaveBeenCalledOnce();
    expect(mocks.render).toHaveBeenCalledOnce();
  });
});
