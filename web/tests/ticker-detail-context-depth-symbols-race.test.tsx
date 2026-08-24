/**
 * @vitest-environment jsdom
 *
 * Client-side ticker navigation must publish a depth subject.
 *
 * WorkspaceShell syncs the route param into context via a parent effect
 * (`setActiveTicker(ticker)`); TickerDetailContent publishes the focused book
 * via a child effect (`setDepthSymbols([bookKey])`). React runs child passive
 * effects before the parent's, so a `[]` reset inside setActiveTicker's
 * ticker-change branch lands AFTER the child's publish and wins. depthSymbols
 * then stays empty, usePrices never sends subscribe-depth, and the Book tab
 * degrades to a single-row "L1 BBO" montage with an empty tape.
 */
import React, { useEffect } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { TickerDetailProvider, useTickerDetail } from "@/lib/TickerDetailContext";

let observed: { activeTicker: string | null; depthSymbols: string[] } = {
  activeTicker: null,
  depthSymbols: [],
};

function Subject({ ticker }: { ticker: string }) {
  const { setDepthSymbols, activeTicker, depthSymbols } = useTickerDetail();
  useEffect(() => {
    setDepthSymbols([ticker]);
    return () => setDepthSymbols([]);
  }, [ticker, setDepthSymbols]);
  observed = { activeTicker, depthSymbols };
  return null;
}

function Shell({ ticker }: { ticker: string }) {
  const { setActiveTicker } = useTickerDetail();
  useEffect(() => {
    setActiveTicker(ticker);
  }, [ticker, setActiveTicker]);
  return <Subject ticker={ticker} />;
}

function App({ ticker }: { ticker: string }) {
  return (
    <TickerDetailProvider>
      <Shell ticker={ticker} />
    </TickerDetailProvider>
  );
}

afterEach(cleanup);

describe("TickerDetailContext depth subject vs. setActiveTicker ordering", () => {
  it("keeps the child's depth subject when shell and subject mount in one commit", () => {
    render(<App ticker="AMZN" />);
    expect(observed).toEqual({ activeTicker: "AMZN", depthSymbols: ["AMZN"] });
  });

  it("keeps the new depth subject across client-side ticker navigation", () => {
    const view = render(<App ticker="AMZN" />);
    view.rerender(<App ticker="NVDA" />);
    expect(observed).toEqual({ activeTicker: "NVDA", depthSymbols: ["NVDA"] });
    view.rerender(<App ticker="AMZN" />);
    expect(observed).toEqual({ activeTicker: "AMZN", depthSymbols: ["AMZN"] });
  });

  it("clears the depth subject when leaving ticker detail", () => {
    function Leave({ ticker }: { ticker: string | null }) {
      const { setActiveTicker } = useTickerDetail();
      useEffect(() => {
        setActiveTicker(ticker);
      }, [ticker, setActiveTicker]);
      return ticker ? <Subject ticker={ticker} /> : null;
    }
    const view = render(
      <TickerDetailProvider>
        <Leave ticker="AMZN" />
      </TickerDetailProvider>,
    );
    view.rerender(
      <TickerDetailProvider>
        <Leave ticker={null} />
      </TickerDetailProvider>,
    );
    // Subject unmounted, so observe via a fresh probe.
    function Probe() {
      const { activeTicker, depthSymbols } = useTickerDetail();
      observed = { activeTicker, depthSymbols };
      return null;
    }
    view.rerender(
      <TickerDetailProvider>
        <Leave ticker={null} />
        <Probe />
      </TickerDetailProvider>,
    );
    expect(observed).toEqual({ activeTicker: null, depthSymbols: [] });
  });
});
