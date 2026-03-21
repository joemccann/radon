import { describe, it, expect } from "vitest";
import {
  PI_COMMANDS,
  PI_COMMAND_SET,
  PI_COMMAND_ALIASES,
  navItems,
  quickPromptsBySection,
  sectionDescription,
} from "../lib/data";
import type { WorkspaceSection } from "../lib/types";

// =============================================================================
// PI_COMMANDS
// =============================================================================

describe("PI_COMMANDS", () => {
  it("is an array", () => {
    expect(Array.isArray(PI_COMMANDS)).toBe(true);
  });

  it("contains expected core commands", () => {
    const commands = [...PI_COMMANDS];
    expect(commands).toContain("scan");
    expect(commands).toContain("discover");
    expect(commands).toContain("evaluate");
    expect(commands).toContain("portfolio");
    expect(commands).toContain("journal");
    expect(commands).toContain("sync");
    expect(commands).toContain("help");
  });

  it("contains leap-scan", () => {
    const commands = [...PI_COMMANDS];
    expect(commands).toContain("leap-scan");
  });

  it("has no empty strings", () => {
    for (const cmd of PI_COMMANDS) {
      expect(cmd.length).toBeGreaterThan(0);
    }
  });
});

// =============================================================================
// PI_COMMAND_SET
// =============================================================================

describe("PI_COMMAND_SET", () => {
  it("is a Set", () => {
    expect(PI_COMMAND_SET).toBeInstanceOf(Set);
  });

  it("contains all PI_COMMANDS entries", () => {
    for (const cmd of PI_COMMANDS) {
      expect(PI_COMMAND_SET.has(cmd)).toBe(true);
    }
  });

  it("has same size as PI_COMMANDS (no duplicates)", () => {
    expect(PI_COMMAND_SET.size).toBe(PI_COMMANDS.length);
  });

  it("does not contain non-existent commands", () => {
    expect(PI_COMMAND_SET.has("nonexistent")).toBe(false);
    expect(PI_COMMAND_SET.has("")).toBe(false);
  });
});

// =============================================================================
// PI_COMMAND_ALIASES
// =============================================================================

describe("PI_COMMAND_ALIASES", () => {
  it("is a non-empty object", () => {
    expect(typeof PI_COMMAND_ALIASES).toBe("object");
    expect(Object.keys(PI_COMMAND_ALIASES).length).toBeGreaterThan(0);
  });

  it("maps to valid commands starting with /", () => {
    for (const [alias, target] of Object.entries(PI_COMMAND_ALIASES)) {
      expect(alias.length).toBeGreaterThan(0);
      expect(target.startsWith("/")).toBe(true);
    }
  });

  it("contains known aliases", () => {
    expect(PI_COMMAND_ALIASES["watchlist"]).toBeDefined();
    expect(PI_COMMAND_ALIASES["action items"]).toBeDefined();
  });

  it("aliases map to known command roots", () => {
    for (const target of Object.values(PI_COMMAND_ALIASES)) {
      // Extract the command name after / and before any space
      const cmdName = target.split(" ")[0].replace("/", "");
      expect(PI_COMMAND_SET.has(cmdName)).toBe(true);
    }
  });
});

// =============================================================================
// navItems
// =============================================================================

describe("navItems", () => {
  it("is a non-empty array", () => {
    expect(Array.isArray(navItems)).toBe(true);
    expect(navItems.length).toBeGreaterThan(0);
  });

  it("each item has required fields", () => {
    for (const item of navItems) {
      expect(typeof item.label).toBe("string");
      expect(typeof item.route).toBe("string");
      expect(typeof item.href).toBe("string");
      expect(item.icon).toBeDefined();
    }
  });

  it("each href starts with /", () => {
    for (const item of navItems) {
      expect(item.href.startsWith("/")).toBe(true);
    }
  });

  it("each href contains the route", () => {
    for (const item of navItems) {
      expect(item.href).toContain(item.route);
    }
  });

  it("has correct route/href pairs", () => {
    const routeMap = new Map(navItems.map((n) => [n.route, n.href]));
    expect(routeMap.get("dashboard")).toBe("/dashboard");
    expect(routeMap.get("flow-analysis")).toBe("/flow-analysis");
    expect(routeMap.get("portfolio")).toBe("/portfolio");
    expect(routeMap.get("performance")).toBe("/performance");
    expect(routeMap.get("orders")).toBe("/orders");
    expect(routeMap.get("scanner")).toBe("/scanner");
    expect(routeMap.get("discover")).toBe("/discover");
    expect(routeMap.get("journal")).toBe("/journal");
    expect(routeMap.get("internals")).toBe("/internals");
  });

  it("has unique routes", () => {
    const routes = navItems.map((n) => n.route);
    expect(new Set(routes).size).toBe(routes.length);
  });

  it("has unique hrefs", () => {
    const hrefs = navItems.map((n) => n.href);
    expect(new Set(hrefs).size).toBe(hrefs.length);
  });
});

// =============================================================================
// quickPromptsBySection
// =============================================================================

describe("quickPromptsBySection", () => {
  const allSections: WorkspaceSection[] = [
    "dashboard",
    "flow-analysis",
    "portfolio",
    "performance",
    "orders",
    "scanner",
    "discover",
    "journal",
    "regime",
    "internals",
    "cta",
    "ticker-detail",
  ];

  it("covers all WorkspaceSection keys", () => {
    for (const section of allSections) {
      expect(quickPromptsBySection[section]).toBeDefined();
      expect(Array.isArray(quickPromptsBySection[section])).toBe(true);
    }
  });

  it("each section has at least one prompt", () => {
    for (const section of allSections) {
      expect(quickPromptsBySection[section].length).toBeGreaterThan(0);
    }
  });

  it("all prompts are non-empty strings", () => {
    for (const section of allSections) {
      for (const prompt of quickPromptsBySection[section]) {
        expect(typeof prompt).toBe("string");
        expect(prompt.length).toBeGreaterThan(0);
      }
    }
  });

  it("has no extra keys beyond known sections", () => {
    const keys = Object.keys(quickPromptsBySection);
    for (const key of keys) {
      expect(allSections).toContain(key);
    }
  });
});

// =============================================================================
// sectionDescription
// =============================================================================

describe("sectionDescription", () => {
  const allSections: WorkspaceSection[] = [
    "dashboard",
    "flow-analysis",
    "portfolio",
    "performance",
    "orders",
    "scanner",
    "discover",
    "journal",
    "regime",
    "internals",
    "cta",
    "ticker-detail",
  ];

  it("covers all WorkspaceSection keys", () => {
    for (const section of allSections) {
      expect(sectionDescription[section]).toBeDefined();
      expect(typeof sectionDescription[section]).toBe("string");
    }
  });

  it("each description is a non-empty string", () => {
    for (const section of allSections) {
      expect(sectionDescription[section].length).toBeGreaterThan(0);
    }
  });

  it("has no extra keys beyond known sections", () => {
    const keys = Object.keys(sectionDescription);
    for (const key of keys) {
      expect(allSections).toContain(key);
    }
  });

  it("descriptions end with period", () => {
    for (const section of allSections) {
      expect(sectionDescription[section].endsWith(".")).toBe(true);
    }
  });
});
