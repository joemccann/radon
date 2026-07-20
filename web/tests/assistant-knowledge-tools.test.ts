import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  radonFetch: vi.fn(),
}));

vi.mock("@/lib/radonApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/radonApi")>();
  return {
    ...actual,
    radonFetch: mocks.radonFetch,
  };
});

const LONG_CONTENT = "dark pool accumulation detail ".repeat(200); // ~6000 chars
const LONG_SUMMARY = "summary sentence ".repeat(60); // ~1000 chars

const RESULT_ROW = {
  source: "evals",
  scope: "trading",
  doc_key: "evals/NVDA-2026-06-01",
  chunk_ix: 2,
  title: "NVDA 7-milestone eval",
  summary: LONG_SUMMARY,
  content: LONG_CONTENT,
  metadata: { tickers: ["NVDA"] },
  score: 0.912,
  last_activity_at: "2026-06-01T14:00:00Z",
  neighbors: [{ chunk_ix: 1, content: "neighbor text" }],
};

describe("assistant knowledge tools", () => {
  beforeEach(() => {
    vi.resetModules();
    mocks.radonFetch.mockReset();
  });

  afterEach(() => {
    vi.doUnmock("@/lib/llm/provider");
  });

  it("registers search_knowledge and find_prior_evals as READ tools", async () => {
    const { ASSISTANT_TOOLS, isDestructiveTool, toolSchemas } = await import(
      "@/lib/assistant/tools"
    );

    const names = ASSISTANT_TOOLS.map((tool) => tool.name);
    expect(names).toEqual(expect.arrayContaining(["search_knowledge", "find_prior_evals"]));

    expect(isDestructiveTool("search_knowledge")).toBe(false);
    expect(isDestructiveTool("find_prior_evals")).toBe(false);

    const searchTool = ASSISTANT_TOOLS.find((tool) => tool.name === "search_knowledge");
    expect(searchTool?.destructive).toBe(false);
    expect(typeof searchTool?.run).toBe("function");
    expect(searchTool?.input_schema).toMatchObject({
      type: "object",
      required: ["query"],
    });
    const searchProps = searchTool?.input_schema.properties as Record<string, unknown>;
    expect(searchProps).toHaveProperty("query");
    expect(searchProps).toHaveProperty("scopes");
    expect(searchProps).toHaveProperty("sources");

    const evalsTool = ASSISTANT_TOOLS.find((tool) => tool.name === "find_prior_evals");
    expect(evalsTool?.destructive).toBe(false);
    expect(typeof evalsTool?.run).toBe("function");
    expect(evalsTool?.input_schema).toMatchObject({
      type: "object",
      required: ["ticker"],
    });

    // The model-facing schema surface must expose both tools.
    const schemaNames = toolSchemas().map((schema) => schema.name);
    expect(schemaNames).toEqual(
      expect.arrayContaining(["search_knowledge", "find_prior_evals"]),
    );
  });

  it("search_knowledge always sends compact:true and limit 6, threading the token", async () => {
    mocks.radonFetch.mockResolvedValue({ results: [RESULT_ROW], retrieval: "hybrid" });
    const { executeTool } = await import("@/lib/assistant/tools");

    const result = await executeTool(
      "search_knowledge",
      { query: "gex flip regime", scopes: ["trading"], sources: ["evals"] },
      "jwt-token",
    );

    expect(result.ok).toBe(true);
    expect(mocks.radonFetch).toHaveBeenCalledTimes(1);
    const [path, opts] = mocks.radonFetch.mock.calls[0];
    expect(path).toBe("/knowledge/search");
    expect(opts).toMatchObject({ method: "POST", token: "jwt-token" });
    expect(JSON.parse(opts.body as string)).toMatchObject({
      query: "gex flip regime",
      scopes: ["trading"],
      sources: ["evals"],
      compact: true,
      limit: 6,
    });
  });

  it("search_knowledge omits scope/source filters when not supplied", async () => {
    mocks.radonFetch.mockResolvedValue({ results: [], retrieval: "fts-only" });
    const { executeTool } = await import("@/lib/assistant/tools");

    const result = await executeTool("search_knowledge", { query: "watchdog 2fa" });

    expect(result.ok).toBe(true);
    const [, opts] = mocks.radonFetch.mock.calls[0];
    const body = JSON.parse(opts.body as string);
    expect(body).not.toHaveProperty("scopes");
    expect(body).not.toHaveProperty("sources");
  });

  it("search_knowledge formats results as bounded text blocks, never raw JSON dumps", async () => {
    mocks.radonFetch.mockResolvedValue({ results: [RESULT_ROW], retrieval: "hybrid" });
    const { executeTool } = await import("@/lib/assistant/tools");

    const result = await executeTool("search_knowledge", { query: "nvda thesis" });

    expect(result.ok).toBe(true);
    const data = result.data as { retrieval: string; count: number; results: string[] };
    expect(data.retrieval).toBe("hybrid");
    expect(data.count).toBe(1);
    expect(data.results).toHaveLength(1);

    const block = data.results[0];
    expect(typeof block).toBe("string");
    // Header carries source, doc_key, title, and score for citation.
    expect(block).toContain("evals");
    expect(block).toContain("evals/NVDA-2026-06-01");
    expect(block).toContain("NVDA 7-milestone eval");
    expect(block).toContain("0.912");
    // Content is truncated, not dumped wholesale.
    expect(block).toContain(LONG_CONTENT.slice(0, 100));
    expect(block).not.toContain(LONG_CONTENT);
    expect(block.length).toBeLessThan(2200);
    // No raw JSON structure leaks into the block.
    expect(block).not.toContain('"metadata"');
    expect(block).not.toContain('"neighbors"');
  });

  it("search_knowledge bounds pathological title and doc_key in the citation header", async () => {
    const hugeTitle = "T".repeat(10_000);
    const hugeDocKey = `docs/${"k".repeat(10_000)}`;
    mocks.radonFetch.mockResolvedValue({
      results: [{ ...RESULT_ROW, title: hugeTitle, doc_key: hugeDocKey }],
      retrieval: "hybrid",
    });
    const { executeTool } = await import("@/lib/assistant/tools");

    const result = await executeTool("search_knowledge", { query: "nvda thesis" });

    expect(result.ok).toBe(true);
    const data = result.data as { results: string[] };
    const [header] = data.results[0].split("\n");
    expect(header.length).toBeLessThan(600);
  });

  it("search_knowledge returns an empty formatted payload for zero hits", async () => {
    mocks.radonFetch.mockResolvedValue({ results: [], retrieval: "fts-only" });
    const { executeTool } = await import("@/lib/assistant/tools");

    const result = await executeTool("search_knowledge", { query: "nothing here" });

    expect(result.ok).toBe(true);
    expect(result.data).toMatchObject({ retrieval: "fts-only", count: 0, results: [] });
  });

  it("find_prior_evals uppercases the ticker and requests compact bounded results", async () => {
    mocks.radonFetch.mockResolvedValue({
      ticker: "NVDA",
      results: [RESULT_ROW],
      retrieval: "hybrid",
    });
    const { executeTool } = await import("@/lib/assistant/tools");

    const result = await executeTool("find_prior_evals", { ticker: " nvda " }, "jwt-token");

    expect(result.ok).toBe(true);
    expect(mocks.radonFetch).toHaveBeenCalledTimes(1);
    const [path, opts] = mocks.radonFetch.mock.calls[0];
    expect(path).toBe("/knowledge/prior-evals?ticker=NVDA&limit=6&compact=true");
    expect(opts).toMatchObject({ token: "jwt-token" });

    const data = result.data as { ticker: string; count: number; results: string[] };
    expect(data.ticker).toBe("NVDA");
    expect(data.count).toBe(1);
    expect(data.results[0]).toContain("evals/NVDA-2026-06-01");
    expect(data.results[0]).not.toContain(LONG_CONTENT);
  });

  it("search_knowledge retries once on a 503 and succeeds on the second attempt", async () => {
    const { RadonApiError } = await import("@/lib/radonApi");
    mocks.radonFetch
      .mockRejectedValueOnce(new RadonApiError(503, "Knowledge retrieval is unavailable"))
      .mockResolvedValueOnce({ results: [RESULT_ROW], retrieval: "hybrid" });
    const { executeTool } = await import("@/lib/assistant/tools");

    const result = await executeTool("search_knowledge", { query: "EWY risk reversal" });

    expect(result.ok).toBe(true);
    expect(mocks.radonFetch).toHaveBeenCalledTimes(2);
    const data = result.data as { retrieval: string; count: number; results: string[] };
    expect(data.retrieval).toBe("hybrid");
    expect(data.count).toBe(1);
  });

  it("find_prior_evals retries once on a network failure and succeeds", async () => {
    mocks.radonFetch
      .mockRejectedValueOnce(new TypeError("fetch failed"))
      .mockResolvedValueOnce({ ticker: "EWY", results: [RESULT_ROW], retrieval: "hybrid" });
    const { executeTool } = await import("@/lib/assistant/tools");

    const result = await executeTool("find_prior_evals", { ticker: "EWY" });

    expect(result.ok).toBe(true);
    expect(mocks.radonFetch).toHaveBeenCalledTimes(2);
  });

  it("search_knowledge returns the exact degraded message when both attempts fail", async () => {
    const { RadonApiError } = await import("@/lib/radonApi");
    mocks.radonFetch.mockRejectedValue(new RadonApiError(503, "Knowledge retrieval is unavailable"));
    const { executeTool, KNOWLEDGE_UNAVAILABLE_MESSAGE } = await import("@/lib/assistant/tools");

    const result = await executeTool("search_knowledge", { query: "gex" });

    expect(mocks.radonFetch).toHaveBeenCalledTimes(2);
    expect(result.ok).toBe(false);
    expect(result.error).toBe(KNOWLEDGE_UNAVAILABLE_MESSAGE);
    expect(KNOWLEDGE_UNAVAILABLE_MESSAGE).toContain("temporarily unavailable");
    expect(KNOWLEDGE_UNAVAILABLE_MESSAGE).toContain("do not invent");
    expect(KNOWLEDGE_UNAVAILABLE_MESSAGE).not.toContain("—");
  });

  it("find_prior_evals returns the exact degraded message when both attempts fail", async () => {
    const { RadonApiError } = await import("@/lib/radonApi");
    mocks.radonFetch.mockRejectedValue(new RadonApiError(502, "upstream reset"));
    const { executeTool, KNOWLEDGE_UNAVAILABLE_MESSAGE } = await import("@/lib/assistant/tools");

    const result = await executeTool("find_prior_evals", { ticker: "EWY" });

    expect(mocks.radonFetch).toHaveBeenCalledTimes(2);
    expect(result.ok).toBe(false);
    expect(result.error).toBe(KNOWLEDGE_UNAVAILABLE_MESSAGE);
  });

  it("does not fire a second request after a client-side timeout abort", async () => {
    // AbortSignal.timeout in radonFetch rejects with a DOMException named
    // "TimeoutError". The aborted server-side retrieval is uncancellable and
    // still grinding; an immediate retry would double the orphaned work.
    mocks.radonFetch.mockRejectedValue(
      new DOMException("The operation was aborted due to timeout", "TimeoutError"),
    );
    const { executeTool, KNOWLEDGE_UNAVAILABLE_MESSAGE } = await import("@/lib/assistant/tools");

    const result = await executeTool("search_knowledge", { query: "gex" });

    expect(mocks.radonFetch).toHaveBeenCalledTimes(1);
    expect(result.ok).toBe(false);
    expect(result.error).toBe(KNOWLEDGE_UNAVAILABLE_MESSAGE);
  });

  it("find_prior_evals degrades without a second request on a timeout abort", async () => {
    mocks.radonFetch.mockRejectedValue(
      new DOMException("The operation was aborted due to timeout", "TimeoutError"),
    );
    const { executeTool, KNOWLEDGE_UNAVAILABLE_MESSAGE } = await import("@/lib/assistant/tools");

    const result = await executeTool("find_prior_evals", { ticker: "EWY" });

    expect(mocks.radonFetch).toHaveBeenCalledTimes(1);
    expect(result.ok).toBe(false);
    expect(result.error).toBe(KNOWLEDGE_UNAVAILABLE_MESSAGE);
  });

  it("does not retry a 4xx validation failure and preserves its detail", async () => {
    const { RadonApiError } = await import("@/lib/radonApi");
    mocks.radonFetch.mockRejectedValue(new RadonApiError(422, "query too short"));
    const { executeTool, KNOWLEDGE_UNAVAILABLE_MESSAGE } = await import("@/lib/assistant/tools");

    const result = await executeTool("search_knowledge", { query: "x" });

    expect(mocks.radonFetch).toHaveBeenCalledTimes(1);
    expect(result.ok).toBe(false);
    expect(result.error).toContain("query too short");
    expect(result.error).not.toBe(KNOWLEDGE_UNAVAILABLE_MESSAGE);
  });

  it("delivers the exact degraded message to the model as a tool_result without throwing", async () => {
    const { RadonApiError } = await import("@/lib/radonApi");
    mocks.radonFetch.mockRejectedValue(new RadonApiError(503, "Knowledge retrieval is unavailable"));

    const chat = vi
      .fn()
      .mockResolvedValueOnce({
        provider: "anthropic",
        model: "mock",
        text: "Checking prior theses.",
        toolCalls: [{ id: "tu_kb", name: "search_knowledge", input: { query: "EWY risk reversal" } }],
        stopReason: "tool_use",
      })
      .mockResolvedValueOnce({
        provider: "anthropic",
        model: "mock",
        text: "Knowledge base unavailable; answering without prior context.",
        stopReason: "end_turn",
      });
    vi.doMock("@/lib/llm/provider", () => ({ chat }));

    const { runAssistantLoop } = await import("@/lib/assistant/loop");
    const { KNOWLEDGE_UNAVAILABLE_MESSAGE } = await import("@/lib/assistant/tools");

    const result = await runAssistantLoop(
      [{ role: "user", content: "What was my EWY thesis?" }],
      "system",
    );

    expect(result.content).toBe("Knowledge base unavailable; answering without prior context.");
    expect(result.toolEvents).toEqual([
      expect.objectContaining({ name: "search_knowledge", ok: false, error: KNOWLEDGE_UNAVAILABLE_MESSAGE }),
    ]);

    const secondCallMessages = (chat.mock.calls[1][0] as { messages: Array<{ content: unknown }> }).messages;
    const toolResultTurn = secondCallMessages[secondCallMessages.length - 1];
    const blocks = toolResultTurn.content as Array<{ type: string; content: string }>;
    expect(blocks[0].type).toBe("tool_result");
    expect(JSON.parse(blocks[0].content)).toEqual({ error: KNOWLEDGE_UNAVAILABLE_MESSAGE });
  });

  it("pins the SYSTEM_PROMPT honesty rule for knowledge failures", async () => {
    const { SYSTEM_PROMPT } = await import("@/app/api/assistant/route");

    expect(SYSTEM_PROMPT).toContain("say so plainly");
    expect(SYSTEM_PROMPT).toContain("never fabricate prior theses, lessons, or sizing history");
    expect(SYSTEM_PROMPT).not.toContain("—");
  });
});
