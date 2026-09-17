/**
 * `/preferences` redirects every signed-in user to `/profile?tab=preferences`,
 * but Preferences and Credentials are operator-only: a demo trial user has no
 * button and no body for them, so honouring the query verbatim rendered a
 * profile page with an empty content region. The resolved tab falls back.
 */
import { describe, expect, it } from "vitest";

import { resolveProfileTab } from "@/lib/profileTabs";

describe("resolveProfileTab", () => {
  it("keeps operator-only tabs for the operator", () => {
    expect(resolveProfileTab("preferences", true)).toBe("preferences");
    expect(resolveProfileTab("credentials", true)).toBe("credentials");
  });

  it("falls a demo user back to Bookmarks instead of an empty operator tab", () => {
    expect(resolveProfileTab("preferences", false)).toBe("bookmarks");
    expect(resolveProfileTab("credentials", false)).toBe("bookmarks");
  });

  it("leaves shared tabs alone for everyone", () => {
    expect(resolveProfileTab("watchlist", false)).toBe("watchlist");
    expect(resolveProfileTab("bookmarks", true)).toBe("bookmarks");
  });
});
