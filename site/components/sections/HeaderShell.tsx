import Image from "next/image";
import { CommandChip } from "@/components/atoms/CommandChip";
import { headerLinks } from "@/lib/landing-content";

export function HeaderShell() {
  return (
    <header className="fixed inset-x-0 top-0 z-40 border-b border-grid bg-canvas/95">
      <div className="mx-auto flex h-16 w-full max-w-[1440px] items-center justify-between gap-6 px-4 sm:px-6 lg:px-8">
        <a href="#top" className="flex items-center gap-3">
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
              className="font-mono text-[11px] uppercase tracking-[0.18em] text-secondary transition-colors hover:text-primary"
            >
              {item.label}
            </a>
          ))}
        </nav>
        <div className="flex items-center gap-3">
          <a
            href="https://github.com/joemccann/radon"
            target="_blank"
            rel="noopener noreferrer"
            className="hidden sm:inline-flex"
          >
            <CommandChip command="Inspect Source" />
          </a>
          <a
            href="#strategies"
            className="inline-flex items-center border border-accent bg-accent px-4 py-2 font-mono text-[11px] uppercase tracking-[0.18em] text-canvas transition-colors hover:bg-signal-strong"
          >
            Inspect Strategies
          </a>
        </div>
      </div>
    </header>
  );
}
