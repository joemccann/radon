"use client";

import { useCallback, useState } from "react";
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

export default function SharePnlButton({ data, size = 13 }: SharePnlButtonProps) {
  const [copying, setCopying] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleShare = useCallback(async () => {
    if (copying) return;
    setCopying(true);
    try {
      const params = new URLSearchParams();
      params.set("description", data.description);
      params.set("pnl", String(data.pnl));
      if (data.pnlPct != null) params.set("pnlPct", String(data.pnlPct));
      if (data.commission != null) params.set("commission", String(data.commission));
      if (data.fillPrice != null) params.set("fillPrice", String(data.fillPrice));
      if (data.time) params.set("time", data.time);

      const res = await fetch(`/api/share/pnl?${params.toString()}`);
      if (!res.ok) throw new Error("Failed to generate image");

      const blob = await res.blob();
      await navigator.clipboard.write([
        new ClipboardItem({ "image/png": blob }),
      ]);

      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error("Share PnL failed:", err);
    } finally {
      setCopying(false);
    }
  }, [copying, data]);

  return (
    <button
      className="share-pnl-button"
      onClick={handleShare}
      disabled={copying}
      title={copied ? "Copied to clipboard!" : "Copy P&L card to clipboard"}
    >
      <Share2 size={size} />
      {copied && <span className="share-pnl-toast">Copied!</span>}
    </button>
  );
}
