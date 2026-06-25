import type { ReactNode } from "react";

type PlateFrameProps = {
  figNo: string;
  figTitle: string;
  caption: ReactNode;
  source: string;
  confidence: string;
  children: ReactNode;
  /** Use `shot` for full-bleed screenshots, `body` for inline figures/tables. */
  variant?: "body" | "shot";
};

// The journal plate is the editorial identity: bordered frame, mono header
// (fig number + title), the exhibit, then an italic-serif caption paired with a
// mono source/confidence rail. Colors come from theme tokens via globals.css.
export function PlateFrame({
  figNo,
  figTitle,
  caption,
  source,
  confidence,
  children,
  variant = "body",
}: PlateFrameProps) {
  return (
    <figure className="plate">
      <div className="plate-head">
        <span className="font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-signal-deep">
          {figNo}
        </span>
        <span className="font-mono text-[12px] tracking-[0.02em] text-secondary">
          {figTitle}
        </span>
      </div>
      <div className={variant === "shot" ? "plate-shot" : "plate-body"}>
        {children}
      </div>
      <figcaption>
        <span className="max-w-[64ch] font-serif text-[14.5px] italic leading-[1.45] text-secondary">
          {caption}
        </span>
        <span className="flex flex-shrink-0 gap-[14px] whitespace-nowrap font-mono text-[10.5px] uppercase tracking-[0.05em] text-muted">
          <span>
            Source <b className="font-medium text-signal-deep">{source}</b>
          </span>
          <span>
            Confidence <b className="font-medium text-signal-deep">{confidence}</b>
          </span>
        </span>
      </figcaption>
    </figure>
  );
}
