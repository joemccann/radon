"use client";

import { useEffect, useId, useRef, useState } from "react";
import type { DeckKey } from "./AssetCockpit";
import styles from "./GlyphRail.module.css";

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
const MOBILE_PRIMARY: GlyphDef[] = [
  { key: "c", label: "Chain" },
  { key: "p", label: "Position" },
  { key: "n", label: "News" },
  ORDER_GLYPH,
];
const MOBILE_SECONDARY: GlyphDef[] = [
  { key: "r", label: "Ratings" },
  { key: "s", label: "Seasonality" },
  { key: "i", label: "Company" },
  { key: "h", label: "13F holdings" },
  { key: "f", label: "Filings" },
];

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
  const [moreOpen, setMoreOpen] = useState(false);
  const railRef = useRef<HTMLDivElement>(null);
  const moreRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = useId();
  useEffect(() => {
    if (!moreOpen) return;
    menuRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const onPointerDown = (event: PointerEvent) => {
      if (!railRef.current?.contains(event.target as Node)) setMoreOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      // Close this disclosure before the cockpit's document shortcut can close
      // the current deck. Esc returns to the same working instrument surface.
      event.preventDefault();
      event.stopPropagation();
      setMoreOpen(false);
      moreRef.current?.focus();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, [moreOpen]);

  const selectDeck = (key: DeckKey) => {
    if (moreOpen) moreRef.current?.focus();
    setMoreOpen(false);
    onDeckChange(activeDeck === key ? null : key);
  };
  const glyphs: GlyphDef[] = includeOrder
    ? MOBILE_PRIMARY
    : [...SECONDARY_GLYPHS, CMD_GLYPH];
  const secondaryActive = MOBILE_SECONDARY.some((glyph) => glyph.key === activeDeck);
  return (
    <div ref={railRef} className={`glyph-rail ${styles.rail} ${includeOrder ? styles.mobile : ""}`}>
      {glyphs.map((g) => {
        const pressed = activeDeck === g.key;
        const showBadge = g.key === "n" && typeof newsCount === "number" && newsCount > 0;
        return (
          <button
            key={g.key}
            type="button"
            className="glyph"
            aria-label={includeOrder ? g.label : undefined}
            aria-pressed={pressed}
            onClick={() => selectDeck(g.key)}
          >
            {showBadge && <span className="glyph-dot">{`•${newsCount}`}</span>}
            <span className="glyph-k">{g.key}</span>
            <span className="glyph-l">{g.label}</span>
          </button>
        );
      })}
      {includeOrder && (
        <>
          <button
            ref={moreRef}
            type="button"
            className={`glyph ${secondaryActive ? styles.secondaryActive : ""}`}
            aria-label="More instrument tools"
            aria-expanded={moreOpen}
            aria-controls={menuId}
            onClick={() => setMoreOpen((open) => !open)}
          >
            <span className="glyph-l">More</span>
          </button>
          {moreOpen && (
            <div id={menuId} ref={menuRef} className={styles.more} role="group" aria-label="More instrument tools">
              {MOBILE_SECONDARY.map((glyph) => (
                <button key={glyph.key} type="button" aria-pressed={activeDeck === glyph.key} onClick={() => selectDeck(glyph.key)}>
                  {glyph.label}
                  <span aria-hidden="true">↗</span>
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
