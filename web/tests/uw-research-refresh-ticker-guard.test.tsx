// @vitest-environment jsdom
// Audit 2026-11-03 §3.5: UwResearchContext.refresh had no current-ticker
// guard, so a refresh for A that resolved after the operator moved to B
// replaced B's snapshot with A's and B's panel went blank.
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UwResearchProvider, useUwResearch } from "@/lib/UwResearchContext";

const snapshot = (ticker: string, tag: string) => ({
  schema_version: 1,
  ticker,
  source: "unusual_whales",
  stream: { status: "live" },
  research: { status: "ok", sections: {}, tag },
});

class FakeEventSource {
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  addEventListener() {}
  close() {}
}

let refreshFn: (() => Promise<void>) | null = null;
function Probe({ ticker }: { ticker: string }) {
  const { data, refresh } = useUwResearch(ticker);
  refreshFn = refresh;
  const tag = (data?.research as { tag?: string } | undefined)?.tag;
  return <div data-testid="probe">{data ? `${data.ticker}:${tag}` : "none"}</div>;
}

describe("UwResearchProvider.refresh", () => {
  let releasePost!: (res: Response) => void;
  beforeEach(() => {
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (init?.method === "POST") {
          return new Promise<Response>((resolve) => {
            releasePost = resolve;
          });
        }
        const ticker = new URL(url, "http://x").searchParams.get("ticker")!;
        return Promise.resolve(new Response(JSON.stringify(snapshot(ticker, "get"))));
      }),
    );
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("a late refresh for the previous ticker does not replace the current ticker's snapshot", async () => {
    const view = render(
      <UwResearchProvider>
        <Probe ticker="AAPL" />
      </UwResearchProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("probe").textContent).toBe("AAPL:get"));

    let pending!: Promise<void>;
    act(() => {
      pending = refreshFn!();
    });

    view.rerender(
      <UwResearchProvider>
        <Probe ticker="MSFT" />
      </UwResearchProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("probe").textContent).toBe("MSFT:get"));

    await act(async () => {
      releasePost(new Response(JSON.stringify(snapshot("AAPL", "post"))));
      await pending;
    });
    expect(screen.getByTestId("probe").textContent).toBe("MSFT:get");
  });
});
