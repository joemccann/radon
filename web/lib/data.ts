import {
  CTAGlyph,
  DashboardGlyph,
  DiscoverGlyph,
  ExposureGlyph,
  FlowGlyph,
  JournalGlyph,
  OperatorGlyph,
  OrdersGlyph,
  PerformanceGlyph,
  PortfolioGlyph,
  PreferencesGlyph,
  RegimeGlyph,
  ScannerGlyph,
  WatchlistGlyph,
  ProfileGlyph,
} from "@/components/icons/RadonGlyphs";
import type { WorkspaceNavItem, WorkspaceSection } from "./types";

export const PI_COMMANDS = ["scan", "discover", "evaluate", "portfolio", "journal", "sync", "leap-scan", "help"] as const;
export const PI_COMMAND_SET = new Set<string>(PI_COMMANDS);

export const PI_COMMAND_ALIASES: Record<string, string> = {
  "compare support vs against": "/scan --top 20",
  "action items": "/journal --limit 25",
  "what are action items": "/journal --limit 25",
};

export const NAV_GROUP_LABEL: Record<import("./types").NavGroupId, string> = {
  overview: "Overview",
  positions: "Positions",
  research: "Research",
  risk: "Risk",
  operations: "Operations",
};

export const NAV_GROUP_ORDER: import("./types").NavGroupId[] = ["overview", "positions", "research", "risk", "operations"];

/** The four recurring decisions in the Clear workstation, shared across viewports. */
export const clearPrimaryNavigation: { label: string; href: string; sections: WorkspaceSection[] }[] = [
  { label: "Portfolio", href: "/dashboard", sections: ["dashboard", "performance"] },
  { label: "Research", href: "/scanner", sections: ["scanner", "discover", "flow-analysis", "options", "watchlist"] },
  { label: "Risk", href: "/regime/cri", sections: ["regime", "cta"] },
  { label: "Positions", href: "/portfolio", sections: ["portfolio", "orders"] },
];

export const navItems: WorkspaceNavItem[] = [
  { label: "Portfolio", route: "dashboard", href: "/dashboard", icon: DashboardGlyph, group: "overview" },
  { label: "Positions", route: "portfolio", href: "/portfolio", icon: PortfolioGlyph, group: "positions" },
  { label: "Performance", route: "performance", href: "/performance", icon: PerformanceGlyph, hidden: false, group: "positions" },
  { label: "Orders", route: "orders", href: "/orders", icon: OrdersGlyph, group: "positions" },
  { label: "Scanner", route: "scanner", href: "/scanner", icon: ScannerGlyph, group: "research" },
  { label: "Watchlist", route: "watchlist", href: "/watchlist", icon: WatchlistGlyph, group: "positions" },
  { label: "Flow Analysis", route: "flow-analysis", href: "/flow-analysis", icon: FlowGlyph, group: "research" },
  { label: "Options", route: "options", href: "/options", icon: ExposureGlyph, group: "research" },
  { label: "Discover", route: "discover", href: "/discover", icon: DiscoverGlyph, hidden: true, group: "research" },
  { label: "Journal", route: "journal", href: "/journal", icon: JournalGlyph, group: "operations" },
  { label: "Regime", route: "regime", href: "/regime/cri", icon: RegimeGlyph, group: "risk" },
  { label: "CTA", route: "cta", href: "/cta", icon: CTAGlyph, group: "risk" },
  { label: "Alerts", route: "alerts", href: "/alerts", icon: ScannerGlyph, group: "operations" },
  { label: "Workflow", route: "workflow", href: "/workflow", icon: OperatorGlyph, group: "operations" },
  { label: "Operator", route: "admin", href: "/admin", icon: OperatorGlyph, group: "operations" },
  { label: "Preferences", route: "preferences", href: "/preferences", icon: PreferencesGlyph, group: "operations" },
  // Profile is reached via the dedicated user card above the sidebar footer,
  // not the main nav list — hidden keeps it out of the primary loop while
  // still exposing the route/label/icon to consumers that resolve by route.
  { label: "Profile", route: "profile", href: "/profile", icon: ProfileGlyph, hidden: true, group: "operations" },
];

export const quickPromptsBySection: Record<WorkspaceSection, string[]> = {
  dashboard: ["portfolio", "scan --top 12", "compare support vs against", "review watch list", "help"],
  "flow-analysis": ["analyze nvda", "compare support vs against", "what are action items", "review watch list", "scan --top 12", "evaluate nvda", "portfolio"],
  options: ["evaluate mu", "scan --top 12", "portfolio", "help"],
  portfolio: ["portfolio", "analyze nvda", "journal --limit 10", "evaluate msft", "help"],
  performance: ["portfolio", "stress-test", "journal --limit 10", "help"],
  orders: ["portfolio", "journal --limit 10", "scan --top 12", "help"],
  scanner: ["scan --top 25", "scan --min-score 12", "evaluate igv", "discover", "help"],
  discover: ["discover", "scan --top 12", "analyze aaoi", "journal", "help"],
  watchlist: ["scan --top 12", "evaluate msft", "portfolio", "help"],
  journal: ["journal --limit 25", "portfolio", "analyze nfLx", "help"],
  regime: ["cri-scan", "portfolio", "scan --top 12", "help"],
  cta: ["menthorq-cta", "cri-scan", "portfolio", "help"],
  alerts: ["scan --top 12", "portfolio", "help"],
  workflow: ["scan --top 12", "portfolio", "help"],
  admin: ["help"],
  preferences: ["help"],
  profile: ["portfolio", "scan --top 12", "help"],
  "ticker-detail": ["portfolio", "scan --top 12", "help"],
};

export const sectionDescription: Record<WorkspaceSection, string> = {
  dashboard: "Portfolio snapshot and command control panel.",
  "flow-analysis": "Flow and position analysis context.",
  options: "Options exposure, Greeks, open interest, and volatility measurements.",
  portfolio: "Current portfolio-focused controls and risk summary.",
  performance: "Institutional YTD performance analytics and benchmark-relative risk metrics.",
  orders: "Open orders and executed trades from IB Gateway.",
  scanner: "Candidate discovery and scan-driven alerts.",
  discover: "Opportunity discovery and watchlist growth.",
  watchlist: "Tracked symbols with inline instrument research and execution context.",
  journal: "Trade decision logs and history review.",
  regime: "Crash Risk Index: real-time CTA deleveraging monitor.",
  cta: "CTA positioning, vol-targeting exposure model and institutional flow.",
  alerts: "Signal alert rules evaluated against incoming scan rows.",
  workflow: "Visual flow-pipeline composer for chaining scans and signals.",
  admin: "Operator controls for IB Gateway 2FA and Radon services.",
  preferences: "Operator tunable runtime limits, scanner concurrency and feature flags.",
  profile: "Your account, saved articles and symbol watchlist.",
  "ticker-detail": "Instrument research surface: company, book, chain, position, orders, news, ratings, seasonality.",
};
