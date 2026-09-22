import { RevealOnScroll } from "@/components/atoms/RevealOnScroll";

export function ResearchExecutionSection() {
  return (
    <section id="research-execution" className="px-6 py-[clamp(64px,9vw,128px)] sm:px-8">
      <div className="mx-auto grid max-w-[1140px] gap-10 md:grid-cols-[1.1fr_1fr] md:gap-16">
        <RevealOnScroll>
          <h2 className="editorial-thesis mb-6 text-primary">Build the view. Express it in the market.</h2>
          <p className="max-w-[65ch] text-secondary">
            Radon connects scanners, cited research and market-structure indicators
            to an operator-controlled trading workflow. Compare the evidence, choose
            the options, stock or futures expression, and review risk before execution.
          </p>
          <a href="https://app.radon.run/research-workbench" style={{ color: "var(--color-canvas)" }} className="mt-7 inline-flex min-h-11 items-center rounded-lg bg-accent px-5 py-3 font-medium text-canvas transition-opacity hover:opacity-85 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-focus">
            Open the research workbench
          </a>
        </RevealOnScroll>
        <div className="divide-y divide-grid border-y border-grid">
          <div className="py-6">
            <h3 className="mb-2 text-lg font-semibold text-primary">Evidence you can inspect</h3>
            <p className="text-secondary">Keep a figure beside its source passage, fiscal period and assumptions. Carry those references into research briefs and scenario exports.</p>
          </div>
          <div className="py-6">
            <h3 className="mb-2 text-lg font-semibold text-primary">Indicators with an execution path</h3>
            <p className="text-secondary">Flow, volatility and positioning inform the view. Structure selection, portfolio coverage and explicit confirmation govern the order.</p>
          </div>
          <div className="py-6">
            <h3 className="mb-2 text-lg font-semibold text-primary">An agent loop with operator control</h3>
            <p className="text-secondary">Agents retrieve evidence and prepare proposals. The workstation keeps research, review and broker execution connected.</p>
          </div>
        </div>
      </div>
    </section>
  );
}
