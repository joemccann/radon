export type PublisherBrand = {
  id: string;
  name: string;
  iconUrl: string;
  isFallback: boolean;
};

export const DEFAULT_FALLBACK_ICON = "/icons/publishers/default-pdf.svg";

interface PublisherRule {
  id: string;
  name: string;
  iconUrl: string;
  pattern: RegExp;
}

const PUBLISHER_RULES: PublisherRule[] = [
  {
    id: "goldman-sachs",
    name: "Goldman Sachs",
    iconUrl: "/icons/publishers/goldman-sachs.svg",
    pattern: /\bgoldman(?:\s*sachs)?\b/i,
  },
  {
    id: "bank-of-america",
    name: "Bank of America",
    iconUrl: "/icons/publishers/bank-of-america.svg",
    pattern: /\b(?:bofa|bank\s*of\s*america|merrill(?:\s*lynch)?)\b/i,
  },
  {
    id: "jpmorgan",
    name: "J.P. Morgan",
    iconUrl: "/icons/publishers/jpmorgan.svg",
    pattern: /\b(?:j\.?p\.?\s*morgan|jpm(?:\s*us|\s*positioning)?)\b/i,
  },
  {
    id: "morgan-stanley",
    name: "Morgan Stanley",
    iconUrl: "/icons/publishers/morgan-stanley.svg",
    pattern: /\bmorgan\s*stanley\b/i,
  },
  {
    id: "deutsche-bank",
    name: "Deutsche Bank",
    iconUrl: "/icons/publishers/deutsche-bank.svg",
    pattern: /\bdeutsche(?:\s*bank)?\b/i,
  },
  {
    id: "citi",
    name: "Citi",
    iconUrl: "/icons/publishers/citi.svg",
    pattern: /\b(?:citi(?:group)?|citibank)\b/i,
  },
  {
    id: "ubs",
    name: "UBS",
    iconUrl: "/icons/publishers/ubs.svg",
    pattern: /\bubs\b/i,
  },
  {
    id: "mizuho",
    name: "Mizuho",
    iconUrl: "/icons/publishers/mizuho.svg",
    pattern: /\bmizuho\b/i,
  },
  {
    id: "barclays",
    name: "Barclays",
    iconUrl: "/icons/publishers/barclays.svg",
    pattern: /\bbarclays\b/i,
  },
  {
    id: "nomura",
    name: "Nomura",
    iconUrl: "/icons/publishers/nomura.svg",
    pattern: /\bnomura\b/i,
  },
  {
    id: "mufg",
    name: "MUFG",
    iconUrl: "/icons/publishers/mufg.svg",
    pattern: /\b(?:mufg|mitsubishi(?:\s*ufj)?)\b/i,
  },
  {
    id: "bnp-paribas",
    name: "BNP Paribas",
    iconUrl: "/icons/publishers/bnp-paribas.svg",
    pattern: /\bbnp(?:\s*paribas)?\b/i,
  },
  {
    id: "standard-chartered",
    name: "Standard Chartered",
    iconUrl: "/icons/publishers/standard-chartered.svg",
    pattern: /\bstandard\s*chartered\b/i,
  },
  {
    id: "rbc",
    name: "RBC Capital Markets",
    iconUrl: "/icons/publishers/rbc.svg",
    pattern: /\b(?:rbc|royal\s*bank\s*of\s*canada)\b/i,
  },
  {
    id: "canaccord",
    name: "Canaccord Genuity",
    iconUrl: "/icons/publishers/canaccord.svg",
    pattern: /\bcanaccord(?:\s*genuity)?\b/i,
  },
  {
    id: "btig",
    name: "BTIG",
    iconUrl: "/icons/publishers/btig.svg",
    pattern: /\bbtig\b/i,
  },
  {
    id: "apollo",
    name: "Apollo",
    iconUrl: "/icons/publishers/apollo.svg",
    pattern: /\bapollo\b/i,
  },
  {
    id: "wells-fargo",
    name: "Wells Fargo",
    iconUrl: "/icons/publishers/wells-fargo.svg",
    pattern: /\bwells\s*fargo\b/i,
  },
  {
    id: "jefferies",
    name: "Jefferies",
    iconUrl: "/icons/publishers/jefferies.svg",
    pattern: /\bjefferies\b/i,
  },
  {
    id: "raymond-james",
    name: "Raymond James",
    iconUrl: "/icons/publishers/raymond-james.svg",
    pattern: /\braymond\s*james\b/i,
  },
  {
    id: "scotiabank",
    name: "Scotiabank",
    iconUrl: "/icons/publishers/scotiabank.svg",
    pattern: /\b(?:scotiabank|bank\s*of\s*nova\s*scotia|scotia\s*capital)\b/i,
  },
  {
    id: "pictet",
    name: "Pictet",
    iconUrl: "/icons/publishers/pictet.svg",
    pattern: /\b(?:pictet|banque\s*pictet)\b/i,
  },
  {
    id: "seb",
    name: "SEB",
    iconUrl: "/icons/publishers/seb.svg",
    pattern: /\b(?:seb|skandinaviska\s*enskilda\s*banken)\b/i,
  },
  {
    id: "syz",
    name: "Banque Syz",
    iconUrl: "/icons/publishers/syz.svg",
    pattern: /\b(?:syz|banque\s*syz)\b/i,
  },
  {
    id: "unicredit",
    name: "UniCredit",
    iconUrl: "/icons/publishers/unicredit.svg",
    pattern: /\b(?:unicredit|hypovereinsbank)\b/i,
  },
];

/**
 * Resolves a raw publisher string (e.g. from PDF metadata or desk attribution)
 * to a canonical institutional brand with icon URL and fallback information.
 */
export function resolvePublisher(rawPublisher?: string | null): PublisherBrand {
  const trimmed = (rawPublisher ?? "").trim();
  const isUnknown = !trimmed || /^unknown$/i.test(trimmed);

  if (!isUnknown) {
    for (const rule of PUBLISHER_RULES) {
      if (rule.pattern.test(trimmed)) {
        return {
          id: rule.id,
          name: rule.name,
          iconUrl: rule.iconUrl,
          isFallback: false,
        };
      }
    }
  }

  return {
    id: "default",
    name: isUnknown ? "Research Document" : trimmed,
    iconUrl: DEFAULT_FALLBACK_ICON,
    isFallback: true,
  };
}
