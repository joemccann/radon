/**
 * Minimal CSS cascade resolver for layout-contract tests (T-458).
 *
 * jsdom's getComputedStyle does not cascade document stylesheets, and pinning
 * raw stylesheet bytes lets a later higher-specificity selector reintroduce a
 * fixed layout regression while the pin stays green. This helper parses
 * `globals.css` ONCE into rules (a single forward pass — the 2026-08-23
 * lesson: a fresh regex per token straddled the 5s timeout), then resolves
 * the WINNING declaration for a property against a RENDERED element:
 * every rule whose selector `element.matches(...)` at the desktop viewport
 * competes on (specificity, source order), exactly the axis a rogue override
 * would exploit.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

export type CssRule = {
  selectors: string[];
  body: string;
  media: string | null;
  order: number;
};

const DESKTOP_VIEWPORT_PX = 1280;

/** State-dependent or pseudo-element selectors never decide static layout. */
const STATE_PSEUDO = /:(hover|focus|active|focus-visible|focus-within|visited)\b|::/;

function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, " ");
}

function splitSelectors(prelude: string): string[] {
  const cleaned = prelude.split(";").pop() ?? "";
  return cleaned
    .split(",")
    .map((selector) => selector.trim())
    .filter(Boolean);
}

function collectRules(css: string, media: string | null, rules: CssRule[]): void {
  let cursor = 0;
  while (cursor < css.length) {
    const brace = css.indexOf("{", cursor);
    if (brace < 0) break;
    const prelude = css.slice(cursor, brace).trim();
    let depth = 1;
    let end = brace + 1;
    while (end < css.length && depth > 0) {
      if (css[end] === "{") depth += 1;
      else if (css[end] === "}") depth -= 1;
      end += 1;
    }
    const body = css.slice(brace + 1, end - 1);
    if (/^@(media|supports)\b/.test(prelude)) {
      collectRules(body, prelude, rules);
    } else if (!prelude.startsWith("@")) {
      rules.push({ selectors: splitSelectors(prelude), body, media, order: rules.length });
    }
    cursor = end;
  }
}

export function parseRules(css: string): CssRule[] {
  const rules: CssRule[] = [];
  collectRules(stripComments(css), null, rules);
  return rules;
}

/** Width features are evaluated at a desktop viewport; print never applies. */
export function mediaApplies(media: string | null): boolean {
  if (!media) return true;
  if (/\bprint\b/.test(media)) return false;
  for (const feature of media.matchAll(/\(\s*(min|max)-width:\s*([\d.]+)px\s*\)/g)) {
    const px = Number(feature[2]);
    if (feature[1] === "max" && DESKTOP_VIEWPORT_PX > px) return false;
    if (feature[1] === "min" && DESKTOP_VIEWPORT_PX < px) return false;
  }
  return true;
}

export function specificity(selector: string): number {
  const ids = (selector.match(/#[\w-]+/g) ?? []).length;
  const classLike = (selector.match(/\.[\w-]+|\[[^\]]*\]|:(?!:)[\w-]+/g) ?? []).length;
  const types = (selector.match(/(?:^|[\s>+~])[a-zA-Z][\w-]*/g) ?? []).length;
  return ids * 10_000 + classLike * 100 + types;
}

export type WinningDeclaration = { property: string; value: string; selector: string };

/**
 * The declaration the cascade hands `element` for the first of `properties`
 * it can resolve, considering every parsed rule that matches the element.
 * Pass shorthand fallbacks in `properties` (e.g. ["column-gap", "gap"]).
 */
export function winningDeclaration(
  element: Element,
  properties: string[],
  rules: CssRule[],
): WinningDeclaration | null {
  let best: (WinningDeclaration & { spec: number; order: number; declAt: number }) | null = null;
  for (const rule of rules) {
    if (!properties.some((property) => rule.body.includes(property))) continue;
    if (!mediaApplies(rule.media)) continue;
    let spec = -1;
    let matchedSelector = "";
    for (const selector of rule.selectors) {
      if (STATE_PSEUDO.test(selector)) continue;
      let matched = false;
      try {
        matched = element.matches(selector);
      } catch {
        matched = false;
      }
      if (matched && specificity(selector) > spec) {
        spec = specificity(selector);
        matchedSelector = selector;
      }
    }
    if (spec < 0) continue;
    for (const property of properties) {
      const declarations = rule.body.matchAll(
        new RegExp(`(?:^|[;{])\\s*(${property})\\s*:\\s*([^;}]+)`, "g"),
      );
      for (const declaration of declarations) {
        const declAt = declaration.index ?? 0;
        const candidate = {
          property: declaration[1],
          value: declaration[2].trim(),
          selector: matchedSelector,
          spec,
          order: rule.order,
          declAt,
        };
        const wins =
          !best ||
          candidate.spec > best.spec ||
          (candidate.spec === best.spec &&
            (candidate.order > best.order ||
              (candidate.order === best.order && candidate.declAt > best.declAt)));
        if (wins) best = candidate;
      }
    }
  }
  return best ? { property: best.property, value: best.value, selector: best.selector } : null;
}

/** `column-gap`, honoring a `gap` shorthand (row [column]) if that wins. */
export function resolvedColumnGap(element: Element, rules: CssRule[]): string | null {
  const winner = winningDeclaration(element, ["column-gap", "gap"], rules);
  if (!winner) return null;
  if (winner.property === "gap") {
    const parts = winner.value.split(/\s+/);
    return parts[1] ?? parts[0];
  }
  return winner.value;
}

export const GLOBALS_CSS_RULES = parseRules(
  readFileSync(join(__dirname, "..", "app", "globals.css"), "utf8"),
);
