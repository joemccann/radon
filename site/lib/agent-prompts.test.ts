import { afterEach, describe, expect, it, vi } from "vitest";
import {
  AGENT_PROMPT_SECTION_HEADINGS,
  SHARED_HARD_NOS,
  capabilityPrompts,
  developerRecipes,
  formatAgentPrompt,
  getCapabilityPrompt,
  writeClipboard,
} from "./agent-prompts";
import { HOSTED_MCP_URL } from "./developer-pages";
import { DEMO_APP_URL, siteUrl } from "./seo";

const REQUIRED_CAPABILITIES = [
  "flow",
  "gates",
  "gate-01",
  "gate-02",
  "gate-03",
  "gate-04",
  "cri",
  "gex",
  "structures",
  "kelly",
  "ib-terminal",
  "uw-ib",
] as const;

const REQUIRED_RECIPE_TITLES = [
  "Score flow for TICKER",
  "Evaluate gates for a structure",
  "Read CRI regime",
  "Read GEX walls and magnets for TICKER",
  "List convex structures",
  "Size with fractional Kelly",
  "What is Radon / when to use",
];

function headings(markdown: string): string[] {
  return markdown.split("\n").filter((line) => line.startsWith("#"));
}

describe("agent prompt payload", () => {
  it("keeps a stable markdown field order", () => {
    const markdown = formatAgentPrompt(getCapabilityPrompt("flow"));
    expect(headings(markdown)).toEqual([
      "# Radon Terminal - Flow scorer",
      ...AGENT_PROMPT_SECTION_HEADINGS.map((heading) => `## ${heading}`),
    ]);
    expect(markdown.startsWith("# Radon Terminal - Flow scorer\n")).toBe(true);
  });

  it("ships the shared hard nos before capability-specific nos", () => {
    const markdown = formatAgentPrompt(getCapabilityPrompt("flow"));
    const hardNos = markdown.split("## Hard nos\n")[1].split("\n## ")[0];
    const bullets = hardNos
      .split("\n")
      .filter((line) => line.startsWith("- "));
    expect(bullets.slice(0, SHARED_HARD_NOS.length)).toEqual(
      SHARED_HARD_NOS.map((item) => `- ${item}`),
    );
    expect(bullets.length).toBeGreaterThan(SHARED_HARD_NOS.length);
  });

  it("covers every listed dossier and gate surface", () => {
    expect(Object.keys(capabilityPrompts)).toEqual([...REQUIRED_CAPABILITIES]);
    for (const id of REQUIRED_CAPABILITIES) {
      const markdown = formatAgentPrompt(getCapabilityPrompt(id));
      expect(markdown).toContain(`1. Read ${siteUrl}/llms.txt`);
      expect(markdown).toMatch(
        /2\. Fetch https:\/\/radon\.run\/\S* with Accept: text\/markdown \(or \.md\)/,
      );
      expect(markdown).toContain("3. Demo: ");
      expect(markdown).toContain("4. MCP: ");
      expect(markdown).toContain(DEMO_APP_URL);
    }
  });

  it("reuses published when-to-use language and refuses broker behavior", () => {
    const flow = formatAgentPrompt(getCapabilityPrompt("flow"));
    expect(flow).toContain("dark-pool");
    expect(flow).toContain("accumulation or distribution");
    expect(flow).toContain("does not place, route, or broker an order");

    const gates = formatAgentPrompt(getCapabilityPrompt("gates"));
    expect(gates).toContain("gain at least 2x loss");
    expect(gates).toContain("stops on the first failure");

    const gate04 = formatAgentPrompt(getCapabilityPrompt("gate-04"));
    expect(gate04).toContain("Disabled by operator policy");

    const kelly = formatAgentPrompt(getCapabilityPrompt("kelly"));
    expect(kelly).toContain("2.5%");
    expect(kelly).toContain("ceiling, not a target");
  });

  it("points GEX and CRI at existing demo routes and hosted MCP reads", () => {
    const cri = formatAgentPrompt(getCapabilityPrompt("cri"));
    expect(cri).toContain(`${DEMO_APP_URL}/regime/cri`);
    expect(cri).toContain("demo_regime");
    expect(cri).toContain(HOSTED_MCP_URL);

    const gex = formatAgentPrompt(getCapabilityPrompt("gex"));
    expect(gex).toContain(`${DEMO_APP_URL}/regime/gex`);
    expect(gex).toContain("demo_gex");
    expect(gex).toContain("walls");
    expect(gex).toContain("magnets");
  });
});

describe("developer recipes", () => {
  it("ships seven one-paste cards with the canonical payload shape", () => {
    expect(developerRecipes).toHaveLength(7);
    expect(developerRecipes.map((recipe) => recipe.title)).toEqual(
      REQUIRED_RECIPE_TITLES,
    );
    for (const recipe of developerRecipes) {
      const markdown = formatAgentPrompt(recipe.prompt);
      expect(headings(markdown).slice(1)).toEqual(
        AGENT_PROMPT_SECTION_HEADINGS.map((heading) => `## ${heading}`),
      );
      expect(markdown).toContain("Not a broker; no Robinhood routing");
      expect(markdown).toContain("does not place, route, or broker an order");
      expect(markdown).not.toMatch(/[–—]/);
    }
  });

  it("keeps ticker and structure placeholders in the matching recipes", () => {
    const flow = formatAgentPrompt(
      developerRecipes.find((recipe) => recipe.id === "score-flow")!.prompt,
    );
    expect(flow).toContain("TICKER");
    expect(flow).toContain(`${DEMO_APP_URL}/flow-analysis/TICKER`);

    const gates = formatAgentPrompt(
      developerRecipes.find((recipe) => recipe.id === "evaluate-gates")!.prompt,
    );
    expect(gates).toContain("STRUCTURE_ID");

    const kelly = formatAgentPrompt(
      developerRecipes.find((recipe) => recipe.id === "size-kelly")!.prompt,
    );
    expect(kelly).toContain("MAX_GAIN");
    expect(kelly).toContain("MAX_LOSS");
  });
});

describe("writeClipboard", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reports copied when the clipboard write succeeds", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    await expect(writeClipboard("payload")).resolves.toBe("copied");
    expect(writeText).toHaveBeenCalledWith("payload");
  });

  it("reports failed when the clipboard is missing or throws", async () => {
    vi.stubGlobal("navigator", {});
    vi.stubGlobal("document", undefined);
    await expect(writeClipboard("payload")).resolves.toBe("failed");
    vi.stubGlobal("navigator", {
      clipboard: {
        writeText: vi.fn().mockRejectedValue(new Error("denied")),
      },
    });
    await expect(writeClipboard("payload")).resolves.toBe("failed");
  });

  it("falls back to a textarea copy when the clipboard API is missing", async () => {
    const execCommand = vi.fn().mockReturnValue(true);
    const area = {
      value: "",
      setAttribute: vi.fn(),
      style: { position: "", left: "" },
      select: vi.fn(),
    };
    vi.stubGlobal("navigator", {});
    vi.stubGlobal("document", {
      createElement: vi.fn(() => area),
      body: {
        appendChild: vi.fn(),
        removeChild: vi.fn(),
      },
      execCommand,
    });
    await expect(writeClipboard("payload")).resolves.toBe("copied");
    expect(area.value).toBe("payload");
    expect(execCommand).toHaveBeenCalledWith("copy");
  });
});
