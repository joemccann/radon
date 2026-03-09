import {
  Activity,
  Circle,
  ClipboardList,
  LayoutDashboard,
  LineChart,
  Search,
  Shield,
  Sparkles,
  Wrench,
} from "lucide-react";
import type { WorkspaceNavItem, WorkspaceSection } from "./types";

export const PI_COMMANDS = ["scan", "discover", "evaluate", "portfolio", "journal", "sync", "leap-scan", "help"] as const;
export const PI_COMMAND_SET = new Set<string>(PI_COMMANDS);

export const PI_COMMAND_ALIASES: Record<string, string> = {
  "compare support vs against": "/scan --top 20",
  "action items": "/journal --limit 25",
  "what are action items": "/journal --limit 25",
  "review watch list": "/scan --top 12",
  "watch list": "/scan --top 12",
  "watchlist": "/scan --top 12",
};

export const navItems: WorkspaceNavItem[] = [
  { label: "Dashboard", route: "dashboard", href: "/dashboard", icon: LayoutDashboard },
  { label: "Flow Analysis", route: "flow-analysis", href: "/flow-analysis", icon: LineChart },
  { label: "Portfolio", route: "portfolio", href: "/portfolio", icon: Circle },
  { label: "Orders", route: "orders", href: "/orders", icon: ClipboardList },
  { label: "Scanner", route: "scanner", href: "/scanner", icon: Sparkles },
  { label: "Discover", route: "discover", href: "/discover", icon: Search },
  { label: "Journal", route: "journal", href: "/journal", icon: Wrench },
  { label: "Regime", route: "regime", href: "/regime", icon: Shield },
  { label: "CTA", route: "cta", href: "/cta", icon: Activity },
];

export const quickPromptsBySection: Record<WorkspaceSection, string[]> = {
  dashboard: ["portfolio", "scan --top 12", "compare support vs against", "review watch list", "help"],
  "flow-analysis": ["analyze brze", "compare support vs against", "what are action items", "review watch list", "scan --top 12", "evaluate brze", "portfolio"],
  portfolio: ["portfolio", "analyze brze", "journal --limit 10", "evaluate msft", "help"],
  orders: ["portfolio", "journal --limit 10", "scan --top 12", "help"],
  scanner: ["scan --top 25", "scan --min-score 12", "evaluate igv", "discover", "help"],
  discover: ["discover", "scan --top 12", "analyze aaoi", "journal", "help"],
  journal: ["journal --limit 25", "portfolio", "analyze nfLx", "help"],
  regime: ["cri-scan", "portfolio", "scan --top 12", "help"],
  cta: ["menthorq-cta", "cri-scan", "portfolio", "help"],
};

export const sectionDescription: Record<WorkspaceSection, string> = {
  dashboard: "Portfolio snapshot and command control panel.",
  "flow-analysis": "Flow and position analysis context.",
  portfolio: "Current portfolio-focused controls and risk summary.",
  orders: "Open orders and executed trades from IB Gateway.",
  scanner: "Candidate discovery and scan-driven alerts.",
  discover: "Opportunity discovery and watchlist growth.",
  journal: "Trade decision logs and history review.",
  regime: "Crash Risk Index — real-time CTA deleveraging monitor.",
  cta: "CTA positioning, vol-targeting exposure model and institutional flow.",
};
