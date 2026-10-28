import { HUMANIZER_QUALITATIVE, HUMANIZER_VOCABULARY, HUMANIZER_WEIGHTS } from "./humanizerPolicy";

export type HumanizerFinding = { field: string; category: string; text: string; start: number; end: number; deduction: number };
export type HumanizerAudit = { score: number; findings: HumanizerFinding[]; qualitative: HumanizerFinding[] };
const MAX_FINDINGS = 4096;
const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const vocabulary = new RegExp(`\\b(?:${HUMANIZER_VOCABULARY.map(escape).join("|")})\\b`, "gi");
// These checks supplement the full contextual reviewer. They never rewrite or
// delete facts, and genuine negation ("has not broken resistance") is valid.
const LOCAL_PATTERNS: Array<[string, RegExp]> = [
  ["negative_parallelism", /[.!?\n]\s*(?:(?:it|this|that)(?:['’]s|\s+is)\s+)?not\s+(?![$€£+\-]?\d)(?:(?:a|an|the)\s+)?[A-Za-z][^.!?\n]{0,160}/gi],
  ["negative_parallelism", new RegExp("[,;\u2014]\\s*not\\s+(?!(?:[$\u20ac\u00a3+\\-]?\\d|\\d))(?:(?:a|an|the)\\s+)?[A-Za-z][^.!?\\n]{0,160}", "gi")],
  ["negative_parallelism", new RegExp("\\bnot\\s+(?:only|just|merely)\\b(?:[^.!?\\n]|\\.(?=\\d)){0,240}?(?:\\bbut\\b|[,;]\\s*(?:it|this|that)(?:['\u2019]s|\\s+is)\\b)", "gi")],
  ["negative_parallelism", new RegExp("\\bnot\\s+(?:a|an|the|about)\\b(?:[^.!?\\n]|\\.(?=\\d)){0,240}?\\bbut\\b", "gi")],
  ["negative_parallelism", new RegExp("\\b(?:it|this|that)(?:\\s+is\\s+not|\\s+isn['\u2019]t|['\u2019]s\\s+not)\\b(?:[^.!?\\n]|\\.(?=\\d)){0,240}?[.;]\\s*(?:it|this|that)(?:['\u2019]s|\\s+is)\\b", "gi")],
  ["em_dash_overuse", new RegExp("\u2014|&mdash;|&#0*8212;|&#x0*2014;", "gi")],
  ["curly_quotes", new RegExp("[\u201c\u201d\u2018\u2019\u201e\u201f]", "gi")],
  ["chatbot_artifacts", new RegExp("\\b(?:I hope this helps|let me know|would you like me to|certainly[!,])", "gi")],
  ["cutoff_disclaimer", new RegExp("\\b(?:my (?:knowledge cutoff|last training update)|as of my last|based on my training)\\b", "gi")],
  ["sycophancy", new RegExp("\\b(?:great question|you['\u2019]re absolutely right|excellent question)\\b", "gi")],
  ["filler", new RegExp("\\b(?:in order to|due to the fact that|at this point in time|it is important to note|it is worth noting|worth noting)\\b", "gi")],
  ["generic_positive_conclusion", new RegExp("\\b(?:the future looks bright|exciting times lie ahead|time will tell|buckle up|stay tuned)\\b", "gi")],
];

function checkCopy(copy: Record<string, string>) {
  if (!Object.keys(copy).length || Object.values(copy).some(text => typeof text !== "string" || !text.trim() || text.length > 30_000)) {
    throw new Error("Humanizer requires bounded nonempty copy");
  }
}
function dedupe(findings: HumanizerFinding[]): HumanizerFinding[] {
  if (findings.length > MAX_FINDINGS) throw new Error("Humanizer finding budget exceeded");
  // Process narrow spans first. A broad review span that overlaps two
  // disjoint passages must not join them and erase one deduction.
  const ordered = [...findings].sort((a, b) => a.field.localeCompare(b.field) || a.category.localeCompare(b.category)
    || (a.end - a.start) - (b.end - b.start) || a.start - b.start || a.end - b.end);
  const accepted: HumanizerFinding[] = [];
  for (const item of ordered) {
    const overlaps = accepted.filter(finding => finding.field === item.field && finding.category === item.category
      && item.start < finding.end && finding.start < item.end).sort((a, b) => a.start - b.start || a.end - b.end);
    let components = 0;
    let componentEnd = -1;
    for (const finding of overlaps) {
      if (finding.start >= componentEnd) { components++; componentEnd = finding.end; }
      else componentEnd = Math.max(componentEnd, finding.end);
    }
    if (components > 1) continue;
    accepted.push(item);
  }
  const mergeOrder = accepted.sort((a, b) => a.field.localeCompare(b.field) || a.category.localeCompare(b.category) || a.start - b.start || a.end - b.end);
  const merged: HumanizerFinding[] = [];
  for (const item of mergeOrder) {
    const last = merged.at(-1);
    if (last && last.field === item.field && last.category === item.category && item.start < last.end) {
      if (item.end > last.end) { last.text += item.text.slice(last.end - item.start); last.end = item.end; }
    } else merged.push({ ...item });
  }
  return merged;
}
export function localHumanizerFindings(copy: Record<string, string>): HumanizerFinding[] {
  checkCopy(copy);
  const findings: HumanizerFinding[] = [];
  for (const [field, text] of Object.entries(copy)) {
    for (const [category, pattern] of [["vocabulary", vocabulary] as [string, RegExp], ...LOCAL_PATTERNS]) {
      for (const match of text.matchAll(new RegExp(pattern.source, pattern.flags))) {
        if (findings.length >= MAX_FINDINGS) throw new Error("Humanizer finding budget exceeded");
        findings.push({ field, category, text: match[0], start: match.index!, end: match.index! + match[0].length, deduction: category === "vocabulary" ? 5 : 0 });
      }
    }
  }
  return dedupe(findings);
}
function normalize(copy: Record<string, string>, value: unknown, qualitative: boolean): HumanizerFinding[] {
  if (!Array.isArray(value) || value.length > MAX_FINDINGS) throw new Error("Humanizer findings must be bounded arrays");
  const allowed = qualitative ? HUMANIZER_QUALITATIVE : HUMANIZER_WEIGHTS;
  const findings: HumanizerFinding[] = [];
  for (const item of value) {
    if (!item || typeof item !== "object" || typeof item.field !== "string" || !(item.field in copy)
      || typeof item.category !== "string" || !Object.hasOwn(allowed, item.category)
      || typeof item.text !== "string" || !item.text.trim()) throw new Error("Humanizer finding has invalid category or span");
    const allowedVocabulary = [...HUMANIZER_VOCABULARY, "landscape", "underscore", "highlight",
      "underscores", "underscored", "underscoring", "highlights", "highlighted", "highlighting"];
    if (item.category === "vocabulary" && !allowedVocabulary.some(word => word === item.text.toLowerCase())) {
      throw new Error("Humanizer vocabulary finding is not in the visible rubric");
    }
    const deduction = qualitative ? 0 : HUMANIZER_WEIGHTS[item.category as keyof typeof HUMANIZER_WEIGHTS];
    if ("deduction" in item && item.deduction !== deduction) throw new Error("Humanizer deduction differs from rubric");
    let found = false;
    const pattern = new RegExp(item.category === "vocabulary" ? `\\b${escape(item.text)}\\b` : escape(item.text), "g");
    for (const match of copy[item.field].matchAll(pattern)) {
      if (findings.length >= MAX_FINDINGS) throw new Error("Humanizer finding budget exceeded");
      found = true;
      findings.push({ field: item.field, category: item.category, text: match[0], start: match.index!, end: match.index! + match[0].length, deduction });
    }
    if (!found) throw new Error("Humanizer finding does not quote the actual copy");
  }
  return findings;
}
export function auditHumanizerCopy(copy: Record<string, string>, review: unknown, previous?: HumanizerAudit): HumanizerAudit {
  checkCopy(copy);
  if (!review || typeof review !== "object" || !("findings" in review) || !("qualitative" in review)) throw new Error("Humanizer review is malformed");
  const local = localHumanizerFindings(copy);
  // A reviewer cannot erase an earlier finding while the offending text remains,
  // including if a rewrite moves it between the caption and article fields.
  for (const finding of [...(previous?.findings ?? []), ...(previous?.qualitative ?? [])]) {
    for (const [field, text] of Object.entries(copy)) {
      for (const match of text.matchAll(new RegExp(escape(finding.text), "gi"))) {
        if (local.length >= MAX_FINDINGS) throw new Error("Humanizer finding budget exceeded");
        local.push({ ...finding, field, text: match[0], start: match.index!, end: match.index! + match[0].length });
      }
    }
  }
  const findings = dedupe([...local.filter(f => f.deduction > 0), ...normalize(copy, review.findings, false)]);
  const qualitative = dedupe([...local.filter(f => f.deduction === 0), ...normalize(copy, review.qualitative, true)]);
  return { score: Math.max(0, 100 - findings.reduce((sum, finding) => sum + finding.deduction, 0)), findings, qualitative };
}
export class HumanizerReviewError extends Error {
  constructor(readonly audit: HumanizerAudit) { super("Humanizer review rejected the draft"); }
}
export function assertPassed(audit: HumanizerAudit): HumanizerAudit {
  if (audit.score < 90 || audit.qualitative.length) throw new HumanizerReviewError(audit);
  return audit;
}
export function assertLocalHumanizer(copy: Record<string, string>): HumanizerAudit {
  return assertPassed(auditHumanizerCopy(copy, { findings: [], qualitative: [] }));
}
