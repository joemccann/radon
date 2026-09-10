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
      "Radon constructs a view from scanners, news, dark-pool flow, and regime models, then expresses it as defined-risk options, stock, or futures. Four gates still sit on the order path. Try it at demo.radon.run.",
  },
  {
    question: "How does Radon read dark-pool flow?",
    answer:
      "Radon takes off-exchange and OTC prints from Unusual Whales and lines them up with the Interactive Brokers tape. Volume is venue-weighted into a rolling z-score. Direction comes from print side and size clustering. You get a flow score and a lead window per ticker.",
  },
  {
    question: "Does Radon work with Interactive Brokers?",
    answer:
      "Yes. Interactive Brokers is the live tape and the place orders go. A structure that clears the pre-trade check becomes an IB combo. Fills land in the journal with lot-matched P&L. Live trading needs a funded IB account. Radon is not a broker.",
  },
  {
    question: "Do I need an Unusual Whales subscription?",
    answer:
      "For live use, yes. Unusual Whales supplies the dark-pool prints and options flow the scores use, next to the Interactive Brokers tape. The demo at demo.radon.run needs neither subscription.",
  },
  {
    question: "How is Radon different from SpotGamma or MenthorQ?",
    answer:
      "SpotGamma and MenthorQ publish dealer-positioning levels. Radon takes MenthorQ levels as one input. It also picks the structure, sizes it with fractional Kelly hard-capped at 2.5% of bankroll, runs four gates, and journals the chain from signal to order.",
  },
  {
    question: "What is GEX and how does Radon use it?",
    answer:
      "GEX is dealer gamma by strike. Positive gamma pins. Negative gamma amplifies. Radon turns that into walls and magnets: levels that hold or pull price, and the strikes you actually trade against.",
  },
  {
    question: "Can I use Radon with Robinhood?",
    answer:
      "No. Robinhood is not wired in, and Radon does not send orders there. You can still open the demo at demo.radon.run. Live use needs Interactive Brokers. Radon is not a broker.",
  },
  {
    question: "Is there a free demo?",
    answer:
      "Yes. demo.radon.run is a full instance with seeded data and no brokerage hookup. You get scanners, news, regime models, structures, sizing, gates, and the journal. No cost.",
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
