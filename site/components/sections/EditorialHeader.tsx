"use client";

import { useEffect, useId, useState } from "react";
import { CtaBeam } from "@/components/atoms/CtaBeam";
import { ThemeToggle } from "@/components/atoms/ThemeToggle";
import { editorialNavLinks, DEMO_URL } from "@/lib/editorial-content";

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/60 focus-visible:ring-offset-2 focus-visible:ring-offset-canvas";

export function EditorialHeader() {
  const [open, setOpen] = useState(false);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <header className="sticky top-0 z-50 border-b border-hairline-soft bg-canvas/92 backdrop-blur-[8px]">
      <div className="mx-auto flex h-[72px] w-full max-w-[1140px] items-center justify-between gap-4 px-5 sm:px-8">
        <a href="/#top" className={`flex min-h-11 items-center gap-2.5 ${focusRing}`}>
          <span
            aria-hidden="true"
            className="relative inline-block h-3 w-3 rounded-full border-[1.5px] border-accent after:absolute after:inset-[3px] after:rounded-full after:bg-accent after:content-['']"
          />
          <span className="font-sans text-[28px] font-[650] tracking-[-1.4px] text-primary">
            Radon
          </span>
        </a>
        <nav aria-label="Primary navigation" className="hidden items-center gap-7 lg:flex">
          {editorialNavLinks.map((link) => (
            <a
              key={link.href}
              href={link.href}
              className={`min-h-11 inline-flex items-center font-sans text-[14px] font-[550] text-secondary transition-colors hover:text-primary ${focusRing}`}
            >
              {link.label}
            </a>
          ))}
        </nav>
        <div className="flex items-center gap-2">
          <CtaBeam>
            <a
              href={DEMO_URL}
              className={`inline-flex min-h-11 items-center rounded-[8px] bg-accent px-3.5 font-sans text-[13px] font-medium text-canvas transition-colors hover:bg-signal-deep ${focusRing}`}
            >
              Try the demo
            </a>
          </CtaBeam>
          <ThemeToggle />
          <button
            type="button"
            className={`inline-flex min-h-11 min-w-11 items-center justify-center rounded-[8px] text-primary lg:hidden ${focusRing}`}
            aria-expanded={open}
            aria-controls={menuId}
            aria-label={open ? "Close navigation" : "Open more navigation"}
            onClick={() => setOpen((value) => !value)}
          >
            <span aria-hidden="true" className="font-sans text-[13px] font-medium">
              {open ? "Close" : "Menu"}
            </span>
          </button>
        </div>
      </div>
      {open ? (
        <nav
          id={menuId}
          aria-label="Overflow navigation"
          className="border-t border-hairline-soft bg-canvas px-5 py-4 lg:hidden"
        >
          <div className="mx-auto flex max-w-[1140px] flex-col">
            {editorialNavLinks.map((link) => (
              <a
                key={link.href}
                href={link.href}
                onClick={() => setOpen(false)}
                className={`inline-flex min-h-11 items-center font-sans text-[15px] text-secondary ${focusRing}`}
              >
                {link.label}
              </a>
            ))}
          </div>
        </nav>
      ) : null}
    </header>
  );
}
