import { RevealOnScroll } from "@/components/atoms/RevealOnScroll";
import { SectionHeading } from "@/components/atoms/SectionHeading";
import { ProductPlate } from "@/components/molecules/ProductPlate";
import { auditEntries } from "@/lib/editorial-content";

export function EvidenceSection() {
  return (
    <section id="evidence" className="px-8 py-[clamp(64px,9vw,128px)]">
      <div className="mx-auto max-w-[1140px]">
        <SectionHeading no="Evidence" label="Journal and portfolio" />

        <div className="grid items-start gap-[clamp(28px,4vw,56px)] md:grid-cols-2">
          <RevealOnScroll>
            <h2 className="editorial-thesis mb-[22px] text-primary">
              The journal is the book.
            </h2>
            <p className="mb-[1.05em] text-secondary">
              Every routed contract keeps the chain: flow score, regime read,
              structure, and result. Portfolio and orders both read from that
              journal.
            </p>
            <p className="mb-[1.05em] text-secondary">
              P&amp;L stays on the same row that placed the trade, lot-matched to
              basis.
            </p>

            <div className="mt-[30px] border-t border-grid font-mono text-[12px]">
              {auditEntries.map((entry) => (
                <div
                  key={`${entry.date}-${entry.ticker}`}
                  className="grid grid-cols-[92px_1fr_auto] items-baseline gap-[14px] border-b border-hairline-soft py-3"
                >
                  <span className="text-muted">{entry.date}</span>
                  <span className="text-secondary">
                    <b className="font-medium text-primary">{entry.ticker}</b> {entry.detail}
                  </span>
                  <span
                    className={[
                      "text-right font-medium tabular-nums",
                      entry.positive ? "text-signal-deep" : "text-negative",
                    ].join(" ")}
                  >
                    {entry.result}
                  </span>
                </div>
              ))}
            </div>

            <p className="mt-[18px] flex gap-[14px] font-mono text-[10.5px] uppercase tracking-[0.05em] text-muted">
              <span>
                Source <b className="font-medium text-signal-deep">IB journal</b>
              </span>
              <span>
                Basis <b className="font-medium text-signal-deep">lot-matched</b>
              </span>
              <span>
                R <b className="font-medium text-signal-deep">multiple of risked</b>
              </span>
            </p>
          </RevealOnScroll>

          <RevealOnScroll>
            <ProductPlate
              figNo="Figure 6"
              figTitle="Structure-aware portfolio · live session"
              shot="portfolio"
              lightAlt="Radon portfolio view: net liquidation, day P&L, unrealized P&L, and a table of defined-risk positions with ticker, structure, direction, entry, and P&L."
              darkAlt="Radon portfolio view, dark theme: net liquidation, day P&L, unrealized P&L, and a table of defined-risk positions."
              caption="Same journal that placed the trade. Each row keeps structure, entry, and lot-matched P&L."
            />
          </RevealOnScroll>
        </div>
      </div>
    </section>
  );
}
