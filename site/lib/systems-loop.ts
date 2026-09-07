export type ViewKey = "flow" | "regime" | "news" | "scanner";

export type ViewCase = {
  key: ViewKey;
  label: string;
  ticker: string;
  reading: string;
  evidence: string;
  vehicle: "Options" | "Common stock" | "Futures";
  structure: string;
  expiry: string;
  strikes: string;
  debit: string;
  maxLoss: string;
  convexity: string;
  size: string;
};

export const SYSTEM_LOOP_CASES: ViewCase[] = [
  {
    key: "flow",
    label: "Dark-pool lead",
    ticker: "NVDA",
    reading: "Off-exchange buy pressure is building while the lit tape is quiet.",
    evidence: "Lead window 4 sessions · score 84",
    vehicle: "Options",
    structure: "Call debit spread",
    expiry: "17 Oct 2026",
    strikes: "180 / 195",
    debit: "4.20",
    maxLoss: "420 / contract",
    convexity: "3.0×",
    size: "1.8% bankroll",
  },
  {
    key: "regime",
    label: "Crash regime",
    ticker: "SPX",
    reading: "CRI is elevated. Forced selling is mechanical.",
    evidence: "CRI 62 · VIX and COR1M both contributing",
    vehicle: "Options",
    structure: "Put debit spread",
    expiry: "16 Oct 2026",
    strikes: "5600 / 5400",
    debit: "18.50",
    maxLoss: "1,850 / contract",
    convexity: "5.0×",
    size: "0.9% bankroll",
  },
  {
    key: "news",
    label: "Held-name news",
    ticker: "WULF",
    reading: "A tagged headline lands on a name already in the book.",
    evidence: "Held flag · catalyst category EARNINGS",
    vehicle: "Common stock",
    structure: "Add to existing long",
    expiry: "Spot",
    strikes: "n/a",
    debit: "Last 8.40",
    maxLoss: "Shares owned",
    convexity: "Linear",
    size: "2.1% bankroll",
  },
  {
    key: "scanner",
    label: "Scanner hit",
    ticker: "SMCI",
    reading: "Theta and vol-cone agree the wings are cheap relative to realized.",
    evidence: "Cone p10 wing · same-session scan",
    vehicle: "Options",
    structure: "Long strangle",
    expiry: "20 Nov 2026",
    strikes: "28 put / 42 call",
    debit: "3.10",
    maxLoss: "310 / contract",
    convexity: "2.4×",
    size: "1.4% bankroll",
  },
];
