"use client";

import type { Headline, HeadlineImpact, HeadlinesStatus } from "@/lib/useHeadlines";

const BRACKET_LEDE = /(【[^【】]*】)/g;
const LEADING_LEDE = /^\s*【([^【】]*)】\s*/;

function splitLeadingLede(content: string): { lede: string | null; body: string } {
  const match = content.match(LEADING_LEDE);
  if (!match) return { lede: null, body: content };
  return { lede: match[1], body: content.slice(match[0].length) };
}

function renderLedes(content: string) {
  return content.split(BRACKET_LEDE).map((part, i) =>
    part.startsWith("【") && part.endsWith("】") ? (
      <strong key={i} className="headlines-tape__lede">
        {part}
      </strong>
    ) : (
      part
    ),
  );
}

function fmtTime(iso: string | null): string {
  if (!iso) return "--";
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return iso;
  return new Date(ms).toLocaleTimeString("en-US", {
    hour12: false,
    timeZone: "America/New_York",
  });
}

function ImpactHit({ hit }: { hit: HeadlineImpact }) {
  const up = hit.impact === "bullish";
  return (
    <span className={`headlines-tape__hit${up ? " headlines-tape__hit--up" : ""}`}>
      {up ? "▲" : "▼"} {hit.symbol}
      {hit.impact ? ` ${hit.impact}` : ""}
    </span>
  );
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
      {newestFirst.map((row) => {
        const { lede, body } = splitLeadingLede(row.content);
        return (
          <li
            key={row.id}
            className="headlines-tape__row"
            data-testid="headlines-tape-row"
            data-important={row.important ? "true" : "false"}
          >
            <div className="headlines-tape__meta">
              <time className="headlines-tape__time" dateTime={row.time ?? undefined}>
                {fmtTime(row.time)}
              </time>
              {row.important ? <span className="headlines-tape__imp">Important</span> : null}
              {row.impact[0] ? <ImpactHit hit={row.impact[0]} /> : null}
            </div>
            {lede ? (
              <h3 className="headlines-tape__headline">{lede}</h3>
            ) : null}
            {body ? (
              <p className={`headlines-tape__body${lede ? "" : " headlines-tape__body--solo"}`}>
                {renderLedes(body)}
              </p>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
