import { describe, expect, it } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import ts from "typescript";

const root = join(__dirname, "..");
function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? sourceFiles(path) : /\.tsx?$/.test(path) ? [path] : [];
  });
}

// These are live financial safety or measurement-provenance facts, not failed
// requests. They remain visible after dismissing an operational error toast.
const safetyClasses: Record<string, string[]> = {
  "components/ModifyOrderModal.tsx": ["modify-order-fill-stale"],
  // Dispersion's persisted stale scan timestamp explains why measurements are
  // withheld. The custom-component check below pins its only danger caller.
  "components/SectionEmptyState.tsx": ["section-empty-state"],
  "components/ticker-detail/OrderTab.tsx": ["order-error"],
  "components/ticker-detail/PositionTradeTicket.tsx": ["order-error"],
  "lib/order/components/OrderConfirmSummary.tsx": ["order-confirm-undefined-risk"],
};
const presenters = new Set(["components/ErrorToast.tsx", "components/Toast.tsx"]);

function violations(path: string): string[] {
  const name = relative(root, path);
  if (presenters.has(name)) return [];
  const text = readFileSync(path, "utf8");
  // Most source files contain no UI error surface. Avoid constructing an AST
  // for those files while preserving every attribute/call inspected below.
  const candidate = /\brole\s*=\s*(?:["']alert["']|\{[^}]*["']alert["'])|\bclassName\s*=\s*(?:["'][^"']*error[^"']*["']|\{[^}]*styles\.error)|\btone\s*=|\b(?:window\.)?alert\s*\(/s;
  if (!candidate.test(text)) return [];
  const source = ts.createSourceFile(path, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const found: string[] = [];
  function visit(node: ts.Node) {
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const tag = node.tagName.getText(source);
      if (tag === "SectionEmptyState") {
        const attributes = node.attributes.properties.filter(ts.isJsxAttribute);
        const attribute = (key: string) => attributes.find(item => item.name.getText(source) === key)?.initializer;
        const tone = attribute("tone");
        if (tone && !(ts.isStringLiteral(tone) && tone.text === "default")) {
          const testId = attribute("testId");
          const provenanceOnly = name === "components/DispersionPanel.tsx"
            && ts.isStringLiteral(tone) && tone.text === "danger"
            && testId && ts.isStringLiteral(testId) && testId.text === "dispersion-writer-stale";
          if (!provenanceOnly) found.push(`${name}: inline danger empty state outside approved dispersion provenance`);
        }
      }
      if (/^[a-z]/.test(tag)) {
        const attributes = node.attributes.properties.filter(ts.isJsxAttribute);
        const attribute = (key: string) => attributes.find(item => item.name.getText(source) === key)?.initializer;
        const role = attribute("role")?.getText(source) ?? "";
        const cssNode = attribute("className");
        const css = cssNode && ts.isStringLiteral(cssNode) ? cssNode.text : cssNode?.getText(source) ?? "";
        const inlineAlert = role.includes('"alert"') || role.includes("'alert'");
        const inlineErrorClass = /(?:^|\s)(?:[\w-]+-)?error(?:\s|$)/.test(css) || /\bstyles\.error\b/.test(css);
        const safety = safetyClasses[name]?.some(allowed => css.split(/\s+/).includes(allowed));
        if ((inlineAlert || inlineErrorClass) && !safety) {
          found.push(`${name}:${source.getLineAndCharacterOfPosition(node.getStart()).line + 1}: inline error surface`);
        }
      }
    }
    if (ts.isCallExpression(node) && /^(?:window\.)?alert$/.test(node.expression.getText(source))) {
      found.push(`${name}: blocking browser alert`);
    }
    ts.forEachChild(node, visit);
  }
  visit(source);
  return found;
}

describe("application error presentation contract", () => {
  it("uses shared toast presenters instead of inline alert/error surfaces or browser alerts", () => {
    const paths = ["app", "components", "lib"].flatMap(directory => sourceFiles(join(root, directory)));
    expect(paths.flatMap(violations)).toEqual([]);
  // The contract inventories the entire application on shared CI runners.
  }, 30_000);
});
