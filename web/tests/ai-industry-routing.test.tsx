/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import LegacyAiPage from "@/app/regime/llm/page";
import AiIndustryPage from "@/app/ai-industry/page";
import Sidebar from "@/components/Sidebar";
import AiInfrastructureHandoff from "@/components/AiInfrastructureHandoff";
import MobileTabBar from "@/components/mobile/MobileTabBar";
import MobileMoreDrawer from "@/components/mobile/MobileMoreDrawer";
import { resolveSectionFromPath } from "@/lib/chat";
import { REGIME_TABS } from "@/lib/regimeRail";

vi.mock("next/navigation", () => ({
  usePathname: () => "/ai-industry",
  redirect: (url: string) => { throw new Error(`redirect:${url}`); },
}));
vi.mock("@/components/WorkspaceShell", () => ({ default: ({ section }: { section: string }) => <div data-testid="workspace">{section}</div> }));
vi.mock("@/lib/useProfile", () => ({ useProfile: () => ({ profile: null }) }));
vi.mock("@/lib/ThemeContext", () => ({ useTheme: () => ({ theme: "light", toggleTheme: vi.fn() }) }));
vi.mock("@/lib/IBStatusContext", () => ({ useIBStatusContext: () => ({ displayStatus: "demo" }) }));
vi.mock("@clerk/nextjs", () => ({ useUser: () => ({ user: null }), useClerk: () => ({ signOut: vi.fn() }) }));
afterEach(cleanup);

describe("AI Industry standalone navigation", () => {
  it.each(["demand", "compute", "delivery", "finance"])("preserves the legacy %s pane and repeated filters", async (pane) => {
    await expect(LegacyAiPage({ searchParams: Promise.resolve({ pane, source: ["a", "b"], q: "AI & GPU", empty: "", missing: undefined }) }))
      .rejects.toThrow(`redirect:/ai-industry?pane=${pane}&source=a&source=b&q=AI+%26+GPU&empty=`);
  });

  it("redirects a bare bookmark without adding a query", async () => {
    await expect(LegacyAiPage({ searchParams: Promise.resolve({}) })).rejects.toThrow("redirect:/ai-industry");
  });

  it("renders the standalone workspace and excludes it from the regime rail", () => {
    render(<AiIndustryPage />);
    expect(screen.getByTestId("workspace").textContent).toBe("ai-industry");
    expect(resolveSectionFromPath("/ai-industry", "dashboard")).toBe("ai-industry");
    expect(REGIME_TABS).not.toContain("llm");
  });

  it("marks AI Industry as its own desktop destination and retains all workspaces", () => {
    render(<Sidebar activeSection="ai-industry" actionTone="var(--signal-core)" />);
    const primary = within(screen.getByRole("navigation", { name: "Primary navigation" }));
    expect(primary.getByRole("link", { name: "AI Industry" }).getAttribute("aria-current")).toBe("page");
    expect(primary.getByRole("link", { name: "Risk" }).getAttribute("aria-current")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Open all workspaces" }));
    expect(within(screen.getByRole("navigation", { name: "All workspaces" })).getByRole("link", { name: "AI Industry" }).getAttribute("href")).toBe("/ai-industry");
  });

  it("retains four mobile destinations and exposes AI Industry in the complete menu", () => {
    render(<><MobileTabBar onOpenMore={vi.fn()} /><MobileMoreDrawer open onClose={vi.fn()} /></>);
    expect(screen.getByRole("navigation", { name: "Primary mobile navigation" }).querySelectorAll("a")).toHaveLength(4);
    const menu = within(screen.getByRole("navigation", { name: "Overflow navigation" }));
    expect(menu.getByRole("link", { name: "AI Industry" }).getAttribute("href")).toBe("/ai-industry");
  });

  it("sends ticker research to the standalone category", () => {
    render(<AiInfrastructureHandoff ticker="NVDA" />);
    expect(screen.getByRole("link", { name: /Review industry evidence/ }).getAttribute("href")).toMatch(/^\/ai-industry\?pane=/);
  });
});
