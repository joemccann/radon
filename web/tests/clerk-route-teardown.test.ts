import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { runInNewContext } from "node:vm";
import ts from "typescript";
import { expect, it, vi } from "vitest";

// T-240: execute the registered global teardown for each Clerk proxy spec.
// A cleanup at the end of one test does not cover failed assertions or siblings.
const root = join(__dirname, "../e2e");
for (const file of readdirSync(root).filter((name) => name.endsWith(".spec.ts"))) {
  const source = ts.createSourceFile(file, readFileSync(join(root, file), "utf8"), ts.ScriptTarget.Latest, true);
  if (!source.statements.some((node) => ts.isFunctionDeclaration(node) && node.name?.text === "letClerkLoad")) continue;
  it(`${file} disposes proxy routes after every test`, async () => {
    const callbacks = source.statements.flatMap((node) => {
      if (!ts.isExpressionStatement(node) || !ts.isCallExpression(node.expression)) return [];
      const call = node.expression;
      if (!ts.isPropertyAccessExpression(call.expression)
          || call.expression.expression.getText(source) !== "test"
          || call.expression.name.text !== "afterEach") return [];
      return [call.arguments[0]];
    });
    expect(callbacks.length).toBeGreaterThan(0);
    const unrouteAll = vi.fn().mockResolvedValue(undefined);
    for (const callback of callbacks) {
      const js = ts.transpileModule(`(${callback.getText(source)})`, {
        compilerOptions: { target: ts.ScriptTarget.ES2022 },
      }).outputText;
      await runInNewContext(js)({ page: { unrouteAll } });
    }
    expect(unrouteAll).toHaveBeenCalledWith({ behavior: "ignoreErrors" });
  });
}
