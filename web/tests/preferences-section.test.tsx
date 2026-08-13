/**
 * @vitest-environment jsdom
 *
 * /preferences operator surface — shell registration + section behaviour.
 *
 * Part 1 pins the shared registration files by source inspection (modeled on
 * tests/cta-page.test.ts): the WorkspaceSection union, the nav row, the quick
 * prompts / description maps, the WorkspaceSections switch arm, the
 * WorkspaceShell MetricCards exclusion, and the globals.css style block.
 *
 * Part 2 renders PreferencesSection against a mocked "@/lib/preferences" so
 * this suite never depends on the network client implementation.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { readFileSync } from "fs";
import { join } from "path";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";

const ROOT = join(__dirname, "..");
const read = (rel: string) => readFileSync(join(ROOT, rel), "utf-8");

// ---------------------------------------------------------------------------
// "@/lib/preferences" mock. The pure helpers are implemented here to the
// contract in the spec so the behaviour assertions stay meaningful; the three
// network helpers are spies.
// ---------------------------------------------------------------------------
const mocks = vi.hoisted(() => {
  class PreferencesRequestError extends Error {
    readonly status: number;
    readonly code: string;
    constructor(status: number, code: string, message: string) {
      super(message);
      this.name = "PreferencesRequestError";
      this.status = status;
      this.code = code;
    }
  }
  return {
    PreferencesRequestError,
    fetchPreferences: vi.fn(),
    savePreference: vi.fn(),
    resetPreference: vi.fn(),
  };
});

vi.mock("@/lib/preferences", () => ({
  PreferencesRequestError: mocks.PreferencesRequestError,
  fetchPreferences: mocks.fetchPreferences,
  savePreference: mocks.savePreference,
  resetPreference: mocks.resetPreference,
  groupPreferences: (entries: any[], groups: string[]) => {
    const known = groups.filter((g) => entries.some((e) => e.group === g));
    const extra = Array.from(new Set(entries.map((e) => e.group)))
      .filter((g) => !groups.includes(g))
      .sort();
    return [...known, ...extra].map((group) => ({
      group,
      entries: entries.filter((e) => e.group === group),
    }));
  },
  formatPreferenceValue: (entry: any, value: number | boolean) => {
    if (entry.value_type === "bool") {
      return value ? "On" : "Off";
    }
    const n = Number(value);
    const text = entry.value_type === "int"
      ? new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(n)
      : new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(n);
    return entry.unit ? `${text} ${entry.unit}` : text;
  },
  parsePreferenceInput: (entry: any, raw: string) => {
    const trimmed = raw.trim();
    if (!trimmed) return { error: "value is required" };
    const n = Number(trimmed);
    if (!Number.isFinite(n)) return { error: "value must be a number" };
    if (entry.value_type === "int" && !Number.isInteger(n)) {
      return { error: "value must be a whole number" };
    }
    if (n < Number(entry.hard_min) || n > Number(entry.hard_max)) {
      return { error: `value must be between ${entry.hard_min} and ${entry.hard_max}` };
    }
    return { value: n };
  },
}));

import PreferencesSection from "@/components/PreferencesSection";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------
type Entry = Record<string, unknown>;

const QTY: Entry = {
  key: "RADON_MAX_ORDER_QTY",
  label: "Max contracts per order",
  group: "Order Limits",
  value_type: "int",
  value: 400,
  default: 500,
  hard_min: 1,
  hard_max: 5000,
  unit: "contracts",
  description: "Hard ceiling on contracts per options or combo order.",
  applies_immediately: true,
  source: "db",
  db_rejected: false,
  updated_at: "2026-08-11T17:02:11Z",
  updated_by: "user_2abc",
};

const NOTIONAL: Entry = {
  key: "RADON_MAX_ORDER_NOTIONAL",
  label: "Max order notional",
  group: "Order Limits",
  value_type: "float",
  value: 250000,
  default: 250000,
  hard_min: 1000,
  hard_max: 5000000,
  unit: "USD",
  description: "Hard ceiling on dollar notional per order.",
  applies_immediately: true,
  source: "default",
  db_rejected: false,
  updated_at: null,
  updated_by: null,
};

const KB: Entry = {
  key: "RADON_KB_EMBED_DISABLED",
  label: "Disable knowledge base embeddings",
  group: "Feature Flags",
  value_type: "bool",
  value: false,
  default: false,
  hard_min: false,
  hard_max: true,
  unit: "",
  description: "Turn off knowledge base vector embedding.",
  applies_immediately: false,
  source: "env",
  db_rejected: false,
  updated_at: null,
  updated_by: null,
};

const WORKERS: Entry = {
  key: "RADON_SCANNER_WORKERS",
  label: "Watchlist scanner workers",
  group: "Scanning",
  value_type: "int",
  value: 24,
  default: 24,
  hard_min: 1,
  hard_max: 64,
  unit: "threads",
  description: "Concurrent worker threads for the watchlist scanner.",
  applies_immediately: true,
  source: "default",
  db_rejected: false,
  updated_at: null,
  updated_by: null,
};

const STORE_OK = { available: true, error: null, checked_at: "2026-08-11T17:02:11Z" };

function payload(overrides: Record<string, unknown> = {}) {
  return {
    preferences: [
      structuredClone(QTY),
      structuredClone(NOTIONAL),
      structuredClone(WORKERS),
      structuredClone(KB),
    ],
    groups: ["Order Limits", "Scanning", "Feature Flags"],
    store: { ...STORE_OK },
    generated_at: "2026-08-11T17:02:11Z",
    ...overrides,
  };
}

async function renderSection(p = payload()) {
  mocks.fetchPreferences.mockResolvedValue(p);
  render(<PreferencesSection />);
  await screen.findByTestId("preference-row-RADON_MAX_ORDER_QTY");
}

beforeEach(() => {
  mocks.fetchPreferences.mockReset();
  mocks.savePreference.mockReset();
  mocks.resetPreference.mockReset();
});

afterEach(() => {
  cleanup();
});

// ===========================================================================
// Part 1 — shell registration, source inspection
// ===========================================================================

describe("shell registration", () => {
  it("1. lib/types.ts WorkspaceSection union contains preferences", () => {
    expect(read("lib/types.ts")).toMatch(/WorkspaceSection[^;]*"preferences"/s);
  });

  it("2. lib/data.ts navItems has a visible preferences row right after admin", async () => {
    const { navItems } = await import("@/lib/data");
    const prefs = navItems.find((n) => n.route === "preferences");
    expect(prefs).toBeDefined();
    expect(prefs?.href).toBe("/preferences");
    expect(prefs?.group).toBe("operations");
    expect(prefs?.hidden).toBeFalsy();
    const routes = navItems.map((n) => n.route);
    expect(routes.indexOf("preferences")).toBe(routes.indexOf("admin") + 1);
  });

  it("3. quickPrompts + description are populated and free of em dashes", async () => {
    const { quickPromptsBySection, sectionDescription } = await import("@/lib/data");
    const prompts = quickPromptsBySection.preferences;
    expect(Array.isArray(prompts)).toBe(true);
    expect(prompts.length).toBeGreaterThan(0);
    for (const prompt of prompts) {
      expect(typeof prompt).toBe("string");
      expect(prompt.length).toBeGreaterThan(0);
    }
    const description = sectionDescription.preferences;
    expect(typeof description).toBe("string");
    expect(description.length).toBeGreaterThan(0);
    expect(description).not.toContain("—");
  });

  it("4. WorkspaceSections renders the preferences arm", () => {
    const src = read("components/WorkspaceSections.tsx");
    expect(src).toContain('case "preferences"');
    expect(src).toMatch(/import PreferencesSection from/);
  });

  it("5. WorkspaceShell excludes MetricCards and leaves headerOwnsPageHeading alone", () => {
    const src = read("components/WorkspaceShell.tsx");
    expect(src).toMatch(/activeSection !== "preferences"/);
    const headerBlock = src.slice(
      src.indexOf("const headerOwnsPageHeading"),
      src.indexOf("const headerOwnsPageHeading") + 400,
    );
    expect(headerBlock).not.toContain("preferences");
  });

  it("6. globals.css preferences block uses brand tokens only, radius <= 4px", () => {
    const css = read("app/globals.css");
    const blocks = css.match(/[^{}]*\.preferences-[^{}]*\{[^}]*\}/g) ?? [];
    expect(blocks.length).toBeGreaterThan(0);
    for (const block of blocks) {
      expect(block).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
      expect(block).not.toMatch(/\brgba?\(/);
      for (const m of block.matchAll(/border-radius:\s*([0-9.]+)px/g)) {
        expect(Number(m[1])).toBeLessThanOrEqual(4);
      }
    }
  });

  it("7. resolveSectionFromPath maps /preferences", async () => {
    const { resolveSectionFromPath } = await import("@/lib/chat");
    expect(resolveSectionFromPath("/preferences", "dashboard")).toBe("preferences");
  });
});

// ===========================================================================
// Part 2 — PreferencesSection behaviour
// ===========================================================================

describe("PreferencesSection", () => {
  it("8. renders one card per group and one row per entry", async () => {
    await renderSection();
    expect(screen.getByTestId("preferences-group-order-limits")).toBeTruthy();
    expect(screen.getByTestId("preferences-group-feature-flags")).toBeTruthy();
    expect(screen.getByTestId("preference-row-RADON_MAX_ORDER_QTY")).toBeTruthy();
    expect(screen.getByTestId("preference-row-RADON_MAX_ORDER_NOTIONAL")).toBeTruthy();
    expect(screen.getByTestId("preference-row-RADON_KB_EMBED_DISABLED")).toBeTruthy();
  });

  it("9. renders the source badge, default and allowed range", async () => {
    await renderSection();
    expect(screen.getByTestId("preference-source-RADON_MAX_ORDER_QTY").textContent).toBe("DB");
    expect(screen.getByTestId("preference-source-RADON_MAX_ORDER_NOTIONAL").textContent).toBe("DEFAULT");
    expect(screen.getByTestId("preference-source-RADON_KB_EMBED_DISABLED").textContent).toBe("ENV");
    expect(screen.getByTestId("preference-default-RADON_MAX_ORDER_QTY").textContent).toContain("500 contracts");
    expect(screen.getByTestId("preference-range-RADON_MAX_ORDER_QTY").textContent).toContain("1 to 5000");
    expect(screen.getByTestId("preference-range-RADON_KB_EMBED_DISABLED").textContent).toContain("On or Off");
  });

  it("10. renders RESTART REQUIRED only for applies_immediately false entries", async () => {
    await renderSection();
    expect(screen.getByTestId("preference-restart-badge-RADON_KB_EMBED_DISABLED")).toBeTruthy();
    expect(screen.queryByTestId("preference-restart-badge-RADON_MAX_ORDER_QTY")).toBeNull();
  });

  it("11. Save is disabled until the input changes and re-disables on revert", async () => {
    await renderSection();
    const input = screen.getByTestId("preference-input-RADON_MAX_ORDER_QTY") as HTMLInputElement;
    const save = screen.getByTestId("preference-save-RADON_MAX_ORDER_QTY") as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(input, { target: { value: "450" } });
    expect(save.disabled).toBe(false);
    fireEvent.change(input, { target: { value: "400" } });
    expect(save.disabled).toBe(true);
  });

  it("12. Save calls savePreference and re-renders the row from the response", async () => {
    await renderSection();
    mocks.savePreference.mockResolvedValue({
      preference: { ...structuredClone(NOTIONAL), value: 200000, source: "db", updated_by: "user_2abc" },
      store: { ...STORE_OK },
    });
    const input = screen.getByTestId("preference-input-RADON_MAX_ORDER_NOTIONAL") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "200000" } });
    fireEvent.click(screen.getByTestId("preference-save-RADON_MAX_ORDER_NOTIONAL"));
    await waitFor(() => {
      expect(screen.getByTestId("preference-source-RADON_MAX_ORDER_NOTIONAL").textContent).toBe("DB");
    });
    expect(mocks.savePreference).toHaveBeenCalledTimes(1);
    expect(mocks.savePreference).toHaveBeenCalledWith("RADON_MAX_ORDER_NOTIONAL", 200000);
    expect((screen.getByTestId("preference-input-RADON_MAX_ORDER_NOTIONAL") as HTMLInputElement).value).toBe("200000");
  });

  it("13. a rejected save renders the server message and keeps the server value", async () => {
    await renderSection();
    const message = "RADON_MAX_ORDER_QTY must be between 1 and 2500 (got 9000)";
    mocks.savePreference.mockRejectedValue(new mocks.PreferencesRequestError(422, "OUT_OF_RANGE", message));
    const input = screen.getByTestId("preference-input-RADON_MAX_ORDER_QTY") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "350" } });
    fireEvent.click(screen.getByTestId("preference-save-RADON_MAX_ORDER_QTY"));
    const err = await screen.findByTestId("preference-error-RADON_MAX_ORDER_QTY");
    expect(err.textContent).toBe(message);
    expect(err.getAttribute("role")).toBe("alert");
    expect(screen.getByTestId("preference-source-RADON_MAX_ORDER_QTY").textContent).toBe("DB");
    expect(screen.getByTestId("preference-default-RADON_MAX_ORDER_QTY").textContent).toContain("500 contracts");
  });

  it("14. Reset calls resetPreference and is disabled when source is not db", async () => {
    await renderSection();
    expect((screen.getByTestId("preference-reset-RADON_MAX_ORDER_NOTIONAL") as HTMLButtonElement).disabled).toBe(true);
    const reset = screen.getByTestId("preference-reset-RADON_MAX_ORDER_QTY") as HTMLButtonElement;
    expect(reset.disabled).toBe(false);
    mocks.resetPreference.mockResolvedValue({
      preference: { ...structuredClone(QTY), value: 500, source: "default", updated_at: null, updated_by: null },
      store: { ...STORE_OK },
    });
    fireEvent.click(reset);
    await waitFor(() => {
      expect(screen.getByTestId("preference-source-RADON_MAX_ORDER_QTY").textContent).toBe("DEFAULT");
    });
    expect(mocks.resetPreference).toHaveBeenCalledWith("RADON_MAX_ORDER_QTY");
    expect((screen.getByTestId("preference-input-RADON_MAX_ORDER_QTY") as HTMLInputElement).value).toBe("500");
  });

  it("15. client-side validation blocks Save without calling the API", async () => {
    await renderSection();
    const input = screen.getByTestId("preference-input-RADON_MAX_ORDER_QTY") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "9000" } });
    expect(screen.getByTestId("preference-error-RADON_MAX_ORDER_QTY").textContent).toBe("value must be between 1 and 5000");
    expect((screen.getByTestId("preference-save-RADON_MAX_ORDER_QTY") as HTMLButtonElement).disabled).toBe(true);
    expect(mocks.savePreference).not.toHaveBeenCalled();
  });

  it("16. an unavailable store renders the banner and disables every control", async () => {
    await renderSection(payload({ store: { available: false, error: "HranaHttpError: down", checked_at: null } }));
    expect(screen.getByTestId("preferences-store-banner")).toBeTruthy();
    for (const key of ["RADON_MAX_ORDER_QTY", "RADON_MAX_ORDER_NOTIONAL", "RADON_KB_EMBED_DISABLED"]) {
      expect((screen.getByTestId(`preference-save-${key}`) as HTMLButtonElement).disabled).toBe(true);
      expect((screen.getByTestId(`preference-reset-${key}`) as HTMLButtonElement).disabled).toBe(true);
    }
    expect((screen.getByTestId("preference-group-save-order-limits") as HTMLButtonElement).disabled).toBe(true);
  });

  it("17. bool rows render a checkbox (not role=switch) and save true", async () => {
    await renderSection();
    const box = screen.getByTestId("preference-input-RADON_KB_EMBED_DISABLED") as HTMLInputElement;
    expect(box.type).toBe("checkbox");
    expect(screen.queryAllByRole("switch").length).toBe(0);
    mocks.savePreference.mockResolvedValue({
      preference: { ...structuredClone(KB), value: true, source: "db" },
      store: { ...STORE_OK },
    });
    fireEvent.click(box);
    fireEvent.click(screen.getByTestId("preference-save-RADON_KB_EMBED_DISABLED"));
    await waitFor(() => {
      expect(mocks.savePreference).toHaveBeenCalledWith("RADON_KB_EMBED_DISABLED", true);
    });
    await waitFor(() => {
      expect(screen.getByTestId("preference-source-RADON_KB_EMBED_DISABLED").textContent).toBe("DB");
    });
  });

  it("18. a db_rejected entry renders the ignored badge", async () => {
    const p = payload();
    (p.preferences[0] as Record<string, unknown>).db_rejected = true;
    await renderSection(p);
    expect(screen.getByTestId("preference-rejected-RADON_MAX_ORDER_QTY")).toBeTruthy();
    expect(screen.queryByTestId("preference-rejected-RADON_KB_EMBED_DISABLED")).toBeNull();
  });

  it("19. a failed initial fetch renders the page error, not a blank shell", async () => {
    mocks.fetchPreferences.mockRejectedValue(new mocks.PreferencesRequestError(502, "UPSTREAM_ERROR", "preferences request failed"));
    render(<PreferencesSection />);
    const err = await screen.findByTestId("preferences-error");
    expect(err.textContent).toBe("preferences request failed");
    expect(err.getAttribute("role")).toBe("alert");
  });

  // -------------------------------------------------------------------------
  // Widening a risk cap is the one destructive edit on this page: an extra
  // zero in Max order notional silently quadruples the fat-finger stop. It
  // gets the same type-to-confirm gate the gateway Stop control uses.
  // -------------------------------------------------------------------------

  it("20. raising an Order Limits cap requires typed confirmation before it saves", async () => {
    await renderSection();
    fireEvent.change(screen.getByTestId("preference-input-RADON_MAX_ORDER_NOTIONAL"), {
      target: { value: "900000" },
    });
    fireEvent.click(screen.getByTestId("preference-save-RADON_MAX_ORDER_NOTIONAL"));

    expect(mocks.savePreference).not.toHaveBeenCalled();
    await screen.findByTestId("preference-widen-confirm");
    // ConfirmDialog portals to document.body, so assert on the portal content.
    expect(document.body.textContent).toContain("RADON_MAX_ORDER_NOTIONAL");
    expect(document.body.textContent).toContain("Widen a risk cap");
  });

  it("21. the widen confirmation saves once the key is typed", async () => {
    await renderSection();
    mocks.savePreference.mockResolvedValue({
      preference: { ...structuredClone(NOTIONAL), value: 900000, source: "db" },
      store: { ...STORE_OK },
    });
    fireEvent.change(screen.getByTestId("preference-input-RADON_MAX_ORDER_NOTIONAL"), {
      target: { value: "900000" },
    });
    fireEvent.click(screen.getByTestId("preference-save-RADON_MAX_ORDER_NOTIONAL"));

    await screen.findByTestId("preference-widen-confirm");
    fireEvent.change(screen.getByTestId("admin-confirm-typed-input"), {
      target: { value: "RADON_MAX_ORDER_NOTIONAL" },
    });
    fireEvent.click(screen.getByRole("button", { name: /widen cap/i }));

    await waitFor(() => {
      expect(mocks.savePreference).toHaveBeenCalledWith("RADON_MAX_ORDER_NOTIONAL", 900000);
    });
  });

  it("22. cancelling the widen confirmation leaves the cap untouched", async () => {
    await renderSection();
    fireEvent.change(screen.getByTestId("preference-input-RADON_MAX_ORDER_NOTIONAL"), {
      target: { value: "900000" },
    });
    fireEvent.click(screen.getByTestId("preference-save-RADON_MAX_ORDER_NOTIONAL"));
    await screen.findByTestId("preference-widen-confirm");
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    await waitFor(() => {
      expect(screen.queryByTestId("preference-widen-confirm")).toBeNull();
    });
    expect(mocks.savePreference).not.toHaveBeenCalled();
  });

  it("23. LOWERING an Order Limits cap saves without confirmation", async () => {
    await renderSection();
    mocks.savePreference.mockResolvedValue({
      preference: { ...structuredClone(NOTIONAL), value: 100000, source: "db" },
      store: { ...STORE_OK },
    });
    fireEvent.change(screen.getByTestId("preference-input-RADON_MAX_ORDER_NOTIONAL"), {
      target: { value: "100000" },
    });
    fireEvent.click(screen.getByTestId("preference-save-RADON_MAX_ORDER_NOTIONAL"));

    await waitFor(() => {
      expect(mocks.savePreference).toHaveBeenCalledWith("RADON_MAX_ORDER_NOTIONAL", 100000);
    });
    expect(screen.queryByTestId("preference-widen-confirm")).toBeNull();
  });

  it("24. raising a non-risk preference saves without confirmation", async () => {
    await renderSection();
    mocks.savePreference.mockResolvedValue({
      preference: { ...structuredClone(WORKERS), value: 40, source: "db" },
      store: { ...STORE_OK },
    });
    fireEvent.change(screen.getByTestId("preference-input-RADON_SCANNER_WORKERS"), {
      target: { value: "40" },
    });
    fireEvent.click(screen.getByTestId("preference-save-RADON_SCANNER_WORKERS"));

    await waitFor(() => {
      expect(mocks.savePreference).toHaveBeenCalledWith("RADON_SCANNER_WORKERS", 40);
    });
    expect(screen.queryByTestId("preference-widen-confirm")).toBeNull();
  });

  it("25. the unit is not repeated beside the input", async () => {
    await renderSection();
    const control = screen
      .getByTestId("preference-row-RADON_MAX_ORDER_QTY")
      .querySelector(".preferences-row__control");
    expect(control).toBeTruthy();
    expect(control?.textContent ?? "").not.toContain("contracts");
  });
});
