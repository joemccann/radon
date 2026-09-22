export type ProfileTab = "bookmarks" | "watchlist" | "preferences" | "credentials";

export const PROFILE_TABS = new Set<ProfileTab>(["bookmarks", "watchlist", "preferences", "credentials"]);

const OPERATOR_TABS = new Set<ProfileTab>(["preferences", "credentials"]);

export function initialProfileTab(raw: string | null): ProfileTab {
  if (raw && PROFILE_TABS.has(raw as ProfileTab)) return raw as ProfileTab;
  return "bookmarks";
}

/**
 * The tab that actually renders. `/preferences` redirects every signed-in
 * user to `?tab=preferences`, but the operator tabs have no button and no
 * body for a demo user, so honouring the query verbatim left them on a
 * profile page with an empty content region.
 */
export function resolveProfileTab(requested: ProfileTab, isOperator: boolean): ProfileTab {
  if (!isOperator && OPERATOR_TABS.has(requested)) return "bookmarks";
  return requested;
}
