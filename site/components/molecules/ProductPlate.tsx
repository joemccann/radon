import { PlateFrame } from "@/components/molecules/PlateFrame";

type ProductPlateProps = {
  figNo: string;
  figTitle: string;
  /** Base name under /plates, e.g. "dashboard" -> dashboard-light.png + dashboard-dark.png */
  shot: string;
  lightAlt: string;
  darkAlt: string;
  caption: string;
  source?: string;
  confidence?: string;
};

// IMPORTANT: the plate PNGs in /public/plates are downscaled captures of a real
// Radon session. The operator identity is anonymized, but the FIGURES (net
// liquidation, day P&L, positions) are still real account data. Swap to demo /
// synthetic data before any public, non-operator launch of radon.run.
//
// The light/dark <img> pair is theme-swapped purely in CSS (.plate-shot rules in
// globals.css) so both render server-side with correct alt text and the active
// theme decides which is visible. Width/height set to the native 1512x862 to
// reserve layout space and avoid CLS. Each capture ships as a lossless WebP
// sibling (~60% lighter) with the archival PNG as the <picture> fallback.
export function ProductPlate({
  figNo,
  figTitle,
  shot,
  lightAlt,
  darkAlt,
  caption,
  source = "Radon live session",
  confidence = "Live data",
}: ProductPlateProps) {
  return (
    <PlateFrame
      variant="shot"
      figNo={figNo}
      figTitle={figTitle}
      caption={caption}
      source={source}
      confidence={confidence}
    >
      {/* CSS theme-swap needs both <img> in the DOM; next/image cannot toggle on data-theme */}
      <picture>
        <source type="image/webp" srcSet={`/plates/${shot}-light.webp`} />
        <img
          className="shot-light"
          loading="lazy"
          src={`/plates/${shot}-light.png`}
          width={1512}
          height={862}
          alt={lightAlt}
        />
      </picture>
      <picture>
        <source type="image/webp" srcSet={`/plates/${shot}-dark.webp`} />
        <img
          className="shot-dark"
          loading="lazy"
          src={`/plates/${shot}-dark.png`}
          width={1512}
          height={862}
          alt={darkAlt}
        />
      </picture>
    </PlateFrame>
  );
}
