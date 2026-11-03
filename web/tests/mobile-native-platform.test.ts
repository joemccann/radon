import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import postcss, { type Rule } from "postcss";
import tailwindcss from "tailwindcss";
import { describe, expect, it } from "vitest";
import config from "../tailwind.config";

const web = resolve(__dirname, "..");

function allowsMouseHover(rule: Rule): boolean {
  let parent = rule.parent;
  while (parent) {
    if (parent.type === "atrule" && parent.name === "media" &&
      parent.params.includes("(hover: hover)") && parent.params.includes("(pointer: fine)")) return true;
    parent = parent.parent;
  }
  return false;
}

describe("mobile native platform", () => {
  it("declares landscape text sizing for browsers that only expose it on a phone", () => {
    const declarations = new Map<string, string>();
    postcss.parse(readFileSync(resolve(web, "app/globals.css"), "utf8")).walkRules("html", (rule) => {
      rule.walkDecls((declaration) => { declarations.set(declaration.prop, declaration.value); });
    });
    expect(declarations.get("-webkit-text-size-adjust")).toBe("100%");
    expect(declarations.get("text-size-adjust")).toBe("100%");
  });

  it("gates authored hover styles by hover and pointer capabilities", () => {
    const violations: string[] = [];
    for (const directory of ["app", "components"]) {
      for (const name of readdirSync(resolve(web, directory), { recursive: true })) {
        if (!String(name).endsWith(".css")) continue;
        const file = resolve(web, directory, String(name));
        postcss.parse(readFileSync(file, "utf8")).walkRules((rule) => {
          if (rule.selector.includes(":hover") && !allowsMouseHover(rule)) {
            violations.push(`${directory}/${name}:${rule.source?.start?.line}`);
          }
        });
      }
    }
    expect(violations, "Touch must not acquire persistent mouse hover styling").toEqual([]);
  });

  it("gates generated Tailwind hover utilities by the same capabilities", async () => {
    const result = await postcss([tailwindcss({
      ...config,
      content: [{ raw: '<button class="hover:bg-red-500">Action</button>' }],
    })]).process("@tailwind utilities;", { from: undefined });
    const hover: Rule[] = [];
    result.root.walkRules((rule) => { if (rule.selector.includes(":hover")) hover.push(rule); });
    expect(hover.length).toBeGreaterThan(0);
    expect(hover.every(allowsMouseHover)).toBe(true);
  });
});
