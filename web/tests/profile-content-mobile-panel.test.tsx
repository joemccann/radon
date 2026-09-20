/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(""),
}));
vi.mock("@clerk/nextjs", () => ({
  useUser: () => ({ user: null }),
  useClerk: () => ({ signOut: vi.fn() }),
}));
vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: true, hasMounted: true }),
}));
vi.mock("@/lib/useProfile", () => ({
  useProfile: () => ({
    profile: { username: "op", avatar_url: null },
    isLoading: false,
    saveProfile: vi.fn(),
  }),
}));
vi.mock("@/lib/useBookmarks", () => ({
  useBookmarks: () => ({ bookmarks: [], isLoading: false, toggleBookmark: vi.fn() }),
}));
vi.mock("@/lib/useWatchlist", () => ({
  useWatchlist: () => ({ watchlist: [], isLoading: false, toggleWatch: vi.fn() }),
}));
vi.mock("@/components/PreferencesSection", () => ({
  default: () => <div data-testid="preferences-section">prefs</div>,
}));
vi.mock("@/components/profile/CredentialsPanel", () => ({
  default: () => <div data-testid="credentials-panel">keys</div>,
}));

import ProfileContent from "@/components/profile/ProfileContent";

afterEach(cleanup);

function surface(): HTMLElement {
  const node = document.querySelector(".profile-surface--mobile");
  expect(node).toBeInstanceOf(HTMLElement);
  return node as HTMLElement;
}

function directScrollChild(): HTMLElement | null {
  return surface().querySelector(":scope > .profile-panel-scroll");
}

describe("mobile ProfileContent Prefs/Keys scroller", () => {
  it("wraps Prefs as a direct .profile-panel-scroll child", () => {
    render(<ProfileContent />);
    fireEvent.click(screen.getByRole("tab", { name: "Prefs" }));
    const wrap = directScrollChild();
    expect(wrap).not.toBeNull();
    expect(wrap!.querySelector('[data-testid="preferences-section"]')).not.toBeNull();
  });

  it("wraps Keys as a direct .profile-panel-scroll child", () => {
    render(<ProfileContent />);
    fireEvent.click(screen.getByRole("tab", { name: "Keys" }));
    const wrap = directScrollChild();
    expect(wrap).not.toBeNull();
    expect(wrap!.querySelector('[data-testid="credentials-panel"]')).not.toBeNull();
  });

  it("keeps Bookmarks as a direct .profile-empty / .profile-list child", () => {
    render(<ProfileContent />);
    expect(directScrollChild()).toBeNull();
    expect(surface().querySelector(":scope > .profile-empty")).not.toBeNull();
  });
});
