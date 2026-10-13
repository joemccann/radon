// @vitest-environment jsdom
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { isHeadlineAboutTicker } from "@/lib/tickerNewsRelevance";
import NewsTab from "@/components/ticker-detail/NewsTab";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("isHeadlineAboutTicker", () => {
  it("drops headlines where a word-ticker is only the English word", () => {
    expect(isHeadlineAboutTicker("GERMAN DOMESTIC SPY CHIEF ON LEIZPIG/HALLE AIRPORT DRONE INCIDENT", "SPY")).toBe(false);
    expect(isHeadlineAboutTicker("US spy chief prepares for greater role in Russia-Ukraine talks - FT", "SPY")).toBe(false);
    expect(isHeadlineAboutTicker("ALLEGED NATO SPY TO REMAIN IN CUSTODY IN BELGIUM", "SPY")).toBe(false);
  });

  it("keeps cashtags, parenthesised symbols and case-exact mentions in mixed-case copy", () => {
    expect(isHeadlineAboutTicker("$SPY and $QQQ can look close on a chart.", "SPY")).toBe(true);
    expect(isHeadlineAboutTicker("S&P 500 ETF (SPY) sees record inflows", "SPY")).toBe(true);
    expect(isHeadlineAboutTicker("SPY options volume spikes into CPI", "SPY")).toBe(true);
  });

  it("keeps all-caps wire copy for tickers that are not English words", () => {
    expect(isHeadlineAboutTicker("NVDA SHARES RISE AFTER EARNINGS", "NVDA")).toBe(true);
  });

  it("keeps items whose headline never names the ticker (provider tagged the entity)", () => {
    expect(isHeadlineAboutTicker("Apple unveils new iPhone lineup", "AAPL")).toBe(true);
  });
});

describe("NewsTab relevance", () => {
  it("hides spy-agency headlines, says how many were hidden, and drops a MAJOR badge every row wears", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        source: "unusualwhales",
        data: [
          { headline: "GERMAN DOMESTIC SPY CHIEF ON AIRPORT DRONE INCIDENT", source: "Tradex", created_at: "2026-10-12T10:00:00Z", is_major: true },
          { headline: "$SPY and $QQQ can look close on a chart.", source: "Tradex", created_at: "2026-08-26T10:00:00Z", is_major: true },
          { headline: "ALLEGED NATO SPY TO REMAIN IN CUSTODY", source: "Tradex", created_at: "2026-08-31T10:00:00Z", is_major: true },
        ],
      }),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NewsTab ticker="SPY" active />);

    expect(await screen.findByText("$SPY and $QQQ can look close on a chart.")).toBeTruthy();
    expect(screen.queryByText(/DOMESTIC SPY CHIEF/)).toBeNull();
    expect(screen.queryByText(/NATO SPY/)).toBeNull();
    expect(screen.getByTestId("news-hidden-note").textContent).toBe(
      "2 headlines hidden: they use \"spy\" as a word, not the symbol.",
    );
    expect(screen.queryByText("MAJOR")).toBeNull();
  });

  it("keeps the MAJOR badge when it distinguishes rows", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        source: "unusualwhales",
        data: [
          { headline: "NVDA guides above consensus", source: "UW", created_at: "2026-10-12T10:00:00Z", is_major: true },
          { headline: "NVDA analyst day scheduled", source: "UW", created_at: "2026-10-11T10:00:00Z", is_major: false },
        ],
      }),
    }));
    render(<NewsTab ticker="NVDA" active />);
    expect(await screen.findByText("NVDA guides above consensus")).toBeTruthy();
    expect(screen.getAllByText("MAJOR")).toHaveLength(1);
    expect(screen.queryByTestId("news-hidden-note")).toBeNull();
  });
});
