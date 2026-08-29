"use client";

import type { Headline, HeadlinesStatus } from "@/lib/useHeadlines";

function fmtTime(iso: string | null): string {
  if (!iso) return "--";
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return iso;
  return new Date(ms).toLocaleTimeString("en-US", {
    hour12: false,
    timeZone: "America/New_York",
  });
}

export default function HeadlinesTape({
  items,
  status,
}: {
  items: Headline[];
  status: HeadlinesStatus;
}) {
  if (status === "connecting" && items.length === 0) {
    return <div className="news-feed-empty">Connecting to headlines…</div>;
  }
  if (status === "down" && items.length === 0) {
    return <div className="news-feed-error">Headlines feed unavailable.</div>;
  }
  if (items.length === 0) {
    return <div className="news-feed-empty">Waiting for headline prints.</div>;
  }

  const newestFirst = [...items].reverse();
  return (
    <ol className="headlines-tape" data-testid="headlines-tape">
      {newestFirst.map((row) => (
        <li
          key={row.id}
          className="headlines-tape__row"
          data-testid="headlines-tape-row"
          data-important={row.important ? "true" : "false"}
        >
          <time className="headlines-tape__time" dateTime={row.time ?? undefined}>
            {fmtTime(row.time)}
          </time>
          <p className="headlines-tape__body">
            {row.important ? <span className="headlines-tape__imp">Important</span> : null}
            {row.content}
          </p>
          {row.impact[0] ? (
            <span
              className={`headlines-tape__hit${row.impact[0].impact === "bullish" ? " headlines-tape__hit--up" : ""}`}
            >
              {row.impact[0].symbol}
              {row.impact[0].impact ? ` ${row.impact[0].impact}` : ""}
            </span>
          ) : null}
        </li>
      ))}
    </ol>
  );
}
