"use client";

import { Crosshair } from "lucide-react";
import { fmtPrice } from "@/lib/positionUtils";

type ChainSpotBarProps = {
  ticker: string;
  currentPrice: number | null;
  anchorPrice: number | null;
  priceIsClose?: boolean;
  spotMoved: boolean;
  onRecenter: () => void;
};

export default function ChainSpotBar({
  ticker, currentPrice, anchorPrice, priceIsClose = false, spotMoved, onRecenter,
}: ChainSpotBarProps) {
  const available = currentPrice != null && Number.isFinite(currentPrice) && currentPrice > 0;
  return (
    <div className="chain-spot-bar" data-testid="chain-spot-bar">
      <div className={`chain-spot-bar__anchor${spotMoved ? " chain-spot-bar__anchor--moved" : ""}`}>
        {anchorPrice != null
          ? `${spotMoved ? "Spot moved · " : ""}View at ${fmtPrice(anchorPrice)}`
          : "View not centered"}
      </div>
      <div className="chain-spot-bar__quote">
        <span className="chain-spot-bar__symbol">{ticker.toUpperCase()}</span>
        <strong data-testid="chain-spot-price">{available ? fmtPrice(currentPrice) : "---"}</strong>
        <span className="chain-spot-bar__source">
          {!available ? "Underlying unavailable" : priceIsClose ? "Previous close" : "Underlying"}
        </span>
      </div>
      <button
        type="button"
        className="chain-spot-bar__recenter"
        onClick={onRecenter}
        disabled={!available}
        aria-label="Recenter options chain"
      >
        <Crosshair size={16} aria-hidden="true" />
        Recenter
      </button>
    </div>
  );
}
