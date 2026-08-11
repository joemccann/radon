"use client";

import type { DeckKey } from "./AssetCockpit";

type GlyphDef = { key: DeckKey; label: string };

const SECONDARY_GLYPHS: GlyphDef[] = [
  { key: "c", label: "Chain" },
  { key: "p", label: "Posn" },
  { key: "n", label: "News" },
  { key: "r", label: "Rate" },
  { key: "s", label: "Seas" },
  { key: "i", label: "Info" },
  { key: "h", label: "13F" },
  { key: "f", label: "File" },
];

const ORDER_GLYPH: GlyphDef = { key: "o", label: "Trade" };
const CMD_GLYPH: GlyphDef = { key: ":", label: "Cmd" };

type GlyphRailProps = {
  activeDeck: DeckKey | null;
  onDeckChange: (deck: DeckKey | null) => void;
  /** Unread-news count for the `n` badge. Omitted/0 renders no badge. */
  newsCount?: number;
  /** Mobile: surface the order-ticket glyph (the desktop act column is dropped)
   *  and drop the keyboard-only command palette. */
  includeOrder?: boolean;
};

export default function GlyphRail({ activeDeck, onDeckChange, newsCount, includeOrder }: GlyphRailProps) {
  const glyphs: GlyphDef[] = includeOrder
    ? [...SECONDARY_GLYPHS, ORDER_GLYPH]
    : [...SECONDARY_GLYPHS, CMD_GLYPH];
  return (
    <div className="glyph-rail">
      {glyphs.map((g) => {
        const pressed = activeDeck === g.key;
        const showBadge = g.key === "n" && typeof newsCount === "number" && newsCount > 0;
        return (
          <button
            key={g.key}
            type="button"
            className="glyph"
            aria-pressed={pressed}
            onClick={() => onDeckChange(pressed ? null : g.key)}
          >
            {showBadge && <span className="glyph-dot">{`•${newsCount}`}</span>}
            <span className="glyph-k">{g.key}</span>
            <span className="glyph-l">{g.label}</span>
          </button>
        );
      })}
    </div>
  );
}
