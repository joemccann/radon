// Single source of truth for the on-page FAQ and the FAQPage JSON-LD,
// so the schema always mirrors the visible copy.

export type FaqEntry = {
  question: string;
  answer: string;
};

export const faqEntries: FaqEntry[] = [
  {
    question: "What is Radon?",
    answer:
      "Radon is a market-structure research terminal. It reconstructs institutional accumulation and distribution from dark-pool and OTC prints, scores the flow before the lit price moves, and routes only defined-risk options structures that clear four sequential gates. A free demo runs at demo.radon.run.",
  },
  {
    question: "How does Radon read dark-pool flow?",
    answer:
      "Radon ingests off-exchange and OTC prints from Unusual Whales and reconciles them against the Interactive Brokers realtime tape. Volume is venue-weighted and normalized to a rolling z-score, and directional pressure is inferred from print-side and size clustering. The output is a flow score with a measured lead window per ticker.",
  },
  {
    question: "Does Radon work with Interactive Brokers?",
    answer:
      "Yes. Interactive Brokers is Radon's realtime data and execution backbone. Cleared structures are assembled into IB combo orders after a mandatory pre-trade risk check, and every fill is journaled with lot-matched P&L. Live trading requires a funded Interactive Brokers account; Radon itself is not a broker.",
  },
  {
    question: "Do I need an Unusual Whales subscription?",
    answer:
      "For live use, yes. Unusual Whales supplies the dark-pool prints and options flow that Radon's flow scoring is built on, alongside the Interactive Brokers tape. The free demo at demo.radon.run requires no subscription to either service.",
  },
  {
    question: "How is Radon different from SpotGamma or MenthorQ?",
    answer:
      "SpotGamma and MenthorQ publish dealer-positioning levels as dashboards; Radon consumes MenthorQ levels as one data source. What Radon adds is discipline around the models: CRI, GEX, VCG-R, and GRG regime reads paired with four gates, fractional Kelly sizing hard-capped at 2.5% of bankroll per position, and a journaled audit trail from signal to routed order.",
  },
  {
    question: "What is GEX and how does Radon use it?",
    answer:
      "GEX is aggregate dealer gamma exposure by strike. Positive gamma pins and dampens price movement; negative gamma amplifies it. Radon maps GEX into walls and magnets, which set realistic targets and mark the strikes where market structure resists or accelerates price.",
  },
  {
    question: "Can I use Radon with Robinhood?",
    answer:
      "No. Robinhood is not integrated, and Radon does not route orders through it. A Robinhood user can explore the free demo at demo.radon.run, but live use requires an Interactive Brokers account. Radon is a research instrument, not a broker.",
  },
  {
    question: "Is there a free demo?",
    answer:
      "Yes. demo.radon.run is a full demo instance with seeded data and no brokerage connection required. It shows the flow scanner, the four regime models, the gate discipline, and the trade journal at no cost.",
  },
];

export type FaqQuestionSchema = {
  "@type": "Question";
  name: string;
  acceptedAnswer: {
    "@type": "Answer";
    text: string;
  };
};

export type FaqPageSchema = {
  "@context": "https://schema.org";
  "@type": "FAQPage";
  mainEntity: FaqQuestionSchema[];
};

export function faqStructuredData(): FaqPageSchema {
  return {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: faqEntries.map((entry) => ({
      "@type": "Question",
      name: entry.question,
      acceptedAnswer: {
        "@type": "Answer",
        text: entry.answer,
      },
    })),
  };
}
