"use client";

import { useEffect, useState } from "react";
import Image from "next/image";
import { Menu, X } from "lucide-react";
import { CommandChip } from "@/components/atoms/CommandChip";
import { ThemeToggle } from "@/components/atoms/ThemeToggle";
import { headerLinks } from "@/lib/landing-content";

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus/60 focus-visible:ring-offset-2 focus-visible:ring-offset-canvas";

export function HeaderShell() {
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    if (!menuOpen) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setMenuOpen(false);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [menuOpen]);

  return (
    <header className="fixed inset-x-0 top-0 z-40 border-b border-grid bg-canvas/95">
      <div className="mx-auto flex h-16 w-full max-w-[1440px] items-center justify-between gap-6 px-4 sm:px-6 lg:px-8">
        <a href="#top" className={`flex items-center gap-3 ${focusRing}`}>
          <Image src="/brand/radon-monogram.svg" alt="Radon" width={20} height={20} />
          <span className="font-display text-lg font-semibold uppercase tracking-[0.06em] text-primary">
            Radon
          </span>
        </a>
        <nav aria-label="Primary" className="hidden items-center gap-6 lg:flex">
          {headerLinks.map((item) => (
            <a
              key={item.href}
              href={item.href}
              className={`font-mono text-[11px] uppercase tracking-[0.16em] text-secondary transition-colors hover:text-primary ${focusRing}`}
            >
              {item.label}
            </a>
          ))}
        </nav>
        <div className="flex items-center gap-3">
          <ThemeToggle />
          <a
            href="https://github.com/joemccann/radon"
            target="_blank"
            rel="noopener noreferrer"
            className={`hidden sm:inline-flex ${focusRing}`}
          >
            <CommandChip command="Inspect Source" />
          </a>
          <a
            href="#strategies"
            className={`inline-flex items-center border border-accent bg-accent px-4 py-2 font-mono text-[11px] uppercase tracking-[0.16em] text-canvas transition-colors hover:bg-signal-strong ${focusRing}`}
          >
            Review Strategies
          </a>
          <button
            type="button"
            aria-label="Toggle navigation"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((v) => !v)}
            className={`inline-flex items-center justify-center p-2 text-primary lg:hidden ${focusRing}`}
          >
            {menuOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>
      </div>
      {menuOpen && (
        <nav
          aria-label="Mobile navigation"
          className="border-t border-grid bg-canvas px-4 py-6 lg:hidden"
        >
          <div className="flex flex-col gap-4">
            {headerLinks.map((item) => (
              <a
                key={item.href}
                href={item.href}
                onClick={() => setMenuOpen(false)}
                className={`font-mono text-sm uppercase tracking-[0.16em] text-secondary transition-colors hover:text-primary ${focusRing}`}
              >
                {item.label}
              </a>
            ))}
          </div>
        </nav>
      )}
    </header>
  );
}
