/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import MobileTabBar from "../components/mobile/MobileTabBar";
import MobileAppBar from "../components/mobile/MobileAppBar";
import MobileMoreDrawer from "../components/mobile/MobileMoreDrawer";
import { navItems } from "../lib/data";

const path = vi.hoisted(() => ({ current: "/regime/cri" }));
vi.mock("next/navigation", () => ({ usePathname: () => path.current }));
vi.mock("@/lib/useProfile", () => ({ useProfile: () => ({ profile: null }) }));
vi.mock("@/lib/ThemeContext", () => ({ useTheme: () => ({ theme: "light", toggleTheme: vi.fn() }) }));
vi.mock("@/lib/IBStatusContext", () => ({ useIBStatusContext: () => ({ displayStatus: "demo" }) }));
vi.mock("@/lib/offline/OfflineStatusContext", () => ({ useOfflineStatus: () => ({ offline: false }) }));
vi.mock("@clerk/nextjs", () => ({ useUser: () => ({ user: null }), useClerk: () => ({ signOut: vi.fn() }) }));
afterEach(cleanup);

describe("Clear mobile navigation", () => {
  it("exposes the four recurring tasks and accurately marks the active risk route", () => {
    render(<MobileTabBar onOpenMore={vi.fn()} />);
    const links = within(screen.getByRole("navigation")).getAllByRole("link");
    expect(links.map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
      ["Portfolio", "/dashboard"], ["Research", "/scanner"], ["Risk", "/regime/cri"], ["Positions", "/portfolio"],
    ]);
    expect(screen.getByRole("link", { name: "Risk" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: "Portfolio" }).getAttribute("aria-current")).toBeNull();
  });

  it("keeps secondary navigation in the app bar, alongside instrument search", () => {
    const openMore = vi.fn();
    const openSearch = vi.fn();
    render(<MobileAppBar title="Portfolio" isPageHeading onOpenMore={openMore} onOpenSearch={openSearch} />);
    expect(screen.getByRole("heading", { level: 1, name: "Portfolio" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Open more navigation" }));
    fireEvent.click(screen.getByRole("button", { name: "Open ticker search" }));
    expect(openMore).toHaveBeenCalledOnce();
    expect(openSearch).toHaveBeenCalledOnce();
  });

  it("preserves every route in the menu and traps focus until dismissal", () => {
    const close = vi.fn();
    const trigger = document.createElement("button");
    document.body.appendChild(trigger);
    trigger.focus();
    const view = render(<MobileMoreDrawer open onClose={close} />);
    const dialog = screen.getByRole("dialog", { name: "Your workspace" });
    const navigation = within(dialog).getByRole("navigation", { name: "Overflow navigation" });
    for (const item of navItems) {
      expect(within(navigation).getByRole("link", { name: item.label, exact: true }).getAttribute("href")).toBe(item.href);
    }
    const first = within(dialog).getByTestId("mobile-drawer-close");
    const last = within(dialog).getByTestId("mobile-drawer-theme-toggle");
    expect(document.activeElement).toBe(first);
    fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);
    fireEvent.keyDown(last, { key: "Tab" });
    expect(document.activeElement).toBe(first);
    fireEvent.keyDown(first, { key: "Escape" });
    expect(close).toHaveBeenCalledOnce();
    view.unmount();
    expect(document.activeElement).toBe(trigger);
    trigger.remove();
  });
});
