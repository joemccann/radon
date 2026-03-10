"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Share2 } from "lucide-react";

export type SharePnlData = {
  description: string;
  pnl: number;
  pnlPct: number | null;
  commission: number | null;
  fillPrice: number | null;
  time: string;
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

export function buildTweetText(
  description: string,
  pnl: number,
  pnlPct: number | null,
  showDollar: boolean,
  showPct: boolean,
): string {
  const parts: string[] = [];
  if (showDollar) parts.push(fmtDollar(pnl));
  if (showPct && pnlPct != null && Number.isFinite(pnlPct)) parts.push(fmtPct(pnlPct));
  const pnlStr = parts.join(" ");
  return `${description} ${pnlStr}\n\nExecuted with @raaborern`;
}

export default function SharePnlButton({ data, size = 13 }: SharePnlButtonProps) {
  const [open, setOpen] = useState(false);
  const [showDollar, setShowDollar] = useState(true);
  const [showPct, setShowPct] = useState(true);
  const [copying, setCopying] = useState(false);
  const [copied, setCopied] = useState(false);
  const popoverRef = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    // Delay listener to avoid the opening click from immediately closing
    const id = setTimeout(() => document.addEventListener("mousedown", handler), 0);
    return () => {
      clearTimeout(id);
      document.removeEventListener("mousedown", handler);
    };
  }, [open]);

  const generateImage = useCallback(async () => {
    const params = new URLSearchParams();
    params.set("description", data.description);
    if (showDollar) params.set("pnl", String(data.pnl));
    if (showPct && data.pnlPct != null) params.set("pnlPct", String(data.pnlPct));
    if (data.commission != null) params.set("commission", String(data.commission));
    if (data.fillPrice != null) params.set("fillPrice", String(data.fillPrice));
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
      const text = buildTweetText(data.description, data.pnl, data.pnlPct, showDollar, showPct);
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
        className="share-pnl-button"
        onClick={() => setOpen(!open)}
        title="Share P&L"
      >
        <Share2 size={size} />
      </button>

      {open && (
        <div className="share-pnl-popover">
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
              className="btn-primary share-pnl-action"
              onClick={handleCopyAndTweet}
              disabled={copying || (!showDollar && !showPct)}
            >
              {copying ? "Generating..." : "Copy & Tweet"}
            </button>
            <button
              className="btn-secondary share-pnl-action"
              onClick={handleCopy}
              disabled={copying || (!showDollar && !showPct)}
            >
              {copied ? "Copied!" : "Copy"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
