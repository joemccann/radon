import type { ReactNode, ReactElement } from "react";
import { afterEach, expect, it, vi } from "vitest";

const rendered = vi.hoisted(() => ({ tree: null as ReactNode }));
vi.mock("next/og", () => ({
  ImageResponse: class extends Response {
    constructor(tree: ReactNode) { super("png"); rendered.tree = tree; }
  },
}));
vi.mock("@/lib/og-fonts", () => ({ loadFonts: async () => [] }));
vi.mock("@/lib/rateLimit", () => ({ rateLimit: () => ({ ok: true }), clientIp: () => "test", SHARE_PNL_LIMIT: 10, SHARE_WINDOW_MS: 60_000 }));
import { GET } from "../app/api/share/pnl/route";

function text(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(text).join(" ");
  return text((node as ReactElement<{ children?: ReactNode }>).props?.children);
}

afterEach(() => { rendered.tree = null; });

for (const format of ["card", "story"]) {
  it(`${format} derives hold duration from entry/exit and labels Pacific daylight time correctly`, async () => {
    const params = new URLSearchParams({ description: "Closed TQQQ", pnl: "11435.65", entryPrice: "77.71", exitPrice: "78.85", entryTime: "2026-10-13T15:46:31Z", exitTime: "2026-10-13T16:30:30Z", holdTime: "999 days", format });
    expect((await GET(new Request(`https://radon.run/api/share/pnl?${params}`))).status).toBe(200);
    const output = text(rendered.tree);
    expect(output).toContain("ENTRY $77.71 @ 10/13/2026, 8:46 AM PDT");
    expect(output).toContain("HELD 44 minutes");
    expect(output).toContain("EXIT $78.85 @ 10/13/2026, 9:30 AM PDT");
  });
}

it("renders timestamp-only entry/exit data when their prices are unavailable", async () => {
  const params = new URLSearchParams({ description: "Closed TQQQ", pnl: "11435.65", entryTime: "2026-10-13T15:46:31Z", exitTime: "2026-10-13T16:30:30Z", format: "story" });
  await GET(new Request(`https://radon.run/api/share/pnl?${params}`));
  expect(text(rendered.tree)).toContain("ENTRY 10/13/2026, 8:46 AM PDT");
  expect(text(rendered.tree)).toContain("EXIT 10/13/2026, 9:30 AM PDT");
  expect(text(rendered.tree)).toContain("HELD 44 minutes");
});

it("labels winter timestamps PST and preserves legacy duration-only clients", async () => {
  await GET(new Request("https://radon.run/api/share/pnl?description=Closed+TQQQ&entryTime=2026-01-13T16:00:00Z&exitTime=2026-01-13T17:00:00Z&format=story"));
  expect(text(rendered.tree)).toContain("ENTRY 1/13/2026, 8:00 AM PST");
  expect(text(rendered.tree)).toContain("HELD 1 hour");
  await GET(new Request("https://radon.run/api/share/pnl?description=Closed+TQQQ&holdTime=2+days&format=story"));
  expect(text(rendered.tree)).toContain("HELD 2 days");
});

it("does not invent a same-day hold from a date-only entry", async () => {
  const params = new URLSearchParams({ description: "Closed TQQQ", entryPrice: "77.71", exitPrice: "78.85", entryTime: "2026-10-13", exitTime: "2026-10-13T16:30:30Z", format: "story" });
  await GET(new Request(`https://radon.run/api/share/pnl?${params}`));
  expect(text(rendered.tree)).toContain("ENTRY $77.71 @ 10/13/2026");
  expect(text(rendered.tree)).not.toContain("HELD");
});
