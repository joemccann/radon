"use client";

import { forwardRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { X } from "lucide-react";
import TickerSearch from "@/components/TickerSearch";

type MobileTickerSearchProps = {
  open: boolean;
  onClose: () => void;
};

/**
 * Full-screen ticker search overlay for mobile. Wraps the existing TickerSearch
 * input + dropdown so the WS streaming search results carry over from desktop.
 * On select, navigates to the ticker detail page and closes the overlay.
 */
const MobileTickerSearch = forwardRef<HTMLInputElement, MobileTickerSearchProps>(function MobileTickerSearch(
  { open, onClose },
  inputRef,
) {
  const router = useRouter();

  useEffect(() => {
    if (!open) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKey);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", handleKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  if (!open) return null;

  const handleSelect = (symbol: string) => {
    onClose();
    router.push(`/${symbol.toUpperCase()}`);
  };

  return (
    <div className="mobile-search-root" role="dialog" aria-modal="true" data-testid="mobile-ticker-search">
      <div className="mobile-search-bar">
        <TickerSearch
          ref={inputRef}
          onSelect={handleSelect}
          placeholder="Search ticker"
          className="mobile-search-input"
        />
        <button
          type="button"
          className="mobile-search-close"
          onClick={onClose}
          aria-label="Close search"
          data-testid="mobile-ticker-search-close"
        >
          <X size={20} aria-hidden />
        </button>
      </div>
    </div>
  );
});

export default MobileTickerSearch;
