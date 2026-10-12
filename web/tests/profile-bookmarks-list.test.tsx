// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams("tab=bookmarks"),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
vi.mock("@clerk/nextjs", () => ({
  useUser: () => ({ user: null }),
  useClerk: () => ({ signOut: vi.fn() }),
}));
vi.mock("@/lib/useProfile", () => ({ useProfile: () => ({ profile: null, saveProfile: vi.fn() }) }));
vi.mock("@/lib/useWatchlist", () => ({
  useWatchlist: () => ({ watchlist: [], isLoading: false, toggleWatch: vi.fn() }),
}));
vi.mock("@/lib/useViewport", () => ({
  useViewport: () => ({ isMobile: false, hasMounted: true }),
}));
vi.mock("@/lib/useTickerNav", () => ({ useTickerNav: () => vi.fn() }));
vi.mock("@/components/PreferencesSection", () => ({ default: () => null }));
vi.mock("@/components/profile/CredentialsPanel", () => ({ default: () => null }));
vi.mock("@/lib/useBookmarks", () => ({
  useBookmarks: () => ({
    isLoading: false,
    toggleBookmark: vi.fn(),
    bookmarks: [
      {
        id: "b1", post_id: "p1", saved_at: "2026-10-01T00:00:00Z",
        snapshot: { title: "With image", url: "https://themarketear.com/posts/a", images: ["https://img/x.png"] },
      },
      {
        id: "b2", post_id: "p2", saved_at: "2026-09-01T00:00:00Z",
        snapshot: { title: "No image", url: "https://themarketear.com/posts/b" },
      },
    ],
  }),
}));

import ProfileContent from "../components/profile/ProfileContent";

afterEach(cleanup);

describe("profile bookmarks list (plate 33)", () => {
  it("renders a thumbnail only when the bookmark has an image, never an empty box", () => {
    const { container } = render(<ProfileContent />);
    const rows = screen.getByRole("list", { name: "Saved articles" }).querySelectorAll("li");
    expect(rows).toHaveLength(2);
    expect(rows[0].querySelector("img.profile-bookmark__thumb")).not.toBeNull();
    expect(rows[1].querySelector(".profile-bookmark__thumb")).toBeNull();
    expect(container.querySelector(".profile-bookmark__thumb--empty")).toBeNull();
  });

  it("drops the SAVED badge from every row but keeps the remove control", () => {
    render(<ProfileContent />);
    const list = screen.getByRole("list", { name: "Saved articles" });
    expect(list.textContent).not.toContain("SAVED");
    const toggles = list.querySelectorAll("[data-testid='star-toggle']");
    expect(toggles).toHaveLength(2);
    expect(toggles[0].getAttribute("aria-pressed")).toBe("true");
  });
});
