import Image from "next/image";
import { StatusDot } from "@/components/atoms/StatusDot";
import { TelemetryLabel } from "@/components/atoms/TelemetryLabel";
import { footerColumns } from "@/lib/landing-content";

export function FooterSection() {
  return (
    <footer className="border-t border-grid py-10">
      <div className="grid gap-10 lg:grid-cols-[1.2fr_repeat(2,minmax(0,0.7fr))_auto]">
        <div>
          <div className="flex items-center gap-3">
            <Image src="/brand/radon-monogram.svg" alt="Radon" width={16} height={16} />
            <span className="font-display text-sm font-semibold uppercase tracking-[0.06em] text-primary">
              Radon
            </span>
          </div>
          <p className="mt-4 max-w-xs text-sm leading-6 text-secondary">
            Strategies, execution discipline, and state reconstruction for traders who
            want the machine to stay inspectable.
          </p>
        </div>
        {footerColumns.map((column) => (
          <div key={column.title}>
            <TelemetryLabel>{column.title}</TelemetryLabel>
            <ul className="mt-4 space-y-3">
              {column.links.map((link) => (
                <li key={link.label}>
                  <a
                    href={link.href}
                    target={link.href.startsWith("http") ? "_blank" : undefined}
                    rel={link.href.startsWith("http") ? "noopener noreferrer" : undefined}
                    className="font-mono text-[12px] uppercase tracking-[0.14em] text-secondary transition-colors hover:text-primary"
                  >
                    {link.label}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        ))}
        <div className="lg:justify-self-end">
          <div className="flex items-center gap-3 font-mono text-[11px] uppercase tracking-[0.16em] text-secondary">
            <StatusDot tone="strong" />
            Protocol Nominal
          </div>
          <p className="mt-4 font-mono text-[11px] uppercase tracking-[0.16em] text-muted">
            2026 Radon Terminal
          </p>
        </div>
      </div>
    </footer>
  );
}
