"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Share2 } from "lucide-react";
import { useDismissablePopover } from "@/lib/useDismissablePopover";
import { formatHoldDuration } from "@/lib/holdTime";

const POPOVER_EXIT_MS = 120;
const POPOVER_EXIT_REDUCED_MS = 80;

export type SharePnlData = {
  description: string;
  pnl: number;
  pnlPct: number | null;
  commission: number | null;
  fillPrice: number | null;
  entryPrice: number | null;
  exitPrice: number | null;
  entryTime: string | null;
  exitTime: string | null;
  time: string; // Legacy field, kept for backward compatibility
};

type SharePnlButtonProps = {
  data: SharePnlData;
  size?: number;
};

function fmtDollar(v: number): string {
  const abs = Math.abs(v);
  const sign = v >= 0 ? "+" : "-";
  return `${sign}$${abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtPct(v: number): string {
  const sign = v >= 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

/** Prefix the first bare ticker symbol in the description with $ (cashtag).
 *  Matches the first word that looks like a ticker (1-5 uppercase letters)
 *  appearing after a leading action word (Closed, Opened, Long, Short, Bought, Sold, Cancelled). */
function cashtagTicker(desc: string): string {
  return desc.replace(
    /^(Closed|Opened|Long|Short|Bought|Sold|Cancelled)\s+([A-Z]{1,5})\b/,
    "$1 $$$2",
  );
}

export function buildTweetText(
  description: string,
  pnl: number,
  pnlPct: number | null,
  showDollar: boolean,
  showPct: boolean,
  holdTime?: string | null,
): string {
  const parts: string[] = [];
  if (showDollar) parts.push(fmtDollar(pnl));
  if (showPct && pnlPct != null && Number.isFinite(pnlPct)) parts.push(fmtPct(pnlPct));
  const pnlStr = parts.join(" ");
  const tagged = cashtagTicker(description);
  // pnl and hold time join with " · "; either may be empty without a stray separator.
  const metric = [pnlStr, holdTime ? `Held ${holdTime}` : ""].filter(Boolean).join(" · ");
  return `💸 ${tagged} ${metric}\n\nExecuted with Radon\n\nhttps://radon.run`;
}

export default function SharePnlButton({ data, size = 13 }: SharePnlButtonProps) {
  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [exiting, setExiting] = useState(false);
  const [showDollar, setShowDollar] = useState(false);
  const [showPct, setShowPct] = useState(true);
  const [copying, setCopying] = useState(false);
  const [copied, setCopied] = useState(false);
  const popoverRef = useRef<HTMLDivElement>(null);
  const exitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const close = useCallback(() => setOpen(false), []);
  useDismissablePopover(popoverRef, close, open);

  // Keep the popover mounted through a short exit transition so open/close
  // retargets via CSS (not keyframe restart).
  useEffect(() => {
    if (open) {
      if (exitTimerRef.current) {
        clearTimeout(exitTimerRef.current);
        exitTimerRef.current = null;
      }
      setMounted(true);
      setExiting(false);
      return;
    }
    if (!mounted) return;
    setExiting(true);
    const reduced =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const ms = reduced ? POPOVER_EXIT_REDUCED_MS : POPOVER_EXIT_MS;
    exitTimerRef.current = setTimeout(() => {
      setMounted(false);
      setExiting(false);
      exitTimerRef.current = null;
    }, ms);
    return () => {
      if (exitTimerRef.current) {
        clearTimeout(exitTimerRef.current);
        exitTimerRef.current = null;
      }
    };
  }, [open, mounted]);

  const generateImage = useCallback(async () => {
    const params = new URLSearchParams();
    params.set("description", data.description);
    if (showDollar) params.set("pnl", String(data.pnl));
    if (showPct && data.pnlPct != null) params.set("pnlPct", String(data.pnlPct));
    // Note: commission is intentionally NOT passed to the image API
    if (data.entryPrice != null) params.set("entryPrice", String(data.entryPrice));
    if (data.exitPrice != null) params.set("exitPrice", String(data.exitPrice));
    if (data.entryTime) params.set("entryTime", data.entryTime);
    if (data.exitTime) params.set("exitTime", data.exitTime);
    const holdTime = formatHoldDuration(data.entryTime, data.exitTime);
    if (holdTime) params.set("holdTime", holdTime);
    if (data.fillPrice != null && data.entryPrice == null && data.exitPrice == null) {
      params.set("fillPrice", String(data.fillPrice));
    }
    if (data.time) params.set("time", data.time);

    const res = await fetch(`/api/share/pnl?${params.toString()}`);
    if (!res.ok) throw new Error("Failed to generate image");
    return res.blob();
  }, [data, showDollar, showPct]);

  const copyToClipboard = useCallback(async (blob: Blob) => {
    await navigator.clipboard.write([
      new ClipboardItem({ "image/png": blob }),
    ]);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, []);

  const handleCopy = useCallback(async () => {
    if (copying) return;
    setCopying(true);
    try {
      const blob = await generateImage();
      await copyToClipboard(blob);
    } catch (err) {
      console.error("Share PnL copy failed:", err);
    } finally {
      setCopying(false);
      setOpen(false);
    }
  }, [copying, generateImage, copyToClipboard]);

  const handleCopyAndTweet = useCallback(async () => {
    if (copying) return;
    setCopying(true);
    try {
      const blob = await generateImage();
      await copyToClipboard(blob);
      const text = buildTweetText(
        data.description,
        data.pnl,
        data.pnlPct,
        showDollar,
        showPct,
        formatHoldDuration(data.entryTime, data.exitTime),
      );
      const tweetUrl = `https://twitter.com/intent/tweet?text=${encodeURIComponent(text)}`;
      window.open(tweetUrl, "_blank", "noopener,noreferrer");
    } catch (err) {
      console.error("Share PnL tweet failed:", err);
    } finally {
      setCopying(false);
      setOpen(false);
    }
  }, [copying, generateImage, copyToClipboard, data, showDollar, showPct]);

  return (
    <div style={{ position: "relative", display: "inline-flex" }} ref={popoverRef}>
      <button
        type="button"
        className="share-pnl-button"
        onClick={() => setOpen((prev) => !prev)}
        title="Share P&L"
        aria-expanded={open}
        aria-haspopup="dialog"
      >
        <Share2 size={size} />
      </button>

      {mounted ? (
        <div
          className={`share-pnl-popover${exiting ? " share-pnl-popover--exiting" : ""}`}
          role="dialog"
          aria-label="Share options"
        >
          <div className="share-pnl-popover-title">Share Options</div>
          <label className="share-pnl-checkbox">
            <input
              type="checkbox"
              checked={showDollar}
              onChange={(e) => setShowDollar(e.target.checked)}
            />
            <span>P&amp;L $</span>
          </label>
          <label className="share-pnl-checkbox">
            <input
              type="checkbox"
              checked={showPct}
              onChange={(e) => setShowPct(e.target.checked)}
            />
            <span>P&amp;L %</span>
          </label>
          <div className="share-pnl-popover-actions">
            <button
              type="button"
              className="btn-primary share-pnl-action"
              onClick={handleCopyAndTweet}
              disabled={copying || (!showDollar && !showPct)}
            >
              {copying ? "Generating..." : "Copy & Tweet"}
            </button>
            <button
              type="button"
              className="btn-secondary share-pnl-action"
              onClick={handleCopy}
              disabled={copying || (!showDollar && !showPct)}
            >
              {copied ? "Copied!" : "Copy"}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
