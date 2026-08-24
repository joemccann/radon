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

/**
 * Same parent/child ordering hazard for the other per-instrument fields.
 * `depthFutureExpiry` is published by FuturesOrderForm's mount effect (with a
 * cleanup that publishes null) — a reset inside setActiveTicker lands after it
 * and the relay resolves the front month instead of the selected future.
 * `focusedBookKey` is pinned by the user and cleared by the SUBJECT on ticker
 * change; a shell-side reset must not be what keeps it from leaking.
 */
describe("TickerDetailContext per-instrument fields vs. setActiveTicker ordering", () => {
  let seen: { depthFutureExpiry: string | null; focusedBookKey: string | null } = {
    depthFutureExpiry: null,
    focusedBookKey: null,
  };

  function FuturesSubject({ ticker, expiry }: { ticker: string; expiry: string }) {
    const { setDepthFutureExpiry, setFocusedBookKey, depthFutureExpiry, focusedBookKey } = useTickerDetail();
    useEffect(() => {
      setDepthFutureExpiry(expiry);
      return () => setDepthFutureExpiry(null);
    }, [expiry, setDepthFutureExpiry]);
    useEffect(() => {
      setFocusedBookKey(`${ticker}-LEG`);
      return () => setFocusedBookKey(null);
    }, [ticker, setFocusedBookKey]);
    seen = { depthFutureExpiry, focusedBookKey };
    return null;
  }

  function FuturesShell({ ticker, expiry }: { ticker: string; expiry: string }) {
    const { setActiveTicker } = useTickerDetail();
    useEffect(() => {
      setActiveTicker(ticker);
    }, [ticker, setActiveTicker]);
    return <FuturesSubject ticker={ticker} expiry={expiry} />;
  }

  it("keeps a subject-published future expiry and focused key when shell and subject commit together", () => {
    render(
      <TickerDetailProvider>
        <FuturesShell ticker="VIX" expiry="20260916" />
      </TickerDetailProvider>,
    );
    expect(seen).toEqual({ depthFutureExpiry: "20260916", focusedBookKey: "VIX-LEG" });
  });

  it("keeps the new subject's values across client-side ticker navigation", () => {
    const view = render(
      <TickerDetailProvider>
        <FuturesShell ticker="VIX" expiry="20260916" />
      </TickerDetailProvider>,
    );
    view.rerender(
      <TickerDetailProvider>
        <FuturesShell ticker="SPX" expiry="20261218" />
      </TickerDetailProvider>,
    );
    expect(seen).toEqual({ depthFutureExpiry: "20261218", focusedBookKey: "SPX-LEG" });
  });
});
