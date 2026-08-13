/**
 * Analysis sources — provenance + follow-ups for a newsfeed story.
 *
 * The newsfeed tagging pipeline is genuinely two-stage (text tagger + vision
 * tagger, see scripts/newsfeed/CLAUDE.md), and every post carries the origin
 * article href. That is real provenance, so AnalysisSources renders it rather
 * than inventing citations: the article itself, plus whichever taggers actually
 * contributed to this post.
 *
 * Follow-ups are derived from the post's own tags — ticker-shaped tags become
 * flow prompts, which is the question an operator actually asks next.
 */

import type { AnalysisSource } from "@/components/agent";

type SourcePost = {
  href?: string;
  tags?: string[];
  tags_text?: string[];
  tags_vision?: string[];
};

/** Bare hostname for display, e.g. "themarketear.com". */
function hostnameOf(href: string): string | null {
  try {
    return new URL(href).hostname.replace(/^www\./, "");
  } catch {
    return null;
  }
}

function countLabel(count: number): string {
  return `${count} tag${count === 1 ? "" : "s"}`;
}

/**
 * Provenance rows for one post. Order is most- to least-direct: the article,
 * then the taggers that annotated it. Returns an empty array when the post has
 * no href and no tagger output — AnalysisSources renders nothing rather than an
 * empty SOURCES header.
 */
export function buildAnalysisSources(post: SourcePost): AnalysisSource[] {
  const sources: AnalysisSource[] = [];

  if (post.href) {
    const host = hostnameOf(post.href);
    if (host) {
      sources.push({ id: "article", name: host, feed: "newsfeed", href: post.href });
    }
  }

  const textTags = post.tags_text ?? [];
  if (textTags.length) {
    sources.push({ id: "tagger-text", name: "Text tagger", feed: countLabel(textTags.length) });
  }

  const visionTags = post.tags_vision ?? [];
  if (visionTags.length) {
    sources.push({ id: "tagger-vision", name: "Vision tagger", feed: countLabel(visionTags.length) });
  }

  return sources;
}

/**
 * A tag is treated as an instrument when it looks like a ticker: 1-5 uppercase
 * letters. The taxonomy is UPPERCASE throughout, so a case-sensitive test is
 * what separates "MU" from "FED RATES".
 */
function isInstrumentTag(tag: string): boolean {
  return /^[A-Z]{1,5}$/.test(tag);
}

const MAX_FOLLOW_UPS = 3;

/**
 * Follow-up prompts for the story. Instrument tags become flow questions; the
 * result is capped so the chip row cannot overrun the lightbox.
 */
export function buildFollowUps(post: SourcePost): string[] {
  const tags = post.tags ?? [];
  const seen = new Set<string>();
  const prompts: string[] = [];

  for (const tag of tags) {
    if (!isInstrumentTag(tag) || seen.has(tag)) continue;
    seen.add(tag);
    prompts.push(`Scan ${tag} flow`);
    if (prompts.length >= MAX_FOLLOW_UPS) break;
  }

  return prompts;
}
