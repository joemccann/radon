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

// These are live financial safety facts, not failed requests. They must remain
// visible beside the order while submission is gated, even after dismissing a toast.
const safetyClasses: Record<string, string[]> = {
  "components/ModifyOrderModal.tsx": ["modify-order-fill-stale"],
  "components/ticker-detail/OrderTab.tsx": ["order-error"],
  "components/ticker-detail/PositionTradeTicket.tsx": ["order-error"],
  "lib/order/components/OrderConfirmSummary.tsx": ["order-confirm-undefined-risk"],
};
const presenters = new Set(["components/ErrorToast.tsx", "components/Toast.tsx"]);

function violations(path: string): string[] {
  const name = relative(root, path);
  if (presenters.has(name)) return [];
  const source = ts.createSourceFile(path, readFileSync(path, "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const found: string[] = [];
  function visit(node: ts.Node) {
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const tag = node.tagName.getText(source);
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
  });
});
